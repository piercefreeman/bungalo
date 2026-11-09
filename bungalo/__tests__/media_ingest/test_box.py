import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import asyncssh
import pytest

from bungalo.media_ingest.box import (
    Box,
    UploadFailed,
    VideoFile,
    parse_scan,
    read_transcode_template,
    sample_offsets,
)

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


@pytest.mark.asyncio
async def test_upload_keeps_the_link_off_the_command_line() -> None:
    connection = FakeConnection(FakeResult(stdout=b'{"files": []}\n200'))
    await Box(connection).upload("/Volumes/CARD/my clip.mov", LINK)
    command, kwargs = connection.calls[0]
    assert "abc_DEF-123" not in command
    assert kwargs["input"] == f'url = "{LINK}/my%20clip.mov"\n'.encode()


@pytest.mark.asyncio
async def test_upload_surfaces_studio_refusal_and_closed_links() -> None:
    refused = FakeConnection(FakeResult(stdout=b'{"detail": "Too big"}\n413'))
    with pytest.raises(UploadFailed, match="Too big") as too_big:
        await Box(refused).upload("/Volumes/CARD/a.mov", LINK)
    assert not too_big.value.link_closed

    closed = FakeConnection(FakeResult(stdout=b'{"detail": "Closed"}\n404'))
    with pytest.raises(UploadFailed) as gone:
        await Box(closed).upload("/Volumes/CARD/a.mov", LINK)
    assert gone.value.link_closed


@pytest.mark.asyncio
async def test_upload_error_never_repeats_the_link() -> None:
    connection = FakeConnection(FakeResult(exit_status=7, stderr=LINK.encode()))
    with pytest.raises(UploadFailed) as failed:
        await Box(connection).upload("/Volumes/CARD/a.mov", LINK)
    assert "abc_DEF-123" not in failed.value.reason


@pytest.mark.asyncio
async def test_studio_error_details_never_repeat_the_token() -> None:
    body = b'{"detail": "Rejected /public-upload/abc_DEF-123/a.mov"}\n422'
    connection = FakeConnection(FakeResult(stdout=body))
    with pytest.raises(UploadFailed) as failed:
        await Box(connection).upload("/Volumes/CARD/a.mov", LINK)
    assert "abc_DEF-123" not in failed.value.reason


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
    async with box.transcoded("/Volumes/CARD/it's.mov", "a.mp4", TEMPLATE, "/w") as out:
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
        async with Box(connection).transcoded("/a.mov", "a.mp4", TEMPLATE, "/w"):
            pytest.fail("should not yield")
    assert "progress=" not in failed.value.reason
    assert connection.commands[-1] == "rm -rf /w/.job"


@pytest.mark.asyncio
async def test_transcode_that_writes_nothing_is_a_failure() -> None:
    connection = ScriptedConnection(
        FakeResult(stdout=b"/w/.job\n"), FakeResult(), FakeResult(exit_status=1)
    )
    with pytest.raises(UploadFailed, match="without writing"):
        async with Box(connection).transcoded("/a.mov", "a.mp4", TEMPLATE, "/w"):
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
        async with Box(connection).transcoded("/a.mov", "a.mp4", TEMPLATE, "/w"):
            pass
    logger.warning.assert_called_once()
