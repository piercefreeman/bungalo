import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Queue
from shutil import copyfile
from tempfile import TemporaryDirectory
from traceback import format_exc
from typing import Iterator

from foundation.core import identity
from icloudpd import download, exif_datetime
from icloudpd.paths import clean_filename
from pyicloud_ipd.base import PyiCloudService
from pyicloud_ipd.exceptions import PyiCloudFailedLoginException
from pyicloud_ipd.file_match import FileMatchPolicy
from pyicloud_ipd.item_type import AssetItemType
from pyicloud_ipd.raw_policy import RawTreatmentPolicy
from pyicloud_ipd.services.photos import PhotoAlbum, PhotoAsset
from pyicloud_ipd.version_size import AssetVersionSize
from tzlocal import get_localzone

from bungalo.backups.nas import mount_smb
from bungalo.config import BungaloConfig
from bungalo.config.endpoints import NASEndpoint
from bungalo.constants import DEFAULT_PYICLOUD_COOKIE_PATH
from bungalo.logger import CONSOLE, LOGGER
from bungalo.slack import SlackClient

FOLDER_STRUCTURE = "{:%Y/%m/%d}"


@dataclass
class PhotoContext:
    photo: PhotoAsset
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
        client_id: str | None,
        photo_size: AssetVersionSize,
        output_path: Path,
        slack_client: SlackClient,
        cookie_path: Path,
        concurrency: int = 4,
    ) -> None:
        """
        Initialize the iPhoto sync engine.

        :param username: iCloud account username/email
        :param password: iCloud account password
        :param client_id: Client identifier for iCloud API
        :param photo_size: Size/quality of photos to download (e.g., "original")
        :param output_path: Base directory where photos will be saved
        :param concurrency: Number of concurrent download operations. We've observed
            that higher numbers of workers here will saturate the network connection
            and DNS resolution layer so slack requests time out. 4 seems workable.

        """
        self.username = username
        self.password = password
        self.client_id = client_id
        self.photo_size = photo_size
        self.output_path = output_path
        self.concurrency = concurrency
        self.slack_client = slack_client
        self.cookie_path = cookie_path

        self._completed_photos = 0

    async def sync(self) -> None:
        """
        Synchronize photos from iCloud to the local filesystem.

        Connects to iCloud, fetches photos from the specified album,
        and downloads them with proper organization and metadata.
        Uses asyncio for non-blocking concurrent downloads with a queue system
        to control resource usage.
        """
        CONSOLE.print("Logging in to iCloud...")
        icloud = await self.icloud_login()
        CONSOLE.print("Getting photo metadata from album...")
        photos = icloud.photos.all
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

    async def _populate_queue(
        self, photos: PhotoAlbum, queue: Queue[PhotoContext]
    ) -> None:
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
        self, icloud: PyiCloudService, worker_id: int, queue: Queue[PhotoContext]
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
        core_status = await self.slack_client.create_status(
            f"Starting iPhoto sync of {total_photos} photos..."
        )
        update_status = await self.slack_client.create_status(
            f"Progress: 0 / {total_photos} photos processed",
            parent_ts=core_status,
        )

        processed = 0
        while processed < total_photos:
            # Update progress bar with the new number of completed items
            diff = self._completed_photos - processed
            if diff:
                processed = self._completed_photos
                LOGGER.info(
                    f"Progress: {processed} / {total_photos} photos processed ({(processed / total_photos) * 100:.1f}%)"
                )
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                await self.slack_client.update_status(
                    update_status,
                    f"[{current_time}] Progress: {processed} / {total_photos} photos processed ({(processed / total_photos) * 100:.1f}%)",
                )

            # Wait a bit before checking again so we don't get rate limited
            await asyncio.sleep(30)

        await self.slack_client.update_status(
            update_status,
            f"✅ Completed processing all {total_photos} photos!",
        )

    def iter_photos(self, photos: PhotoAlbum) -> Iterator[PhotoContext]:
        """
        Generate PhotoContext objects for each relevant photo.

        :param photos: Collection of photo objects from iCloud
        :return: Iterator yielding PhotoContext objects for each photo with proper paths

        Skips non-photo/video items and handles timezone conversion.
        """
        for photo in photos:
            filename = clean_filename(photo.filename)

            if photo.item_type not in {AssetItemType.IMAGE, AssetItemType.MOVIE}:
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

    def sync_photo(self, icloud: PyiCloudService, photo_payload: PhotoContext) -> None:
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
            version = photo_payload.photo.versions[self.photo_size]

            start_time = datetime.now()
            download_result = download.download_media(
                logger=LOGGER,
                dry_run=False,
                icloud=icloud,
                photo=photo_payload.photo,
                download_path=str(download_path),
                version=version,
                size=AssetVersionSize.ORIGINAL,
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
        photo: PhotoAsset,
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
            and not exif_datetime.get_photo_exif(logger=LOGGER, path=str(download_path))
        ):
            exif_datetime.set_photo_exif(
                logger=LOGGER,
                path=str(download_path),
                date=created_date.strftime("%Y:%m:%d %H:%M:%S"),
            )
        download.set_utime(str(download_path), created_date)

    async def icloud_login(self) -> PyiCloudService:
        """
        Authenticate with iCloud services.

        :param cookie_directory: Directory to store authentication cookies
        :return: Authenticated iCloud service instance
        :raises Exception: If authentication fails
        """
        LOGGER.debug("Authenticating...")

        icloud = PyiCloudService(
            filename_cleaner=clean_filename,
            lp_filename_generator=identity,
            domain="com",
            raw_policy=RawTreatmentPolicy.AS_IS,
            file_match_policy=FileMatchPolicy.NAME_SIZE_DEDUP_WITH_SUFFIX,
            apple_id=self.username,
            password=self.password,
            cookie_directory=str(self.cookie_path),
            client_id=self.client_id,
        )

        try:
            if icloud.requires_2fa:
                LOGGER.info("Two-factor authentication is required")
                await self.request_2fa(icloud)
            elif icloud.requires_2sa:
                raise Exception(
                    "Two-step authentication is not supported, 2FA with Apple devices is the more modern approach"
                )

            return icloud

        except PyiCloudFailedLoginException as e:
            if "503" in str(e):
                CONSOLE.print(
                    "503 detected, this might be a transient error because of rate limiting."
                )
                CONSOLE.print(
                    "https://github.com/icloud-photos-downloader/icloud_photos_downloader/issues/970"
                )
                CONSOLE.print(
                    "https://github.com/icloud-photos-downloader/icloud_photos_downloader/issues/1078"
                )
                raise Exception("503 detected") from e
            raise e

    async def request_2fa(self, icloud: PyiCloudService) -> None:
        """
        Request and validate two-factor authentication through Slack interaction.
        With the modern 2FA approach, a code is automatically sent to trusted devices.
        We just need to get the code from the user and validate it.

        :param icloud: Authenticated iCloud service instance
        :raises Exception: If 2FA validation fails
        """
        message = (
            "🔐 Two-factor authentication required\n\n"
            "A verification code has been automatically sent to your trusted devices.\n"
            "Please enter the code in this thread."
        )

        root_message = await self.slack_client.create_status(message)

        try:
            async with self.slack_client.listen_for_replies(root_message) as queue:
                while True:
                    verification_code = (await queue.next(timeout=120))["text"].strip()

                    # Validate code format
                    if len(verification_code) != 6 or not verification_code.isdigit():
                        await self.slack_client.create_status(
                            "❌ Invalid code format. Please enter a 6-digit code.",
                            parent_ts=root_message,
                        )
                        continue

                    # Try to validate the code
                    if not icloud.validate_2fa_code(verification_code):
                        await self.slack_client.create_status(
                            "❌ Invalid verification code. Please try again.",
                            parent_ts=root_message,
                        )
                        continue

                    # Code validated successfully
                    await self.slack_client.create_status(
                        "✅ Two-factor authentication completed successfully!",
                        parent_ts=root_message,
                    )
                    return

        except asyncio.TimeoutError:
            await self.slack_client.create_status(
                "⏰ Two-factor authentication timed out waiting for response",
                parent_ts=root_message,
            )
            raise Exception("Two-factor authentication timed out waiting for response")


async def main(config: BungaloConfig) -> None:
    """
    Main entry point for iPhoto backup process.

    :param config: Bungalo configuration containing NAS connection details

    Mounts NAS drive using SMB and performs photo synchronization.
    """
    if not config.iphoto:
        CONSOLE.print("iPhoto backup not configured, skipping")
        return

    slack_client = SlackClient(
        app_token=config.slack.app_token,
        bot_token=config.slack.bot_token,
        channel_id=config.slack.channel,
    )

    endpoint = next(
        (
            e
            for e in config.endpoints.get_all()
            if isinstance(e, NASEndpoint)
            and e.nickname == config.iphoto.output.endpoint_nickname
        ),
        None,
    )
    if not endpoint:
        raise ValueError("No NAS endpoint found in config")

    while True:
        try:
            with mount_smb(
                server=endpoint.ip_address,
                share=config.iphoto.output.drive_name,
                username=endpoint.username,
                password=endpoint.password.get_secret_value(),
                domain=endpoint.domain,
            ) as mount_dir:
                CONSOLE.print(f"SMB share mounted at: {mount_dir}")

                iphoto_sync = iPhotoSync(
                    username=config.iphoto.username,
                    password=config.iphoto.password,
                    client_id=config.iphoto.client_id,
                    photo_size=AssetVersionSize(config.iphoto.photo_size),
                    slack_client=slack_client,
                    cookie_path=Path(DEFAULT_PYICLOUD_COOKIE_PATH).expanduser(),
                    # Re-define the output location relative to the mount point
                    output_path=Path(mount_dir) / config.iphoto.output.path,
                )

                await iphoto_sync.sync()
        except Exception as e:
            CONSOLE.print(f"Error syncing iPhoto library: {e} {format_exc()}")
            await slack_client.create_status(f"Error syncing iPhoto: {e}")

        # Run every 24 hours
        await asyncio.sleep(config.iphoto.interval.total_seconds())
