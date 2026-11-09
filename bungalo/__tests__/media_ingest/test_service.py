from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from bungalo.config.config import MediaIngestConfig, MediaIngestTranscodeConfig
from bungalo.media_ingest.box import (
    Box,
    ScanResult,
    UploadFailed,
    VideoFile,
    Volume,
)
from bungalo.media_ingest.service import MediaIngest
from bungalo.media_ingest.state import StateStore
from bungalo.slack import SlackClient, SlackMessage

STUDIO = "https://studio.example.com"


LINK = f"{STUDIO}/public-upload/abc_DEF-123"


TEMPLATE = 'ffmpeg -i "$INPUT" "$OUTPUT"\n'


def _jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (320, 180), (200, 10, 10)).save(output, format="JPEG")
    return output.getvalue()


class FakeBox:
    def __init__(self, volumes: list[Volume]):
        self.volumes = volumes
        self.uploads: list[tuple[str, str]] = []
        self.upload_errors: dict[str, UploadFailed] = {}
        self.transcodes: list[tuple[str, str, str, str]] = []
        self.transcode_error: UploadFailed | None = None

    async def scan(self, ignored: list[str]) -> ScanResult:
        return ScanResult(self.volumes)

    async def sample_frames(self, path: str):
        return 10.0, [_jpeg()] * 5

    @asynccontextmanager
    async def transcoded(self, source, output_name, template, work_dir):
        self.transcodes.append((source, output_name, template, work_dir))
        if self.transcode_error:
            raise self.transcode_error
        yield f"{work_dir}/job/{output_name}"

    async def upload(self, path: str, link: str) -> None:
        if path in self.upload_errors:
            raise self.upload_errors[path]
        self.uploads.append((path, link))


class FakeSlack:
    def __init__(self) -> None:
        self.posts: list[tuple[str, SlackMessage | None]] = []
        self.status_updates: list[str] = []
        self.files: list[str] = []
        self.replies: list[dict[str, str]] = []
        self.upload_error: Exception | None = None
        self.delete_message = AsyncMock()

    async def create_status(
        self, text: str, parent_ts: SlackMessage | None = None
    ) -> SlackMessage:
        self.posts.append((text, parent_ts))
        return SlackMessage(tid=f"100.{len(self.posts):06}")

    async def update_status(self, status: SlackMessage, text: str) -> None:
        self.status_updates.append(text)

    async def upload_file(self, parent, *, content, filename, title) -> None:
        if self.upload_error:
            raise self.upload_error
        self.files.append(filename)

    async def list_thread_replies(self, parent, *, after_ts):
        return [r for r in self.replies if float(r["ts"]) > float(after_ts)]


def _setup(
    tmp_path: Path,
    box: FakeBox,
    transcode: MediaIngestTranscodeConfig | None = None,
) -> tuple[MediaIngest, FakeSlack]:
    slack = FakeSlack()

    @asynccontextmanager
    async def connect() -> AsyncIterator[Box]:
        yield cast(Box, box)

    ingest = MediaIngest(
        settings=MediaIngestConfig(
            ssh_endpoint="mac",
            studio_url=STUDIO,
            volumes=["CARD"],
            transcode=transcode,
        ),
        slack=cast(SlackClient, slack),
        store=StateStore(tmp_path / "state.json"),
        connect=connect,
    )
    return ingest, slack


def _volume(*names: str) -> Volume:
    return Volume(
        id="uuid:1",
        name="CARD",
        mount_point="/Volumes/CARD",
        files=[VideoFile(name, 1000, 5) for name in names],
    )


@pytest.mark.asyncio
async def test_new_videos_are_announced_once_across_restarts(tmp_path: Path) -> None:
    box = FakeBox([_volume("b.mp4", "a.mp4")])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    await ingest.scan_once()
    assert len(slack.posts) == 1
    assert slack.files == ["contact-sheet-1.jpg"]
    assert [v.relative_path for v in ingest.state.batches[0].videos] == [
        "a.mp4",
        "b.mp4",
    ]

    box.volumes = [_volume("a.mp4", "b.mp4", "c.mp4")]
    restarted, restarted_slack = _setup(tmp_path, box)
    await restarted.scan_once()
    assert [v.relative_path for v in restarted.state.batches[-1].videos] == ["c.mp4"]
    assert len(restarted_slack.posts) == 1


@pytest.mark.asyncio
async def test_failed_sheet_is_withdrawn_and_retried(tmp_path: Path) -> None:
    ingest, slack = _setup(tmp_path, FakeBox([_volume("a.mp4")]))
    slack.upload_error = RuntimeError("missing_scope")
    with pytest.raises(RuntimeError):
        await ingest.scan_once()
    slack.delete_message.assert_awaited_once()
    assert ingest.state.seen == {} and ingest.state.batches == []

    slack.upload_error = None
    await ingest.scan_once()
    assert len(ingest.state.batches) == 1


@pytest.mark.asyncio
async def test_a_different_disk_at_the_same_name_is_not_uploaded(
    tmp_path: Path,
) -> None:
    box = FakeBox([_volume("a.mp4")])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()

    other = _volume("a.mp4")
    other.id = "uuid:2"
    box.volumes = [other]
    slack.replies = [{"ts": "200.000001", "text": f"1 <{LINK}>"}]
    await ingest.check_comments_once()
    assert box.uploads == []
    assert "isn't mounted" in slack.status_updates[-1]


@pytest.mark.asyncio
async def test_changed_file_is_skipped_and_reported(tmp_path: Path) -> None:
    box = FakeBox([_volume("a.mp4", "b.mp4")])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()

    box.volumes = [
        Volume(
            id="uuid:1",
            name="CARD",
            mount_point="/Volumes/CARD",
            files=[VideoFile("a.mp4", 9999, 6), VideoFile("b.mp4", 1000, 5)],
        )
    ]
    slack.replies = [{"ts": "200.000001", "text": f"all <{LINK}>"}]
    await ingest.check_comments_once()
    assert box.uploads == [("/Volumes/CARD/b.mp4", LINK)]
    assert "#1: changed or removed" in slack.status_updates[-1]


@pytest.mark.asyncio
async def test_closed_link_stops_the_remaining_uploads(tmp_path: Path) -> None:
    box = FakeBox([_volume("a.mp4", "b.mp4")])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    box.upload_errors["/Volumes/CARD/a.mp4"] = UploadFailed("Closed", link_closed=True)
    slack.replies = [{"ts": "200.000001", "text": f"all <{LINK}>"}]
    await ingest.check_comments_once()
    assert box.uploads == []
    assert "Uploaded 0 of 2" in slack.status_updates[-1]


@pytest.mark.asyncio
async def test_a_malformed_link_does_not_block_later_comments(tmp_path: Path) -> None:
    box = FakeBox([_volume("a.mp4")])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    slack.replies = [
        {"ts": "200.000001", "text": "1 https://[bad/public-upload/SECRET"},
        {"ts": "200.000002", "text": f"1 <{LINK}>"},
    ]
    await ingest.check_comments_once()
    assert box.uploads == [("/Volumes/CARD/a.mp4", LINK)]
    assert not any("SECRET" in text for text, _ in slack.posts)


@pytest.mark.asyncio
async def test_expiry_reads_final_comments_then_reoffers_unsent(
    tmp_path: Path,
) -> None:
    box = FakeBox([_volume("a.mp4", "b.mp4")])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    ingest.state.batches[0].created_at -= timedelta(days=15)
    slack.replies = [{"ts": "200.000001", "text": f"1 <{LINK}>"}]

    await ingest.check_comments_once()

    assert box.uploads == [("/Volumes/CARD/a.mp4", LINK)]
    assert ingest.state.batches == []
    assert "has closed" in slack.posts[-1][0]

    await ingest.scan_once()
    assert [v.relative_path for v in ingest.state.batches[0].videos] == ["b.mp4"]


@pytest.mark.asyncio
async def test_upload_follows_a_disk_to_its_conflict_renamed_mount(
    tmp_path: Path,
) -> None:
    """
    Regression: macOS mounts a disk at "CARD 1" when another "CARD" is present.
    The same disk must not be announced again, and uploads use its current path.
    """
    box = FakeBox([_volume("a.mp4")])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()

    box.volumes[0].mount_point = "/Volumes/CARD 1"
    await ingest.scan_once()
    assert len(ingest.state.batches) == 1

    slack.replies = [{"ts": "200.000001", "text": f"1 <{LINK}>"}]
    await ingest.check_comments_once()
    assert box.uploads == [("/Volumes/CARD 1/a.mp4", LINK)]


def _transcode_config(tmp_path: Path) -> MediaIngestTranscodeConfig:
    template = tmp_path / "t.sh"
    template.write_text(TEMPLATE)
    return MediaIngestTranscodeConfig(
        template=str(template), output_suffix="_HQ.mp4", work_dir="/w"
    )


@pytest.mark.asyncio
async def test_selected_videos_upload_their_transcoded_output(tmp_path: Path) -> None:
    box = FakeBox([_volume("DCIM/C0001.MOV", "DCIM/C0002.MOV")])
    ingest, slack = _setup(tmp_path, box, _transcode_config(tmp_path))
    await ingest.scan_once()
    slack.replies = [{"ts": "200.000001", "text": f"2 <{LINK}>"}]
    await ingest.check_comments_once()

    assert box.transcodes == [
        ("/Volumes/CARD/DCIM/C0002.MOV", "C0002_HQ.mp4", TEMPLATE, "/w")
    ]
    assert box.uploads == [("/w/job/C0002_HQ.mp4", LINK)]
    assert any(text.startswith("Transcoding 1 of 1") for text in slack.status_updates)
    assert slack.status_updates[-1] == "Uploaded 1 of 1 to Studio."


@pytest.mark.asyncio
async def test_failed_transcode_is_reported_and_not_marked_uploaded(
    tmp_path: Path,
) -> None:
    box = FakeBox([_volume("a.mov")])
    ingest, slack = _setup(tmp_path, box, _transcode_config(tmp_path))
    await ingest.scan_once()
    box.transcode_error = UploadFailed("Transcode failed: No such encoder")
    slack.replies = [{"ts": "200.000001", "text": f"1 <{LINK}>"}]
    await ingest.check_comments_once()

    assert box.uploads == []
    assert "#1: Transcode failed: No such encoder" in slack.status_updates[-1]
    assert ingest.state.batches[0].videos[0].uploaded_to == []


@pytest.mark.asyncio
async def test_a_bad_template_is_explained_and_uploads_nothing(tmp_path: Path) -> None:
    config = _transcode_config(tmp_path)
    Path(config.template).write_text("ffmpeg -i a.mov b.mp4")
    box = FakeBox([_volume("a.mov")])
    ingest, slack = _setup(tmp_path, box, config)
    await ingest.scan_once()
    slack.replies = [{"ts": "200.000001", "text": f"1 <{LINK}>"}]
    await ingest.check_comments_once()

    assert box.uploads == [] and box.transcodes == []
    assert "doesn't use $INPUT" in slack.status_updates[-1]


@pytest.mark.asyncio
async def test_comment_uploads_selection_once_and_stores_no_link(
    tmp_path: Path,
) -> None:
    box = FakeBox([_volume("a.mp4", "b.mp4")])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    slack.replies = [{"ts": "200.000001", "text": f"2 <{LINK}>"}]

    await ingest.check_comments_once()
    await ingest.check_comments_once()

    assert box.uploads == [("/Volumes/CARD/b.mp4", LINK)]
    assert slack.status_updates[-1] == "Uploaded 1 of 1 to Studio."
    assert "abc_DEF-123" not in (tmp_path / "state.json").read_text()

    slack.replies.append({"ts": "200.000002", "text": f"2 <{LINK}>"})
    await ingest.check_comments_once()
    assert len(box.uploads) == 1
    assert "already been uploaded" in slack.posts[-1][0]


@pytest.mark.asyncio
async def test_comment_without_link_gets_guidance_and_no_upload(
    tmp_path: Path,
) -> None:
    box = FakeBox([_volume("a.mp4")])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    slack.replies = [{"ts": "200.000001", "text": "upload 1"}]
    await ingest.check_comments_once()
    assert box.uploads == []
    assert "Studio public upload link" in slack.posts[-1][0]
