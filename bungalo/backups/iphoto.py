import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Queue
from shutil import copyfile
from tempfile import TemporaryDirectory
from typing import Any, Iterator

from icloudpd import download, exif_datetime
from icloudpd.authentication import TwoStepAuthRequiredError, authenticator
from icloudpd.paths import clean_filename
from tzlocal import get_localzone

from bungalo.backups.nas import mount_smb
from bungalo.config import BungaloConfig
from bungalo.io import progress_bar
from bungalo.logger import CONSOLE, LOGGER

FOLDER_STRUCTURE = "{:%Y/%m/%d}"


@dataclass
class PhotoContext:
    photo: Any
    created_date: datetime
    output_path: Path


class iPhotoSync:
    """
    Syncer from remote iCloud photo library to local directory.

    Downloads photos from a specified iCloud album with proper date-based
    organization, EXIF metadata preservation, and concurrent operations.
    """

    def __init__(
        self,
        username: str,
        password: str,
        client_id: str,
        album_name: str,
        photo_size: str,
        output_path: Path,
        concurrency: int = 10,
    ) -> None:
        """
        Initialize the iPhoto sync engine.

        :param username: iCloud account username/email
        :param password: iCloud account password
        :param client_id: Client identifier for iCloud API
        :param album_name: Name of the album to sync
        :param photo_size: Size/quality of photos to download (e.g., "original")
        :param output_path: Base directory where photos will be saved
        :param concurrency: Number of concurrent download operations
        """
        self.username = username
        self.password = password
        self.client_id = client_id
        self.album_name = album_name
        self.photo_size = photo_size
        self.output_path = output_path
        self.concurrency = concurrency

        self._completed_photos = 0

    async def sync(self) -> None:
        """
        Synchronize photos from iCloud to the local filesystem.

        Connects to iCloud, fetches photos from the specified album,
        and downloads them with proper organization and metadata.
        Uses asyncio for non-blocking concurrent downloads with a queue system
        to control resource usage.
        """
        icloud = await self.icloud_login_async()
        CONSOLE.print("Getting photo metadata from album...")
        photos = icloud.photos.albums[self.album_name]
        CONSOLE.print(f"Found {len(photos)} photos, starting to process metadata...")

        # Create a task to populate the queue in background
        queue: Queue[PhotoContext] = Queue()
        producer_task = asyncio.create_task(self._populate_queue(photos, queue))

        # Create worker tasks to process photos
        workers = [
            asyncio.create_task(self._worker(icloud, i, queue))
            for i in range(self.concurrency)
        ]

        # Setup progress tracking
        progress_task = asyncio.create_task(self._track_progress(len(photos)))

        # Wait for all tasks to complete
        await asyncio.gather(producer_task, *workers, progress_task)

        CONSOLE.print("Photo sync completed successfully")

    async def _populate_queue(self, photos: Any, queue: Queue[PhotoContext]) -> None:
        """
        Populate the work queue with photo contexts.

        :param photos: Collection of photo objects from iCloud
        """

        def _inner():
            count = 0
            for photo in self.iter_photos(photos):
                queue.put(photo)
                count += 1
            CONSOLE.print(f"Added {count} photos to processing queue")

        # Run iter_photos in a separate thread to avoid blocking
        await asyncio.to_thread(_inner)

    async def _worker(
        self, icloud: Any, worker_id: int, queue: Queue[PhotoContext]
    ) -> None:
        """
        Worker task that processes photos from the queue.

        :param icloud: Authenticated iCloud service instance
        :param worker_id: Unique ID for this worker
        """

        def _inner():
            while True:
                # Get a photo to process from the queue
                photo_context = queue.get()
                if photo_context is None:
                    break

                try:
                    # Process the photo
                    self.sync_photo(icloud, photo_context)
                except Exception as e:
                    LOGGER.error(
                        f"Worker {worker_id} error processing {photo_context.output_path.name}: {str(e)}"
                    )
                finally:
                    # Mark the task as done
                    queue.task_done()
                    self._completed_photos += 1

        # Run iter_photos in a separate thread to avoid blocking
        await asyncio.to_thread(_inner)

    async def _track_progress(self, total_photos: int) -> None:
        """
        Track and display progress of the photo sync operation.

        :param total_photos: Total number of photos to be processed
        """
        processed = 0

        async with progress_bar(total=total_photos, description="Syncing photos") as (
            pb,
            task,
        ):
            # Update progress bar with the new number of completed items
            diff = self._completed_photos - processed
            if diff:
                pb.update(task, advance=diff)
                processed = self._completed_photos

            # Wait a bit before checking again
            await asyncio.sleep(0.5)

    def iter_photos(self, photos: Any) -> Iterator[PhotoContext]:
        """
        Generate PhotoContext objects for each relevant photo.

        :param photos: Collection of photo objects from iCloud
        :return: Iterator yielding PhotoContext objects for each photo with proper paths

        Skips non-photo/video items and handles timezone conversion.
        """
        for photo in photos:
            filename = clean_filename(photo.filename)

            if photo.item_type not in {"image", "movie"}:
                CONSOLE.print(
                    f"Skipping {filename}, only downloading photos and videos. "
                    f"(Item type was: {photo.item_type})"
                )
                continue

            try:
                created_date = photo.created.astimezone(get_localzone())
            except (ValueError, OSError):
                CONSOLE.print(
                    f"Could not convert photo created date to local timezone ({photo.created})"
                )
                created_date = photo.created

            # The remote path should be prefixed with the date
            date_path = FOLDER_STRUCTURE.format(created_date)

            yield PhotoContext(
                photo=photo,
                created_date=created_date,
                output_path=self.output_path / date_path / filename,
            )

    def sync_photo(self, icloud: Any, photo_payload: PhotoContext) -> None:
        """
        Download and save a single photo to its destination.

        :param icloud: Authenticated iCloud service instance
        :param photo_payload: Photo metadata and destination information

        Skips photos that already exist at the destination path.
        Downloads to a temporary location before copying to final destination.
        """
        # Check if this photo has already been added to the remote
        if photo_payload.output_path.exists():
            return

        # Create parent directory if it doesn't exist
        photo_payload.output_path.parent.mkdir(parents=True, exist_ok=True)

        with TemporaryDirectory() as download_root:
            download_path = Path(download_root) / "content"

            start_time = datetime.now()
            # Download media directly without async wrapping
            download_result = download.download_media(
                icloud,
                photo_payload.photo,
                download_path,
                self.photo_size,
            )
            LOGGER.debug("Download Duration (ms)", datetime.now() - start_time)

            # Process EXIF data directly
            self.inject_exif(
                photo_payload.photo,
                download_result,
                download_path,
                photo_payload.created_date,
            )

            # Copy the file directly
            copyfile(download_path, photo_payload.output_path)

    def inject_exif(
        self,
        photo: Any,
        download_result: bool,
        download_path: Path,
        created_date: datetime,
        set_exif_datetime: bool = True,
    ) -> None:
        """
        Add EXIF metadata to downloaded photos.

        :param photo: Photo object from iCloud
        :param download_result: Whether download was successful
        :param download_path: Path to the downloaded file
        :param created_date: Original creation date to set
        :param set_exif_datetime: Whether to set the EXIF datetime

        Sets creation date in EXIF metadata for JPEGs and
        updates file modification time to match original.
        """
        if not download_result:
            return

        if (
            set_exif_datetime
            and (clean_filename(photo.filename).lower().endswith((".jpg", ".jpeg")))
            and not exif_datetime.get_photo_exif(download_path)
        ):
            exif_datetime.set_photo_exif(
                download_path,
                created_date.strftime("%Y:%m:%d %H:%M:%S"),
            )
        download.set_utime(download_path, created_date)

    def icloud_login(self, cookie_directory: str = "~/.pyicloud") -> Any:
        """
        Authenticate with iCloud services.

        :param cookie_directory: Directory to store authentication cookies
        :return: Authenticated iCloud service instance
        :raises Exception: If two-factor authentication is required
        """
        try:
            return authenticator("com")(
                self.username,
                self.password,
                cookie_directory,
                raise_error_on_2sa=False,
                client_id=self.client_id,
            )
        except TwoStepAuthRequiredError:
            raise Exception("Two-step authentication required")

    async def icloud_login_async(self, cookie_directory: str = "~/.pyicloud") -> Any:
        """
        Authenticate with iCloud services asynchronously.

        :param cookie_directory: Directory to store authentication cookies
        :return: Authenticated iCloud service instance
        :raises Exception: If two-factor authentication is required
        """
        # Run authentication in a thread to avoid blocking
        return await asyncio.to_thread(self.icloud_login, cookie_directory)


async def main(config: BungaloConfig) -> None:
    """
    Main entry point for iPhoto backup process.

    :param config: Bungalo configuration containing NAS connection details

    Mounts NAS drive using SMB and performs photo synchronization.
    """
    with mount_smb(
        server=config.nas.ip_address,
        share=config.nas.drive_name,
        username=config.nas.username,
        password=config.nas.password,
        domain=config.nas.domain,
    ) as mount_dir:
        CONSOLE.print(f"SMB share mounted at: {mount_dir}")

        iphoto_sync = iPhotoSync(
            username=config.iphoto.username,
            password=config.iphoto.password,
            client_id=config.iphoto.client_id,
            album_name=config.iphoto.album_name,
            photo_size=config.iphoto.photo_size,
            output_path=Path(mount_dir) / config.iphoto.output_directory,
        )

        await iphoto_sync.sync()
