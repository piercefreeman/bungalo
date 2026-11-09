"""
Announces new footage on a remote Mac's volumes in Slack, and uploads whatever
people pick by commenting on the contact sheet. Comments are polled rather than
streamed so none are lost to a restart or a long upload.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import AsyncContextManager

from bungalo.app_manager import AppManager
from bungalo.config import BungaloConfig
from bungalo.config.config import MediaIngestConfig
from bungalo.constants import DEFAULT_MEDIA_INGEST_STATE_FILE
from bungalo.logger import CONSOLE, LOGGER
from bungalo.media_ingest.box import (
    Box,
    ScanResult,
    UploadFailed,
    VideoFile,
    Volume,
    connect_box,
    read_transcode_template,
)
from bungalo.media_ingest.comments import CommentError, UploadRequest, parse_comment
from bungalo.media_ingest.contact_sheet import (
    SheetRow,
    paginate,
    render_contact_sheet,
)
from bungalo.media_ingest.state import (
    Batch,
    BatchVideo,
    IngestState,
    StateStore,
    hash_link,
)
from bungalo.slack import SlackClient, SlackMessage

SERVICE_NAME = "media_ingest"
BATCH_LIFETIME = timedelta(days=14)
CONCURRENT_VIDEOS = 4


def format_size(size: float) -> str:
    """A byte count in the largest unit that keeps it readable."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def format_duration(duration: float | None) -> str:
    """Seconds as m:ss or h:mm:ss, for labelling a video."""
    if duration is None:
        return "unknown length"
    minutes, seconds = divmod(round(duration), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


class MediaIngest:
    def __init__(
        self,
        *,
        settings: MediaIngestConfig,
        slack: SlackClient,
        store: StateStore,
        connect: Callable[[], AsyncContextManager[Box]],
    ):
        self.settings = settings
        self.slack = slack
        self.store = store
        self.connect = connect
        self.state: IngestState = store.load()

    async def scan_once(self) -> ScanResult:
        """Connect to the box and post a contact sheet for each volume's new videos."""
        async with self.connect() as box:
            scan = await box.scan(self.settings.volumes)
            for volume in scan.volumes:
                seen = set(self.state.seen.get(volume.id, []))
                new = sorted(
                    (video for video in volume.files if video.fingerprint not in seen),
                    key=lambda video: video.relative_path,
                )
                if new:
                    await self._announce(box, volume, new)
        return scan

    async def check_comments_once(self) -> None:
        """Act on comments made since the last check, then close expired threads."""
        cutoff = datetime.now(timezone.utc) - BATCH_LIFETIME
        for batch in list(self.state.batches):
            parent = SlackMessage(tid=batch.thread_ts)
            replies = await self.slack.list_thread_replies(
                parent, after_ts=batch.last_reply_ts
            )
            for reply in replies:
                await self._handle_comment(batch, parent, reply.get("text", ""))
                # Only after handling: a crash replays the comment, and files
                # confirmed at this link are skipped.
                batch.last_reply_ts = reply["ts"]
                self.store.save(self.state)
            if batch.created_at < cutoff:
                await self._close(batch, parent)

    async def _close(self, batch: Batch, parent: SlackMessage) -> None:
        """Stop reading a thread, and offer its unsent videos again on a later scan."""
        unsent = {v.fingerprint for v in batch.videos if not v.uploaded_to}
        self.state.seen[batch.volume_id] = [
            fingerprint
            for fingerprint in self.state.seen.get(batch.volume_id, [])
            if fingerprint not in unsent
        ]
        self.state.batches.remove(batch)
        self.store.save(self.state)
        await self.slack.create_status(
            f"This contact sheet has closed after {BATCH_LIFETIME.days} days. "
            f"Videos that weren't uploaded will be offered again in a new post "
            f"the next time `{batch.volume_name}` is scanned.",
            parent_ts=parent,
        )

    async def _announce(
        self, box: Box, volume: Volume, videos: list[VideoFile]
    ) -> None:
        rows = await self._sheet_rows(box, volume, videos)
        sheets = await asyncio.to_thread(
            lambda: [render_contact_sheet(page) for page in paginate(rows)]
        )

        count = f"{len(videos)} new video{'' if len(videos) == 1 else 's'}"
        parent = await self.slack.create_status(
            f"*{count} on `{volume.name}`*\n"
            "Contact sheets are in this thread. To upload, reply with the "
            "numbers you want and a Studio public upload link:\n"
            f"`1 3 5-8 {self.settings.studio_url}/public-upload/KEY` "
            "or `all` with the link."
        )
        if parent.command_errored:
            raise RuntimeError("Could not post the contact sheet message to Slack")

        try:
            for index, sheet in enumerate(sheets, start=1):
                await self.slack.upload_file(
                    parent,
                    content=sheet,
                    filename=f"contact-sheet-{index}.jpg",
                    title=f"{volume.name} ({index} of {len(sheets)})",
                )
        except Exception:
            # This volume is retried next poll, so don't leave a dead post behind.
            await self.slack.delete_message(parent)
            raise

        self.state.batches.append(
            Batch(
                thread_ts=parent.tid,
                volume_id=volume.id,
                volume_name=volume.name,
                created_at=datetime.now(timezone.utc),
                last_reply_ts=parent.tid,
                videos=[
                    BatchVideo(
                        number=number,
                        relative_path=video.relative_path,
                        size=video.size,
                        fingerprint=video.fingerprint,
                    )
                    for number, video in enumerate(videos, start=1)
                ],
            )
        )
        seen = self.state.seen.setdefault(volume.id, [])
        seen.extend(video.fingerprint for video in videos)
        self.store.save(self.state)

    async def _sheet_rows(
        self, box: Box, volume: Volume, videos: list[VideoFile]
    ) -> list[SheetRow]:
        limit = asyncio.Semaphore(CONCURRENT_VIDEOS)

        async def row(number: int, video: VideoFile) -> SheetRow:
            async with limit:
                duration, frames = await box.sample_frames(volume.path_of(video))
            return SheetRow(
                number=number,
                name=video.relative_path,
                detail=f"{format_size(video.size)}  {format_duration(duration)}",
                frames=frames,
            )

        return list(
            await asyncio.gather(
                *(row(number, video) for number, video in enumerate(videos, start=1))
            )
        )

    async def _handle_comment(
        self, batch: Batch, parent: SlackMessage, text: str
    ) -> None:
        try:
            request = parse_comment(
                text,
                studio_url=self.settings.studio_url,
                video_count=len(batch.videos),
            )
        except CommentError as error:
            await self.slack.create_status(str(error), parent_ts=parent)
            return
        if request is None:
            return

        link_hash = hash_link(request.link)
        selected = [v for v in batch.videos if v.number in request.numbers]
        pending = [v for v in selected if link_hash not in v.uploaded_to]
        if not pending:
            await self.slack.create_status(
                "Those videos have already been uploaded to that link.",
                parent_ts=parent,
            )
            return

        status = await self.slack.create_status(
            f"Uploading {len(pending)} video{'' if len(pending) == 1 else 's'}...",
            parent_ts=parent,
        )
        try:
            summary = await self._upload(batch, request, pending, link_hash, status)
        except Exception as error:
            LOGGER.error("Upload request failed: %s", type(error).__name__)
            summary = (
                f"Upload stopped because the box couldn't be reached "
                f"({type(error).__name__}). Comment again to retry; videos "
                "confirmed by Studio won't be sent again."
            )
        await self.slack.update_status(status, summary)

    async def _upload(
        self,
        batch: Batch,
        request: UploadRequest,
        pending: list[BatchVideo],
        link_hash: str,
        status: SlackMessage,
    ) -> str:
        async with self.connect() as box:
            scan = await box.scan(self.settings.volumes)
            volume = next((v for v in scan.volumes if v.id == batch.volume_id), None)
            if volume is None:
                return (
                    f"`{batch.volume_name}` isn't mounted on the box right now. "
                    "Comment again once it's back."
                )
            current = {video.relative_path: video for video in volume.files}
            transcode = self.settings.transcode
            template: str | None = None
            if transcode:
                try:
                    template = read_transcode_template(transcode.template)
                except (OSError, ValueError) as error:
                    return f"Nothing uploaded: the transcode template failed: {error}"

            uploaded = 0
            failures: list[str] = []
            for index, video in enumerate(pending, start=1):
                progress = (
                    f"{index} of {len(pending)}: "
                    f"#{video.number} ({format_size(video.size)})"
                )
                action = "Transcoding" if transcode else "Uploading"
                await self.slack.update_status(status, f"{action} {progress}")
                file = current.get(video.relative_path)
                if file is None or file.fingerprint != video.fingerprint:
                    failures.append(f"#{video.number}: changed or removed on the disk")
                    continue
                try:
                    if transcode and template:
                        output_name = (
                            PurePosixPath(file.name).stem + transcode.output_suffix
                        )
                        async with box.transcoded(
                            volume.path_of(file),
                            output_name,
                            template,
                            transcode.work_dir,
                        ) as output:
                            await self.slack.update_status(
                                status, f"Uploading {progress}"
                            )
                            await box.upload(output, request.link)
                    else:
                        await box.upload(volume.path_of(file), request.link)
                except UploadFailed as error:
                    failures.append(f"#{video.number}: {error.reason}")
                    if error.link_closed:
                        break
                    continue
                video.uploaded_to.append(link_hash)
                self.store.save(self.state)
                uploaded += 1

        lines = [f"Uploaded {uploaded} of {len(pending)} to Studio."]
        if failures:
            lines += ["Not uploaded:", *(f"- {failure}" for failure in failures)]
        return "\n".join(lines)


async def _comment_loop(ingest: MediaIngest, interval: float) -> None:
    while True:
        try:
            await ingest.check_comments_once()
        except Exception as error:
            LOGGER.error("Checking Slack comments failed: %s", error)
        await asyncio.sleep(interval)


async def _scan_loop(ingest: MediaIngest) -> None:
    app_manager = AppManager.get()
    interval = ingest.settings.interval
    last_problem: str | None = None

    while True:
        started = datetime.now(timezone.utc)
        await app_manager.update_service(
            SERVICE_NAME,
            state="running",
            detail="Checking the box for new footage",
            last_run_at=started,
        )
        problem: str | None = None
        volume_count = 0
        try:
            scan = await ingest.scan_once()
            volume_count = len(scan.volumes)
            if scan.warnings:
                problem = "Some volumes couldn't be read: " + "; ".join(scan.warnings)
        except Exception as error:
            problem = f"Media ingest check failed: {error or type(error).__name__}"

        if problem:
            CONSOLE.print(problem)
        # Once per distinct problem: a sleeping box shouldn't post every interval.
        if problem and problem != last_problem:
            await ingest.slack.create_status(problem)
        last_problem = problem

        finished = datetime.now(timezone.utc)
        await app_manager.update_service(
            SERVICE_NAME,
            state="error" if problem else "idle",
            detail=problem or f"Watching {volume_count} mounted volume(s)",
            last_run_at=finished,
            next_run_at=finished + interval,
        )
        await asyncio.sleep(interval.total_seconds())


async def run_media_ingest(config: BungaloConfig) -> None:
    """Watch the configured box and act on Slack comments until cancelled."""
    settings = config.media_ingest
    if settings is None:
        raise ValueError("Add a [media_ingest] section to use the media ingest")

    endpoint = next(
        e for e in config.endpoints.sftp if e.nickname == settings.ssh_endpoint
    )
    ingest = MediaIngest(
        settings=settings,
        slack=SlackClient(
            app_token=config.slack.app_token,
            bot_token=config.slack.bot_token,
            channel_id=config.slack.channel,
        ),
        store=StateStore(Path(DEFAULT_MEDIA_INGEST_STATE_FILE).expanduser()),
        connect=lambda: connect_box(endpoint),
    )
    await asyncio.gather(
        _scan_loop(ingest),
        _comment_loop(ingest, settings.reply_interval.total_seconds()),
    )
