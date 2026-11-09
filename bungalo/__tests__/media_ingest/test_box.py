import asyncio
import json
import os
import subprocess
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import asyncssh
import pytest

from bungalo.media_ingest import box as box_module
from bungalo.media_ingest.box import (
    Box,
    BoxCommandError,
    StudioUpload,
    TranscodeProgress,
    UploadFailed,
    VideoFile,
    Volume,
    parse_scan,
    parse_transcode_progress,
    read_transcode_template,
    sample_offsets,
)
from bungalo.media_ingest.state import UploadSessionID

STUDIO = "https://studio.example.com"


LINK = f"{STUDIO}/public-upload/abc_DEF-123"


def test_parse_scan_groups_files_and_identifies_volumes() -> None:
    volumes = parse_scan(
        "V\tABC-1\tCARD\t/Volumes/CARD 1\n"
        "F\t100\t1700\t/Volumes/CARD 1/DCIM/C0001.MP4\n"
        "F\tbroken\t1700\t/Volumes/CARD 1/bad.mp4\n"
        "V\t\tShare\t/Volumes/Share\n"
        "F\t5\t9\t/Volumes/Share/a\tb.mov\n"
    )
    assert [v.id for v in volumes] == ["uuid:ABC-1", "name:Share"]
    assert [v.name for v in volumes] == ["CARD", "Share"]
    assert volumes[0].files == [VideoFile("DCIM/C0001.MP4", 100, 1700)]
    assert volumes[1].files == [VideoFile("a\tb.mov", 5, 9)]


def test_sample_offsets_span_the_play_period() -> None:
    assert sample_offsets(100) == [10, 30, 50, 70, 90]


class FakeResult:
    def __init__(self, exit_status: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class FakeConnection:
    def __init__(self, result: FakeResult):
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run(self, command: str, **kwargs: Any) -> FakeResult:
        self.calls.append((command, kwargs))
        return self.result


TOKEN = "abc_DEF-123"
PATH = "/Volumes/CARD/my clip.mov"


def _opened(missing: list[int], upload_id: str = "up1", size: int = 70) -> FakeResult:
    answer = {
        "upload_id": upload_id,
        "size_bytes": size,
        "part_bytes": 32,
        "parts": 3,
        "missing": missing,
    }
    return FakeResult(stdout=json.dumps(answer).encode() + b"\n200")


class FakeStudio:
    """Answers the box's commands as Studio would; `answers` overrides by request."""

    def __init__(self, *answers: tuple[str, FakeResult]):
        self.answers = list(answers)
        self.requests: list[str] = []
        self.commands: list[str] = []
        self.size = 70
        self.mtimes: list[int] = []

    async def run(self, command: str, **kwargs: Any) -> FakeResult:
        self.commands.append(command)
        if command.startswith("stat "):
            mtime = self.mtimes.pop(0) if self.mtimes else 5
            return FakeResult(stdout=f"{self.size} {mtime}\n".encode())
        url = kwargs["input"].decode().removeprefix('url = "').rstrip('"\n')
        suffix = url.removeprefix(LINK)
        method = (
            "PUT"
            if "--upload-file" in command
            else command.split("--request ")[1].split()[0]
        )
        request = f"{method} {suffix}"
        self.requests.append(request)
        for index, (expected, answer) in enumerate(self.answers):
            if expected == request:
                del self.answers[index]
                return answer
        if method == "PUT":
            return FakeResult(stdout=b"\n204")
        if method == "POST" and "?filename=" in suffix:
            return _opened([1, 2, 3])
        if method == "DELETE":
            return FakeResult(stdout=b"\n204")
        return FakeResult(stdout=b'{"media_file_id": "m", "name": "a"}\n200')

    @property
    def parts(self) -> list[str]:
        return sorted(r for r in self.requests if r.startswith("PUT"))


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bungalo.media_ingest.box.RETRY_DELAY_SECONDS", 0)


ALL_PARTS = [f"PUT /uploads/up1/parts/{number}" for number in (1, 2, 3)]
COMPLETE = "POST /uploads/up1/complete"


@pytest.mark.asyncio
async def test_upload_sends_every_part_then_completes() -> None:
    studio = FakeStudio()
    await Box(studio).upload(PATH, LINK)
    assert studio.requests[0] == "POST /uploads?filename=my%20clip.mov&size_bytes=70"
    assert studio.parts == ALL_PARTS
    assert studio.requests[-1] == COMPLETE
    assert not any(TOKEN in command for command in studio.commands)
    part_three = next(c for c in studio.commands if "skip=2" in c)
    assert "bs=32" in part_three and "my clip.mov" in part_three


@pytest.mark.asyncio
async def test_resuming_sends_only_what_studio_is_missing() -> None:
    studio = FakeStudio(("GET /uploads/up1", _opened([2])))
    box = Box(studio)
    upload = await box.open_upload(PATH, LINK, resume_id=UploadSessionID("up1"))
    await box.send_upload(PATH, LINK, upload)
    assert studio.requests == [
        "GET /uploads/up1",
        "PUT /uploads/up1/parts/2",
        COMPLETE,
    ]


@pytest.mark.asyncio
async def test_resuming_a_finished_upload_only_completes_it() -> None:
    studio = FakeStudio(("GET /uploads/up1", _opened([])))
    box = Box(studio)
    await box.send_upload(
        PATH, LINK, await box.open_upload(PATH, LINK, UploadSessionID("up1"))
    )
    assert studio.requests == ["GET /uploads/up1", COMPLETE]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        FakeResult(stdout=b'{"detail": "expired"}\n410'),
        FakeResult(stdout=b'{"detail": "unknown"}\n404'),
        _opened([1], size=999),
    ],
    ids=["expired", "unknown", "different-size"],
)
async def test_an_upload_that_cant_be_resumed_starts_again(answer: FakeResult) -> None:
    studio = FakeStudio(("GET /uploads/old", answer))
    upload = await Box(studio).open_upload(PATH, LINK, resume_id=UploadSessionID("old"))
    assert upload.upload_id == "up1" and upload.missing == [1, 2, 3]
    assert studio.requests[-1].startswith("POST /uploads?filename=")


@pytest.mark.asyncio
async def test_lost_connections_and_server_errors_are_retried() -> None:
    studio = FakeStudio(
        ("PUT /uploads/up1/parts/2", FakeResult(exit_status=7, stdout=b"\n000")),
        ("PUT /uploads/up1/parts/2", FakeResult(stdout=b"Bad gateway\n502")),
        (COMPLETE, FakeResult(exit_status=52, stdout=b"\n000")),
    )
    await Box(studio).upload(PATH, LINK)
    assert studio.requests.count("PUT /uploads/up1/parts/2") == 3
    assert studio.requests.count(COMPLETE) == 2


@pytest.mark.asyncio
async def test_upload_surfaces_studio_refusal_without_retrying() -> None:
    too_big = ("POST /uploads?filename=my%20clip.mov&size_bytes=70",)
    studio = FakeStudio((*too_big, FakeResult(stdout=b'{"detail": "Too big"}\n413')))
    with pytest.raises(UploadFailed, match="Too big") as failed:
        await Box(studio).upload(PATH, LINK)
    assert not failed.value.link_closed
    assert len(studio.requests) == 1


OPENING = "POST /uploads?filename=my%20clip.mov&size_bytes=70"


@pytest.mark.asyncio
async def test_a_lost_answer_to_opening_is_not_repeated() -> None:
    # Studio may have opened it; another try would leave a second one open.
    studio = FakeStudio((OPENING, FakeResult(exit_status=52, stdout=b"\n000")))
    with pytest.raises(UploadFailed):
        await Box(studio).upload(PATH, LINK)
    assert studio.requests == [OPENING]


@pytest.mark.asyncio
async def test_opening_is_retried_when_it_was_never_sent() -> None:
    studio = FakeStudio((OPENING, FakeResult(exit_status=7, stdout=b"\n000")))
    await Box(studio).upload(PATH, LINK)
    assert studio.requests.count(OPENING) == 2


@pytest.mark.asyncio
async def test_a_missing_upload_does_not_close_the_link() -> None:
    gone = FakeResult(stdout=b'{"detail": "No upload"}\n404')
    studio = FakeStudio(("PUT /uploads/up1/parts/1", gone))
    box = Box(studio)
    with pytest.raises(UploadFailed, match="No upload") as failed:
        await box.send_upload(PATH, LINK, await box.open_upload(PATH, LINK))
    assert not failed.value.link_closed


@pytest.mark.asyncio
async def test_a_file_that_changes_during_upload_is_not_assembled() -> None:
    studio = FakeStudio()
    studio.mtimes = [5, 5, 9]
    box = Box(studio)
    with pytest.raises(UploadFailed, match="changed"):
        await box.send_upload(PATH, LINK, await box.open_upload(PATH, LINK))
    assert COMPLETE not in studio.requests
    assert "DELETE /uploads/up1" in studio.requests


@pytest.mark.asyncio
async def test_a_closed_link_is_reported_as_closed() -> None:
    opening = "POST /uploads?filename=my%20clip.mov&size_bytes=70"
    studio = FakeStudio((opening, FakeResult(stdout=b'{"detail": "Closed"}\n404')))
    with pytest.raises(UploadFailed) as failed:
        await Box(studio).upload(PATH, LINK)
    assert failed.value.link_closed


@pytest.mark.asyncio
async def test_a_file_that_cant_be_read_is_not_retried() -> None:
    studio = FakeStudio(("PUT /uploads/up1/parts/1", FakeResult(exit_status=125)))
    with pytest.raises(UploadFailed, match="Couldn't read"):
        await Box(studio).upload(PATH, LINK)
    assert studio.requests.count("PUT /uploads/up1/parts/1") == 1


@pytest.mark.asyncio
async def test_a_one_shot_upload_is_discarded_when_a_part_fails() -> None:
    refused = FakeResult(stdout=b'{"detail": "Wrong size"}\n422')
    studio = FakeStudio(("PUT /uploads/up1/parts/1", refused))
    with pytest.raises(UploadFailed, match="Wrong size"):
        await Box(studio).upload(PATH, LINK)
    assert "DELETE /uploads/up1" in studio.requests
    assert COMPLETE not in studio.requests


@pytest.mark.asyncio
async def test_a_failed_completion_is_not_discarded() -> None:
    # Completion may have worked with only its answer lost.
    studio = FakeStudio(*[(COMPLETE, FakeResult(exit_status=52))] * 11)
    with pytest.raises(UploadFailed):
        await Box(studio).upload(PATH, LINK)
    assert "DELETE /uploads/up1" not in studio.requests


@pytest.mark.asyncio
async def test_errors_never_repeat_the_token() -> None:
    lost = FakeResult(exit_status=7, stderr=f"{LINK}/uploads".encode())
    opening = "POST /uploads?filename=my%20clip.mov&size_bytes=70"
    studio = FakeStudio(*[(opening, lost)] * 11)
    with pytest.raises(UploadFailed) as failed:
        await Box(studio).upload(PATH, LINK)
    assert TOKEN not in failed.value.reason

    detail = json.dumps({"detail": f"Rejected {LINK}/a.mov"}).encode()
    studio = FakeStudio((opening, FakeResult(stdout=detail + b"\n422")))
    with pytest.raises(UploadFailed) as failed:
        await Box(studio).upload(PATH, f"{LINK}/")
    assert TOKEN not in failed.value.reason


def _delete_setup(
    tmp_path: Path, *, uuid: str = "ABC", version: str = "10 5"
) -> tuple[Box, Volume, VideoFile, Path, Path]:
    """A real sh and rm, with stand-ins for the macOS-only diskutil and BSD stat."""
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "diskutil").write_text(f"#!/bin/sh\necho '   Volume UUID:   {uuid}'\n")
    (tools / "stat").write_text(f"#!/bin/sh\necho '{version}'\n")
    for tool in tools.iterdir():
        tool.chmod(0o755)

    class Local:
        async def run(self, command: str, **kwargs: Any) -> FakeResult:
            path = f"{tools}:{os.environ['PATH']}"
            done = subprocess.run(
                ["/bin/sh", "-c", command],
                capture_output=True,
                env={**os.environ, "PATH": path},
            )
            return FakeResult(done.returncode, done.stdout, done.stderr)

    mount = tmp_path / "CARD"
    mount.mkdir()
    target = mount / "it's $HOME *.mov"
    neighbour = mount / "other.mov"
    target.write_bytes(b"x")
    neighbour.write_bytes(b"y")
    volume = Volume(id="uuid:ABC", name="CARD", mount_point=str(mount))
    return Box(Local()), volume, VideoFile(target.name, 10, 5), target, neighbour


@pytest.mark.asyncio
async def test_delete_removes_only_the_named_file(tmp_path: Path) -> None:
    box, volume, video, target, neighbour = _delete_setup(tmp_path)
    await box.delete(volume, video)
    assert not target.exists() and neighbour.exists()


@pytest.mark.asyncio
async def test_delete_refuses_a_different_disk_at_that_mount(tmp_path: Path) -> None:
    box, volume, video, target, _ = _delete_setup(tmp_path, uuid="OTHER")
    with pytest.raises(BoxCommandError, match="different disk"):
        await box.delete(volume, video)
    assert target.exists()


@pytest.mark.asyncio
async def test_delete_refuses_a_file_that_was_replaced(tmp_path: Path) -> None:
    box, volume, video, target, _ = _delete_setup(tmp_path, version="10 99")
    with pytest.raises(BoxCommandError, match="changed"):
        await box.delete(volume, video)
    assert target.exists()


@pytest.mark.asyncio
async def test_delete_refuses_a_volume_known_only_by_name(tmp_path: Path) -> None:
    box, volume, video, target, _ = _delete_setup(tmp_path)
    volume.id = "name:CARD"
    with pytest.raises(BoxCommandError, match="identified"):
        await box.delete(volume, video)
    assert target.exists()


def test_parse_scan_keeps_carriage_returns_inside_file_names() -> None:
    volumes = parse_scan("V\tU\tCARD\t/Volumes/CARD\nF\t1\t2\t/Volumes/CARD/a\rb.mov\n")
    assert volumes[0].files == [VideoFile("a\rb.mov", 1, 2)]


class ScriptedConnection:
    def __init__(self, *results: FakeResult):
        self.results = list(results)
        self.commands: list[str] = []

    async def run(self, command: str, **kwargs: Any) -> FakeResult:
        self.commands.append(command)
        return self.results.pop(0) if self.results else FakeResult()


TEMPLATE = 'ffmpeg -i "$INPUT" "$OUTPUT"\n'


@pytest.mark.asyncio
async def test_transcode_passes_paths_safely_and_cleans_up() -> None:
    """Regression guard: ffmpeg must not be able to read the session's stdin."""
    connection = ScriptedConnection(FakeResult(stdout=b"/w/.job\n"))
    box = Box(connection)
    async with box.transcoded(
        "/Volumes/CARD/it's.mov", "a.mp4", TEMPLATE, "/w", gate=nullcontext()
    ) as out:
        assert out == "/w/.job/a.mp4"

    make, run, check, remove = connection.commands
    assert ".bungalo-transcode.XXXXXX" in make
    assert """export INPUT='/Volumes/CARD/it'"'"'s.mov'""" in run
    assert "OUTPUT=/w/.job/a.mp4" in run
    assert run.endswith("</dev/null")
    assert "/bin/sh -e -c" in run
    assert check == "test -s /w/.job/a.mp4"
    assert remove == "rm -rf /w/.job"


@pytest.mark.asyncio
async def test_failed_transcode_reports_the_error_and_cleans_up() -> None:
    stderr = (
        b"frame=  10 fps=1\nprogress=continue\nout_time=00:00:01\nNo such encoder\n"
    )
    connection = ScriptedConnection(
        FakeResult(stdout=b"/w/.job\n"), FakeResult(exit_status=1, stderr=stderr)
    )
    with pytest.raises(UploadFailed, match="No such encoder") as failed:
        async with Box(connection).transcoded(
            "/a.mov", "a.mp4", TEMPLATE, "/w", gate=nullcontext()
        ):
            pytest.fail("should not yield")
    assert "progress=" not in failed.value.reason
    assert connection.commands[-1] == "rm -rf /w/.job"


@pytest.mark.asyncio
async def test_transcode_that_writes_nothing_is_a_failure() -> None:
    connection = ScriptedConnection(
        FakeResult(stdout=b"/w/.job\n"), FakeResult(), FakeResult(exit_status=1)
    )
    with pytest.raises(UploadFailed, match="without writing"):
        async with Box(connection).transcoded(
            "/a.mov", "a.mp4", TEMPLATE, "/w", gate=nullcontext()
        ):
            pytest.fail("should not yield")


@pytest.mark.parametrize("body", ['ffmpeg -i "$INPUT" out.mp4', 'ffmpeg "$OUTPUT"'])
def test_template_must_use_both_variables(tmp_path: Path, body: str) -> None:
    path = tmp_path / "t.sh"
    path.write_text(body)
    with pytest.raises(ValueError, match="doesn't use"):
        read_transcode_template(str(path))


def test_template_accepts_braced_variables(tmp_path: Path) -> None:
    path = tmp_path / "t.sh"
    path.write_text('ffmpeg -i "${INPUT}" "${OUTPUT}"')
    assert read_transcode_template(str(path))


class LocalConnection:
    """Runs commands in a real local shell."""

    async def run(self, command: str, **kwargs: Any) -> Any:
        done = subprocess.run(command, shell=True, capture_output=True)
        return SimpleNamespace(
            exit_status=done.returncode, stdout=done.stdout, stderr=done.stderr
        )


@pytest.mark.asyncio
async def test_a_new_transcode_keeps_active_folders_and_clears_abandoned_ones(
    tmp_path: Path,
) -> None:
    """Regression: starting a transcode must never delete another job's output."""
    box = Box(LocalConnection())
    active = Path(await box._make_work_dir(str(tmp_path)))
    (active / "output.mp4").write_bytes(b"video")

    abandoned = tmp_path / ".bungalo-transcode.old"
    abandoned.mkdir()
    long_ago = time.time() - 10 * 24 * 3600
    os.utime(abandoned, (long_ago, long_ago))

    await box._make_work_dir(str(tmp_path))
    assert (active / "output.mp4").exists()
    assert not abandoned.exists()


class _LostConnection(FakeConnection):
    async def run(self, command: str, **kwargs: Any) -> FakeResult:
        if command.startswith("rm -rf"):
            raise asyncssh.ConnectionLost("gone")
        return await super().run(command, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "connection",
    [
        ScriptedConnection(
            FakeResult(stdout=b"/w/.job\n"),
            FakeResult(),
            FakeResult(),
            FakeResult(exit_status=1),
        ),
        _LostConnection(FakeResult(stdout=b"/w/.job\n")),
    ],
)
async def test_failed_cleanup_is_logged_without_replacing_the_result(
    connection: Any,
) -> None:
    with patch("bungalo.media_ingest.box.LOGGER") as logger:
        async with Box(connection).transcoded(
            "/a.mov", "a.mp4", TEMPLATE, "/w", gate=nullcontext()
        ):
            pass
    logger.warning.assert_called_once()


class AsyncLocalConnection:
    """A real local shell that leaves the event loop free while a command runs."""

    async def run(self, command: str, **kwargs: Any) -> Any:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return SimpleNamespace(
            exit_status=process.returncode, stdout=stdout, stderr=stderr
        )


def test_progress_is_the_newest_reading_ffmpeg_wrote() -> None:
    text = (
        "out_time_us=1000000\nspeed=1.2x\nprogress=continue\n"
        "out_time_us=N/A\nspeed=N/A\nprogress=continue\n"
        "out_time_us=9500000\nspeed= 1.6x\nprogress=continue\nframe=2"
    )
    assert parse_transcode_progress(text, 19.0) == TranscodeProgress(9.5, 19.0, 1.6)


@pytest.mark.parametrize("text", ["", "frame=1\n", "out_time_us=N/A\n"])
def test_no_progress_before_ffmpeg_has_a_reading(text: str) -> None:
    assert parse_transcode_progress(text, 19.0) is None


@pytest.mark.asyncio
async def test_a_running_transcode_reports_what_it_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(box_module, "PROGRESS_SECONDS", 0.01)
    template = (
        'test -n "$INPUT"\n'
        "printf 'out_time_us=5000000\\nspeed=2.0x\\nprogress=continue\\n' > \"$PROGRESS\"\n"
        'sleep 0.3\necho done > "$OUTPUT"\n'
    )
    seen: list[TranscodeProgress] = []

    async def report(progress: TranscodeProgress) -> None:
        seen.append(progress)

    box = Box(AsyncLocalConnection())
    async with box.transcoded(
        "/no/such.mov",
        "a.mp4",
        template,
        str(tmp_path),
        gate=nullcontext(),
        on_progress=report,
    ):
        assert seen
    assert seen[0].encoded_seconds == 5.0 and seen[0].speed == 2.0


@pytest.mark.asyncio
async def test_progress_continues_without_a_length_when_the_probe_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a box without ffprobe silently lost all transcode progress."""
    monkeypatch.setattr(box_module, "PROGRESS_SECONDS", 0.01)
    template = (
        "printf 'out_time_us=5000000\\nprogress=continue\\n' > \"$PROGRESS\"\n"
        'sleep 0.3\necho done > "$OUTPUT"\n'
    )
    seen: list[TranscodeProgress] = []

    async def report(progress: TranscodeProgress) -> None:
        seen.append(progress)

    async def no_probe(path: str) -> float | None:
        raise BoxCommandError("ffprobe is not installed on the box")

    box = Box(AsyncLocalConnection())
    monkeypatch.setattr(box, "_duration", no_probe)
    async with box.transcoded(
        "/no/such.mov",
        "a.mp4",
        template,
        str(tmp_path),
        gate=nullcontext(),
        on_progress=report,
    ):
        assert seen
    assert seen[0] == TranscodeProgress(5.0, None, None)


@pytest.mark.asyncio
async def test_upload_reports_each_part_and_counts_what_studio_already_had() -> None:
    reports: list[tuple[int, int]] = []

    async def report(sent: int, total: int) -> None:
        reports.append((sent, total))

    box = Box(FakeStudio())
    upload = StudioUpload(
        upload_id=UploadSessionID("up1"), size_bytes=70, part_bytes=30, missing=[2, 3]
    )
    await box._send_parts("/a.mov", LINK, upload, report)

    assert reports[0] == (30, 70)
    assert reports[-1] == (70, 70)
