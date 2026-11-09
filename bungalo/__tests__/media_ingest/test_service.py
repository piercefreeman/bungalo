import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from bungalo.config.config import MediaIngestConfig, MediaIngestTranscodeConfig
from bungalo.media_ingest.box import (
    Box,
    BoxCommandError,
    ScanResult,
    StudioUpload,
    UploadFailed,
    VideoFile,
    Volume,
)
from bungalo.media_ingest.service import MediaIngest
from bungalo.media_ingest.state import StateStore, UploadSessionID, hash_link
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
        self.resumed: list[UploadSessionID | None] = []
        self.deleted: list[str] = []
        self.delete_errors: dict[str, BoxCommandError] = {}
        self.hold: dict[str, asyncio.Event] = {}
        self.reading = 0
        self.most_reading = 0

    async def _read(self, path: str) -> None:
        self.reading += 1
        self.most_reading = max(self.most_reading, self.reading)
        await asyncio.sleep(0)
        if path in self.hold:
            await self.hold[path].wait()
        self.reading -= 1

    async def scan(self, ignored: list[str]) -> ScanResult:
        return ScanResult(self.volumes)

    async def sample_frames(self, path: str):
        return 10.0, [_jpeg()] * 5

    @asynccontextmanager
    async def transcoded(self, source, output_name, template, work_dir, gate):
        async with gate:
            self.transcodes.append((source, output_name, template, work_dir))
            if self.transcode_error:
                raise self.transcode_error
            await self._read(source)
        yield f"{work_dir}/job/{output_name}"

    async def upload(self, path: str, link: str) -> None:
        if path in self.upload_errors:
            raise self.upload_errors[path]
        await self._read(path)
        self.uploads.append((path, link))

    async def delete(self, volume: Volume, video: VideoFile) -> None:
        path = volume.path_of(video)
        if path in self.delete_errors:
            raise self.delete_errors[path]
        self.deleted.append(path)

    async def open_upload(
        self, path: str, link: str, resume_id: UploadSessionID | None = None
    ) -> StudioUpload:
        self.resumed.append(resume_id)
        return StudioUpload(
            upload_id=resume_id or UploadSessionID("fresh-id"),
            size_bytes=1000,
            part_bytes=32,
            missing=[1],
        )

    async def send_upload(self, path: str, link: str, upload: StudioUpload) -> None:
        await self.upload(path, link)


class FakeSlack:
    def __init__(self) -> None:
        self.posts: list[tuple[str, SlackMessage | None]] = []
        self.status_updates: list[str] = []
        self.files: list[str] = []
        self.replies: list[dict[str, str]] = []
        self.thread_replies: dict[str, list[dict[str, str]]] = {}
        self.reactions: list[tuple[str, str]] = []
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
        replies = self.thread_replies.get(parent.tid, self.replies)
        return [r for r in replies if float(r["ts"]) > float(after_ts)]

    async def add_reaction(self, message: SlackMessage, emoji: str) -> None:
        self.reactions.append((message.tid, emoji))


def _setup(
    tmp_path: Path,
    box: FakeBox,
    transcode: MediaIngestTranscodeConfig | None = None,
    volumes: list[str] | None = None,
) -> tuple[MediaIngest, FakeSlack]:
    slack = FakeSlack()

    @asynccontextmanager
    async def connect() -> AsyncIterator[Box]:
        yield cast(Box, box)

    ingest = MediaIngest(
        settings=MediaIngestConfig(
            ssh_endpoint="mac",
            studio_url=STUDIO,
            volumes=volumes or ["CARD"],
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
    # It stays open for the delete list that upload just posted, then closes.
    batch = ingest.state.batches[0]
    assert batch.delete_offer is not None
    batch.delete_offer.offered_at -= timedelta(days=30)
    await ingest.check_comments_once()
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


def _transcode_config(
    tmp_path: Path, volumes: list[str] | None = None
) -> MediaIngestTranscodeConfig:
    template = tmp_path / "t.sh"
    template.write_text(TEMPLATE)
    return MediaIngestTranscodeConfig(
        template=str(template), output_suffix="_HQ.mp4", work_dir="/w", volumes=volumes
    )


@pytest.mark.asyncio
async def test_a_listed_volume_is_transcoded(tmp_path: Path) -> None:
    box = FakeBox([_volume("a.mov")])
    config = _transcode_config(tmp_path, volumes=["CARD"])
    ingest, slack = _setup(tmp_path, box, config, volumes=["CARD", "NINJAV"])
    await ingest.scan_once()
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"1 {LINK}"}]
    await ingest.check_comments_once()

    assert box.uploads == [("/w/job/a_HQ.mp4", LINK)]
    assert "not these originals" in slack.posts[-1][0]


@pytest.mark.asyncio
async def test_an_unlisted_volume_uploads_its_originals(tmp_path: Path) -> None:
    box = FakeBox([_volume("a.mov")])
    config = _transcode_config(tmp_path, volumes=["NINJAV"])
    ingest, slack = _setup(tmp_path, box, config, volumes=["CARD", "NINJAV"])
    await ingest.scan_once()
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"1 {LINK}"}]
    await ingest.check_comments_once()

    assert box.transcodes == []
    assert box.uploads == [("/Volumes/CARD/a.mov", LINK)]
    assert box.resumed == [None]
    assert not any(text.startswith("Transcoding") for text in slack.status_updates)
    assert "Reply `DELETE`" in slack.posts[-1][0]
    assert "not these originals" not in slack.posts[-1][0]


@pytest.mark.asyncio
async def test_an_unlisted_volume_resumes_across_a_restart(tmp_path: Path) -> None:
    box = FakeBox([_volume("a.mov")])
    box.upload_errors["/Volumes/CARD/a.mov"] = UploadFailed("Lost it")
    config = _transcode_config(tmp_path, volumes=["NINJAV"])
    ingest, slack = _setup(tmp_path, box, config, volumes=["CARD", "NINJAV"])
    await ingest.scan_once()
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"1 {LINK}"}]
    await ingest.check_comments_once()

    box.upload_errors.clear()
    restarted, slack = _setup(tmp_path, box, config, volumes=["CARD", "NINJAV"])
    slack.replies = [{"ts": "201.0", "user": "U", "text": f"1 {LINK}"}]
    await restarted.check_comments_once()

    assert box.resumed == [None, "fresh-id"]
    assert box.uploads == [("/Volumes/CARD/a.mov", LINK)]
    assert box.transcodes == []


@pytest.mark.asyncio
async def test_delete_warning_follows_what_was_uploaded_not_the_config(
    tmp_path: Path,
) -> None:
    box = FakeBox([_volume("a.mov", "b.mov")])
    ingest, slack = _setup(tmp_path, box, _transcode_config(tmp_path))
    await ingest.scan_once()
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"1 {LINK}"}]
    await ingest.check_comments_once()

    config = _transcode_config(tmp_path, volumes=["NINJAV"])
    restarted, slack = _setup(tmp_path, box, config, volumes=["CARD", "NINJAV"])
    slack.replies = [{"ts": "201.0", "user": "U", "text": f"2 {LINK}"}]
    await restarted.check_comments_once()

    offer = slack.posts[-1][0]
    assert "#1 `a.mov`" in offer and "#2 `b.mov`" in offer
    assert "Studio only has transcoded versions of #1." in offer


@pytest.mark.asyncio
async def test_a_bad_template_only_stops_volumes_that_use_it(tmp_path: Path) -> None:
    config = _transcode_config(tmp_path, volumes=["NINJAV"])
    Path(config.template).write_text("ffmpeg -i a.mov b.mp4")
    box = FakeBox([_volume("a.mov")])
    ingest, slack = _setup(tmp_path, box, config, volumes=["CARD", "NINJAV"])
    await ingest.scan_once()
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"1 {LINK}"}]
    await ingest.check_comments_once()

    assert box.uploads == [("/Volumes/CARD/a.mov", LINK)]
    assert slack.status_updates[-1] == "Uploaded 1 of 1 to Studio."


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


@pytest.mark.asyncio
async def test_a_failed_upload_resumes_across_a_restart(tmp_path: Path) -> None:
    box = FakeBox([_volume("a.mp4")])
    box.upload_errors["/Volumes/CARD/a.mp4"] = UploadFailed("Lost it")
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"1 {LINK}"}]
    await ingest.check_comments_once()
    video = ingest.state.batches[0].videos[0]
    assert video.upload_ids == {hash_link(LINK): "fresh-id"}

    box.upload_errors.clear()
    restarted, slack = _setup(tmp_path, box)
    slack.replies = [{"ts": "201.0", "user": "U", "text": f"1 {LINK}"}]
    await restarted.check_comments_once()
    assert box.resumed == [None, "fresh-id"]
    video = restarted.state.batches[0].videos[0]
    assert video.upload_ids == {} and video.uploaded_to == [hash_link(LINK)]


@pytest.mark.asyncio
async def test_a_transcoded_upload_is_never_resumed(tmp_path: Path) -> None:
    template = tmp_path / "t.sh"
    template.write_text(TEMPLATE)
    transcode = MediaIngestTranscodeConfig(
        template=str(template), output_suffix="_HQ.mp4", work_dir="/w"
    )
    box = FakeBox([_volume("a.mp4")])
    ingest, slack = _setup(tmp_path, box, transcode)
    await ingest.scan_once()
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"1 {LINK}"}]
    await ingest.check_comments_once()
    assert box.uploads == [("/w/job/a_HQ.mp4", LINK)]
    assert box.resumed == []
    assert ingest.state.batches[0].videos[0].upload_ids == {}


THREAD = SlackMessage(tid="100.000001")


async def _uploaded(
    tmp_path: Path, *names: str, select: str = "all"
) -> tuple[FakeBox, MediaIngest, FakeSlack]:
    """A thread whose videos were announced and then uploaded."""
    box = FakeBox([_volume(*names)])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"{select} {LINK}"}]
    await ingest.check_comments_once()
    return box, ingest, slack


def _reply(slack: FakeSlack, ts: str, text: str = "DELETE") -> None:
    slack.replies.append({"ts": ts, "user": "U", "text": text})


@pytest.mark.asyncio
async def test_uploads_are_offered_for_deletion_in_the_thread(tmp_path: Path) -> None:
    box, ingest, slack = await _uploaded(tmp_path, "a.mp4", "b.mp4", select="1")
    prompt, parent = slack.posts[-1]
    assert parent == THREAD
    assert "`DELETE`" in prompt and "a.mp4" in prompt and "b.mp4" not in prompt
    assert box.deleted == []
    offer = ingest.state.batches[0].delete_offer
    assert offer is not None and offer.numbers == [1]


@pytest.mark.asyncio
async def test_delete_reply_removes_the_uploaded_files(tmp_path: Path) -> None:
    box, ingest, slack = await _uploaded(tmp_path, "a.mp4", "b.mp4", select="1")
    _reply(slack, "300.0")
    await ingest.check_comments_once()
    assert box.deleted == ["/Volumes/CARD/a.mp4"]
    batch = ingest.state.batches[0]
    assert batch.videos[0].deleted and not batch.videos[1].deleted
    assert batch.delete_offer is None
    assert "Deleted 1" in slack.status_updates[-1]
    assert all(parent == THREAD for _, parent in slack.posts[1:])

    _reply(slack, "301.0")
    await ingest.check_comments_once()
    assert box.deleted == ["/Volumes/CARD/a.mp4"]
    assert "Nothing was deleted" in slack.posts[-1][0]


@pytest.mark.asyncio
async def test_each_new_upload_lists_everything_still_on_the_disk(
    tmp_path: Path,
) -> None:
    box, ingest, slack = await _uploaded(tmp_path, "a.mp4", "b.mp4", select="1")
    _reply(slack, "300.0", f"2 {LINK}")
    await ingest.check_comments_once()
    assert "a.mp4" in slack.posts[-1][0] and "b.mp4" in slack.posts[-1][0]
    _reply(slack, "301.0")
    await ingest.check_comments_once()
    assert len(box.deleted) == 2


@pytest.mark.asyncio
async def test_nothing_is_offered_when_no_upload_succeeds(tmp_path: Path) -> None:
    box = FakeBox([_volume("a.mp4")])
    box.upload_errors["/Volumes/CARD/a.mp4"] = UploadFailed("Nope")
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"1 {LINK}"}]
    await ingest.check_comments_once()
    assert ingest.state.batches[0].delete_offer is None
    assert not any("`DELETE`" in text for text, _ in slack.posts)

    _reply(slack, "300.0")
    await ingest.check_comments_once()
    assert box.deleted == []


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["delete", "DELETE 1", "ok DELETE"])
async def test_anything_but_an_exact_delete_removes_nothing(
    tmp_path: Path, text: str
) -> None:
    box, ingest, slack = await _uploaded(tmp_path, "a.mp4")
    _reply(slack, "300.0", text)
    await ingest.check_comments_once()
    assert box.deleted == []


@pytest.mark.asyncio
async def test_delete_from_before_the_latest_list_removes_nothing(
    tmp_path: Path,
) -> None:
    box, ingest, slack = await _uploaded(tmp_path, "a.mp4")
    offer = ingest.state.batches[0].delete_offer
    assert offer is not None
    offer.prompt_ts = "500.0"
    _reply(slack, "300.0")
    await ingest.check_comments_once()
    assert box.deleted == []
    assert ingest.state.batches[0].delete_offer is not None


@pytest.mark.asyncio
async def test_a_changed_file_is_never_deleted(tmp_path: Path) -> None:
    box, ingest, slack = await _uploaded(tmp_path, "a.mp4", "b.mp4")
    box.volumes[0].files[0] = VideoFile("a.mp4", 1000, 99)
    _reply(slack, "300.0")
    await ingest.check_comments_once()
    assert box.deleted == ["/Volumes/CARD/b.mp4"]
    assert "#1: changed or removed" in slack.status_updates[-1]
    assert not ingest.state.batches[0].videos[0].deleted


@pytest.mark.asyncio
async def test_a_different_disk_is_never_deleted_from(tmp_path: Path) -> None:
    box, ingest, slack = await _uploaded(tmp_path, "a.mp4")
    box.volumes[0].id = "uuid:other"
    _reply(slack, "300.0")
    await ingest.check_comments_once()
    assert box.deleted == []
    assert "isn't mounted" in slack.status_updates[-1]
    assert ingest.state.batches[0].delete_offer is not None


@pytest.mark.asyncio
async def test_a_failed_delete_can_be_confirmed_again(tmp_path: Path) -> None:
    box, ingest, slack = await _uploaded(tmp_path, "a.mp4", "b.mp4")
    box.delete_errors["/Volumes/CARD/a.mp4"] = BoxCommandError("Read-only file system")
    _reply(slack, "300.0")
    await ingest.check_comments_once()
    assert box.deleted == ["/Volumes/CARD/b.mp4"]
    assert "Read-only file system" in slack.status_updates[-1]

    box.delete_errors.clear()
    _reply(slack, "301.0")
    await ingest.check_comments_once()
    assert box.deleted == ["/Volumes/CARD/b.mp4", "/Volumes/CARD/a.mp4"]
    assert ingest.state.batches[0].delete_offer is None


@pytest.mark.asyncio
async def test_transcoded_uploads_warn_that_originals_are_not_on_studio(
    tmp_path: Path,
) -> None:
    template = tmp_path / "t.sh"
    template.write_text(TEMPLATE)
    transcode = MediaIngestTranscodeConfig(
        template=str(template), output_suffix="_HQ.mp4", work_dir="/w"
    )
    ingest, slack = _setup(tmp_path, FakeBox([_volume("a.mp4")]), transcode)
    await ingest.scan_once()
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"1 {LINK}"}]
    await ingest.check_comments_once()
    assert "not these originals" in slack.posts[-1][0]


@pytest.mark.asyncio
async def test_a_volume_without_a_disk_id_is_not_offered_for_deletion(
    tmp_path: Path,
) -> None:
    volume = _volume("a.mp4")
    volume.id = "name:CARD"
    box = FakeBox([volume])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"1 {LINK}"}]
    await ingest.check_comments_once()
    assert ingest.state.batches[0].delete_offer is None
    assert "no disk ID" in slack.posts[-1][0]

    _reply(slack, "300.0")
    await ingest.check_comments_once()
    assert box.deleted == []


@pytest.mark.asyncio
async def test_a_long_list_is_shown_in_full_before_it_can_be_confirmed(
    tmp_path: Path,
) -> None:
    names = [f"{'long/' * 40}{index:04}.mp4" for index in range(300)]
    box, ingest, slack = await _uploaded(tmp_path, *names)
    pages = [text for text, _ in slack.posts if "- #" in text]
    assert len(pages) > 1 and all(len(page) <= 8000 for page in pages)
    assert all(name in "".join(pages) for name in names)
    assert "`DELETE`" in pages[-1]
    offer = ingest.state.batches[0].delete_offer
    assert offer is not None and offer.prompt_ts == slack_tid(slack, pages[-1])


def slack_tid(slack: FakeSlack, text: str) -> str:
    index = next(i for i, (posted, _) in enumerate(slack.posts) if posted == text)
    return f"100.{index + 1:06}"


@pytest.mark.asyncio
async def test_an_offer_that_could_not_be_posted_is_retried(tmp_path: Path) -> None:
    box = FakeBox([_volume("a.mp4")])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    post = slack.create_status

    async def failing(text: str, parent_ts: SlackMessage | None = None) -> SlackMessage:
        if "`DELETE`" in text:
            return SlackMessage(tid="error", command_errored=True)
        return await post(text, parent_ts)

    slack.create_status = failing  # type: ignore[method-assign]
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"1 {LINK}"}]
    await ingest.check_comments_once()
    batch = ingest.state.batches[0]
    assert batch.delete_offer is None and batch.delete_offer_due

    slack.create_status = post  # type: ignore[method-assign]
    await ingest.check_comments_once()
    assert batch.delete_offer is not None and not batch.delete_offer_due


@pytest.mark.asyncio
async def test_a_crash_before_the_offer_still_asks_after_restart(
    tmp_path: Path,
) -> None:
    box, ingest, _ = await _uploaded(tmp_path, "a.mp4")
    batch = ingest.state.batches[0]
    batch.delete_offer, batch.delete_offer_due = None, True
    ingest.store.save(ingest.state)

    restarted, slack = _setup(tmp_path, box)
    await restarted.check_comments_once()
    assert restarted.state.batches[0].delete_offer is not None
    assert "`DELETE`" in slack.posts[-1][0]


@pytest.mark.asyncio
async def test_a_list_posted_at_expiry_can_still_be_answered(tmp_path: Path) -> None:
    box = FakeBox([_volume("a.mp4")])
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    batch = ingest.state.batches[0]
    batch.created_at = batch.created_at - timedelta(days=30)
    slack.replies = [{"ts": "200.0", "user": "U", "text": f"1 {LINK}"}]
    await ingest.check_comments_once()
    assert ingest.state.batches == [batch] and batch.delete_offer is not None

    _reply(slack, "300.0")
    await ingest.check_comments_once()
    assert box.deleted == ["/Volumes/CARD/a.mp4"]
    assert ingest.state.batches == []


def _named_volume(name: str, *files: str) -> Volume:
    return Volume(
        id=f"uuid:{name}",
        name=name,
        mount_point=f"/Volumes/{name}",
        files=[VideoFile(file, 1000, 5) for file in files],
    )


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not condition():
            await asyncio.sleep(0.001)


async def _let_others_run() -> None:
    for _ in range(50):
        await asyncio.sleep(0)


def _thread_of(ingest: MediaIngest, volume_id: str) -> str:
    return next(b.thread_ts for b in ingest.state.batches if b.volume_id == volume_id)


def _ask(ts: str = "200.0", text: str = f"all {LINK}") -> list[dict[str, str]]:
    return [{"ts": ts, "user": "U", "text": text}]


@pytest.mark.asyncio
async def test_footage_with_no_transcode_doesnt_wait_behind_footage_with_one(
    tmp_path: Path,
) -> None:
    box = FakeBox([_named_volume("CARD", "a.mov"), _named_volume("NINJAV", "b.mov")])
    release = box.hold["/Volumes/NINJAV/b.mov"] = asyncio.Event()
    config = _transcode_config(tmp_path, volumes=["NINJAV"])
    ingest, slack = _setup(tmp_path, box, config, volumes=["CARD", "NINJAV"])
    await ingest.scan_once()
    slack.thread_replies = {
        _thread_of(ingest, "uuid:CARD"): _ask(),
        _thread_of(ingest, "uuid:NINJAV"): _ask(),
    }

    await ingest.poll_comments()
    await _until(lambda: box.uploads == [("/Volumes/CARD/a.mov", LINK)])
    await _let_others_run()
    assert len(box.uploads) == 1

    release.set()
    await ingest.check_comments_once()
    assert box.uploads[1] == ("/w/job/b_HQ.mp4", LINK)


@pytest.mark.asyncio
async def test_a_volume_is_only_read_for_one_video_at_a_time(tmp_path: Path) -> None:
    box = FakeBox([_named_volume("CARD", "a.mov")])
    release = box.hold["/Volumes/CARD/a.mov"] = asyncio.Event()
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    box.volumes[0].files.append(VideoFile("b.mov", 1000, 5))
    await ingest.scan_once()
    first, second = (batch.thread_ts for batch in ingest.state.batches)
    slack.thread_replies = {first: _ask(), second: _ask()}

    await ingest.poll_comments()
    await _until(lambda: box.reading == 1)
    await _let_others_run()
    assert box.uploads == []
    assert "Waiting for its turn: 1 of 1: #1 (1000 B)" in slack.status_updates

    release.set()
    await ingest.check_comments_once()
    assert sorted(path for path, _ in box.uploads) == [
        "/Volumes/CARD/a.mov",
        "/Volumes/CARD/b.mov",
    ]
    assert box.most_reading == 1


@pytest.mark.asyncio
async def test_one_transcode_at_a_time_but_not_through_its_upload(
    tmp_path: Path,
) -> None:
    box = FakeBox([_named_volume("ONE", "a.mov"), _named_volume("TWO", "b.mov")])
    transcoding = box.hold["/Volumes/ONE/a.mov"] = asyncio.Event()
    uploading = box.hold["/w/job/a_HQ.mp4"] = asyncio.Event()
    ingest, slack = _setup(
        tmp_path, box, _transcode_config(tmp_path), volumes=["ONE", "TWO"]
    )
    await ingest.scan_once()
    slack.thread_replies = {
        _thread_of(ingest, "uuid:ONE"): _ask(),
        _thread_of(ingest, "uuid:TWO"): _ask(),
    }

    await ingest.poll_comments()
    await _until(lambda: len(box.transcodes) == 1)
    await _let_others_run()
    assert len(box.transcodes) == 1

    transcoding.set()
    await _until(lambda: len(box.transcodes) == 2)
    uploading.set()
    await ingest.check_comments_once()
    assert len(box.uploads) == 2


@pytest.mark.asyncio
async def test_every_comment_is_acknowledged_at_once_even_on_a_busy_thread(
    tmp_path: Path,
) -> None:
    box = FakeBox([_volume("a.mov")])
    release = box.hold["/Volumes/CARD/a.mov"] = asyncio.Event()
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    slack.replies = _ask()

    await ingest.poll_comments()
    await _until(lambda: box.reading == 1)
    slack.replies = [*_ask(), *_ask("201.0", "nice shot")]
    await ingest.poll_comments()
    await ingest.poll_comments()

    assert slack.reactions == [("200.0", "eyes"), ("201.0", "eyes")]
    assert box.uploads == []

    release.set()
    await ingest.check_comments_once()
    await ingest.check_comments_once()
    assert len(box.uploads) == 1
    assert ingest.state.batches[0].last_reply_ts == "201.0"


@pytest.mark.asyncio
async def test_a_comment_finished_while_slack_answers_isnt_acted_on_twice(
    tmp_path: Path,
) -> None:
    box = FakeBox([_volume("a.mov")])
    release = box.hold["/Volumes/CARD/a.mov"] = asyncio.Event()
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    slack.replies = _ask()
    await ingest.poll_comments()
    await _until(lambda: box.reading == 1)
    listing = slack.list_thread_replies

    async def finishing_meanwhile(parent, *, after_ts):
        answer = await listing(parent, after_ts=after_ts)
        release.set()
        await asyncio.gather(*ingest._thread_work.values())
        return answer

    slack.list_thread_replies = finishing_meanwhile  # type: ignore[method-assign]
    await ingest.poll_comments()
    slack.list_thread_replies = listing  # type: ignore[method-assign]
    await ingest.check_comments_once()

    assert box.uploads == [("/Volumes/CARD/a.mov", LINK)]


@pytest.mark.asyncio
async def test_a_thread_that_fails_is_retried_without_stopping_the_others(
    tmp_path: Path,
) -> None:
    box = FakeBox([_named_volume("ONE", "a.mov"), _named_volume("TWO", "b.mov")])
    ingest, slack = _setup(tmp_path, box, volumes=["ONE", "TWO"])
    await ingest.scan_once()
    broken = _thread_of(ingest, "uuid:ONE")
    slack.thread_replies = {broken: _ask(), _thread_of(ingest, "uuid:TWO"): _ask()}
    posting = slack.create_status

    async def failing_once(text, parent_ts=None):
        if parent_ts and parent_ts.tid == broken and text.startswith("Uploading"):
            slack.create_status = posting  # type: ignore[method-assign]
            raise OSError("Slack went away")
        return await posting(text, parent_ts)

    slack.create_status = failing_once  # type: ignore[method-assign]
    await ingest.check_comments_once()
    assert [path for path, _ in box.uploads] == ["/Volumes/TWO/b.mov"]

    await ingest.check_comments_once()
    assert sorted(path for path, _ in box.uploads) == [
        "/Volumes/ONE/a.mov",
        "/Volumes/TWO/b.mov",
    ]


@pytest.mark.asyncio
async def test_a_finished_transcode_uploads_while_the_next_runs_but_only_so_many(
    tmp_path: Path,
) -> None:
    box = FakeBox([_volume("a.mov", "b.mov", "c.mov")])
    first = box.hold["/w/job/a_HQ.mp4"] = asyncio.Event()
    second = box.hold["/w/job/b_HQ.mp4"] = asyncio.Event()
    ingest, slack = _setup(tmp_path, box, _transcode_config(tmp_path))
    await ingest.scan_once()
    slack.replies = _ask()

    await ingest.poll_comments()
    await _until(lambda: len(box.transcodes) == 2)
    await _let_others_run()
    assert len(box.transcodes) == 2
    assert box.uploads == []

    first.set()
    await _until(lambda: len(box.transcodes) == 3)
    second.set()
    await ingest.check_comments_once()
    assert sorted(path for path, _ in box.uploads) == [
        "/w/job/a_HQ.mp4",
        "/w/job/b_HQ.mp4",
        "/w/job/c_HQ.mp4",
    ]
    assert slack.status_updates[-1] == "Uploaded 3 of 3 to Studio."


@pytest.mark.asyncio
async def test_a_closed_link_stops_the_videos_still_waiting(tmp_path: Path) -> None:
    box = FakeBox([_volume("a.mov", "b.mov", "c.mov")])
    box.upload_errors["/Volumes/CARD/a.mov"] = UploadFailed(
        "Link closed", link_closed=True
    )
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    slack.replies = _ask()

    await ingest.check_comments_once()

    assert box.resumed == [None]
    assert slack.status_updates[-1] == (
        "Uploaded 0 of 3 to Studio.\nNot uploaded:\n- #1: Link closed"
    )


@pytest.mark.asyncio
async def test_failures_are_reported_in_order_and_the_rest_still_upload(
    tmp_path: Path,
) -> None:
    box = FakeBox([_volume("a.mov", "b.mov", "c.mov")])
    box.upload_errors["/Volumes/CARD/a.mov"] = UploadFailed("One")
    box.upload_errors["/Volumes/CARD/c.mov"] = UploadFailed("Three")
    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    slack.replies = _ask()

    await ingest.check_comments_once()

    assert box.uploads == [("/Volumes/CARD/b.mov", LINK)]
    assert slack.status_updates[-1] == (
        "Uploaded 1 of 3 to Studio.\nNot uploaded:\n- #1: One\n- #3: Three"
    )


@pytest.mark.asyncio
async def test_losing_the_box_stops_the_reply_and_says_why(tmp_path: Path) -> None:
    box = FakeBox([_volume("a.mov", "b.mov")])

    async def lost(path: str, link: str) -> None:
        raise ConnectionResetError("gone")

    ingest, slack = _setup(tmp_path, box)
    await ingest.scan_once()
    box.upload = lost  # type: ignore[method-assign]
    slack.replies = _ask()

    await ingest.check_comments_once()

    assert "couldn't be reached (ConnectionResetError)" in slack.status_updates[-1]
    assert ingest.state.batches[0].videos[0].uploaded_to == []


@pytest.mark.asyncio
async def test_a_reaction_slack_refuses_never_stops_anything() -> None:
    client = SlackClient(bot_token="x", app_token="y", channel_id="C1")
    for error in ("already_reacted", "missing_scope"):
        refusal = SlackApiError("no", cast(object, {"error": error}))
        client._web = cast(
            AsyncWebClient,
            SimpleNamespace(reactions_add=AsyncMock(side_effect=refusal)),
        )
        await client.add_reaction(SlackMessage(tid="1.0"), "eyes")
