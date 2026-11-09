"""
Everything that happens on the machine holding the footage. Commands run over
SSH so video never travels through Bungalo: frames come back as small JPEGs,
and uploads go straight from the box to Studio with curl.
"""

import json
import os
import re
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import asyncssh

from bungalo.config.endpoints import SFTPEndpoint
from bungalo.logger import LOGGER

# Studio's video formats (editor/core/media.py); it refuses anything else.
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mxf")
FRAMES_PER_VIDEO = 5
FRAME_WIDTH = 320
FRAME_HEIGHT = 180

SCAN_TIMEOUT_SECONDS = 600
FRAME_TIMEOUT_SECONDS = 120

# Non-interactive SSH sessions on macOS don't include Homebrew in PATH.
_PATH_SETUP = "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; "

_PROGRESS_LINE = re.compile(r"^(\w+=\s*\S*|frame=.*|size=.*)$")

_NAME_TESTS = " -o ".join(f"-iname '*{ext}'" for ext in VIDEO_EXTENSIONS)
_SCAN_SCRIPT = rf"""
export LC_ALL=C
newline='
'
root_device=$(stat -f %d /Volumes) || exit 3
for volume in /Volumes/*; do
  [ -d "$volume" ] || continue
  # The boot disk appears here as a symlink to /.
  [ -L "$volume" ] && continue
  # A leftover folder is on the boot disk rather than being its own mount.
  [ "$(stat -f %d "$volume")" = "$root_device" ] && continue
  mounted=$(basename "$volume")
  info=$(diskutil info "$volume" 2>/dev/null)
  # macOS renames the mount point when names conflict, but not the volume.
  label=$(printf '%s\n' "$info" | sed -n 's/^ *Volume Name: *//p' | head -n 1)
  if [ -z "$label" ]; then
    # A network share isn't a disk, but its source still carries its real name.
    label=$(mount | grep -F " on $volume (" | sed -E 's/ on .*//; s#.*/##; s/%20/ /g')
  fi
  for wanted in "$@"; do
    [ "$label" = "$wanted" ] || continue
    uuid=$(printf '%s\n' "$info" | sed -n 's/^ *Volume UUID: *//p' | head -n 1)
    printf 'V\t%s\t%s\t%s\n' "$uuid" "$wanted" "$volume"
    find "$volume" -name '.*' -prune -o -type f ! -name "*$newline*" \( {_NAME_TESTS} \) \
      -exec stat -f 'F%t%z%t%m%t%N' {{}} +
    break
  done
done
exit 0
"""


@dataclass(frozen=True)
class VideoFile:
    relative_path: str
    size: int
    mtime: int

    @property
    def name(self) -> str:
        return PurePosixPath(self.relative_path).name

    @property
    def fingerprint(self) -> str:
        """Identifies this recording, even if a card reuses its file names."""
        return f"{self.relative_path}|{self.size}|{self.mtime}"


@dataclass
class Volume:
    id: str
    name: str
    mount_point: str
    files: list[VideoFile] = field(default_factory=list)

    def path_of(self, video: VideoFile) -> str:
        """Where a video on this volume is mounted right now."""
        return f"{self.mount_point}/{video.relative_path}"


@dataclass
class ScanResult:
    volumes: list[Volume]
    warnings: list[str] = field(default_factory=list)


class BoxCommandError(RuntimeError):
    """A command on the box failed in a way that makes its result unusable."""


class UploadFailed(RuntimeError):
    def __init__(self, reason: str, *, link_closed: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.link_closed = link_closed


def parse_scan(output: str) -> list[Volume]:
    """Read the scan script's output: a V line per volume, then its F lines."""
    volumes: list[Volume] = []
    for line in output.split("\n"):
        if line.startswith("V\t"):
            parts = line.split("\t", 3)
            if len(parts) != 4:
                continue
            _, uuid, name, mount_point = parts
            mounted = PurePosixPath(mount_point).name
            volume_id = f"uuid:{uuid}" if uuid else f"name:{mounted}"
            volumes.append(Volume(id=volume_id, name=name, mount_point=mount_point))
        elif line.startswith("F\t") and volumes:
            parts = line.split("\t", 3)
            if len(parts) != 4 or not parts[1].isdigit() or not parts[2].isdigit():
                continue
            _, size, mtime, path = parts
            prefix = f"{volumes[-1].mount_point}/"
            if path.startswith(prefix):
                volumes[-1].files.append(
                    VideoFile(path.removeprefix(prefix), int(size), int(mtime))
                )
    return volumes


def sample_offsets(duration: float, count: int = FRAMES_PER_VIDEO) -> list[float]:
    """Evenly spaced moments, centred so they miss black frames at either end."""
    return [duration * (index + 0.5) / count for index in range(count)]


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


class Box:
    def __init__(self, connection: Any):
        self.connection = connection

    async def scan(self, volume_names: list[str]) -> ScanResult:
        """Find the videos on those named volumes that are currently mounted."""
        result = await self.connection.run(
            shlex.join(["/bin/sh", "-s", "--", *volume_names]),
            input=_SCAN_SCRIPT.encode(),
            encoding=None,
            check=False,
            timeout=SCAN_TIMEOUT_SECONDS,
        )
        stderr = _text(result.stderr).strip()
        if result.exit_status != 0:
            raise BoxCommandError(
                f"Volume scan exited with {result.exit_status}: {stderr}"
            )
        return ScanResult(
            volumes=parse_scan(_text(result.stdout)),
            warnings=stderr.splitlines()[:3],
        )

    async def sample_frames(self, path: str) -> tuple[float | None, list[bytes | None]]:
        """Duration and evenly spaced JPEGs; a frame that can't be read is None."""
        duration = await self._duration(path)
        if duration is None:
            return None, [None] * FRAMES_PER_VIDEO
        return duration, [
            await self._frame(path, offset) for offset in sample_offsets(duration)
        ]

    async def upload(self, path: str, link: str) -> None:
        """
        Send a file from the box to a Studio upload link. The link is the
        credential, so curl reads it from stdin to keep it out of `ps`.
        """
        target = f"{link.rstrip('/')}/{quote(PurePosixPath(path).name, safe='')}"
        command = [
            "curl",
            "--config",
            "-",
            "--silent",
            "--show-error",
            "--globoff",
            "--connect-timeout",
            "30",
            # Give up on a stalled transfer: under 1 KB/s for ten minutes.
            "--speed-limit",
            "1024",
            "--speed-time",
            "600",
            "--upload-file",
            path,
            "--write-out",
            "\n%{http_code}",
        ]
        result = await self.connection.run(
            _PATH_SETUP + shlex.join(command),
            input=f'url = "{target}"\n'.encode(),
            encoding=None,
            check=False,
        )
        if result.exit_status != 0:
            raise UploadFailed(
                _redact(_text(result.stderr).strip(), link)
                or f"curl exited with {result.exit_status}"
            )
        body, _, status = _text(result.stdout).rpartition("\n")
        if status != "200":
            raise UploadFailed(
                _redact(_studio_error(body, status), link), link_closed=status == "404"
            )

    @asynccontextmanager
    async def transcoded(
        self, source: str, output_name: str, template: str, work_dir: str
    ) -> AsyncIterator[str]:
        """
        Run a transcode template on the box, yielding the path of its output.
        The output is deleted on leaving the context, whatever happened.
        """
        work = await self._make_work_dir(work_dir)
        try:
            output = f"{work}/{output_name}"
            await self._run_template(source, output, template)
            yield output
        finally:
            await self._remove_work_dir(work)

    async def _make_work_dir(self, work_dir: str) -> str:
        # Reclaim what a crash left behind, but never a folder that could be in use.
        # The leading dot keeps scans from reading one placed on a listed volume.
        root = shlex.quote(work_dir)
        script = (
            f"mkdir -p {root} && find {root} -maxdepth 1 -type d "
            f"-name '.bungalo-transcode.*' -mtime +2 -exec rm -rf {{}} + "
            f"&& mktemp -d {root}/.bungalo-transcode.XXXXXX"
        )
        result = await self.connection.run(
            f"/bin/sh -c {shlex.quote(script)}", encoding=None, check=False
        )
        if result.exit_status != 0:
            raise UploadFailed(
                f"Couldn't create a transcode folder: {_text(result.stderr).strip()}"
            )
        return _text(result.stdout).strip()

    async def _run_template(self, source: str, output: str, template: str) -> None:
        # ffmpeg reads stdin unless told not to, so give it nothing to wait on.
        result = await self.connection.run(
            f"{_PATH_SETUP}export INPUT={shlex.quote(source)} "
            f"OUTPUT={shlex.quote(output)}; "
            f"/bin/sh -e -c {shlex.quote(template)} </dev/null",
            encoding=None,
            check=False,
        )
        if result.exit_status != 0:
            raise UploadFailed(f"Transcode failed: {_transcode_error(result.stderr)}")
        check = await self.connection.run(
            f"test -s {shlex.quote(output)}", encoding=None, check=False
        )
        if check.exit_status != 0:
            raise UploadFailed("Transcode finished without writing $OUTPUT")

    async def _remove_work_dir(self, work: str) -> None:
        try:
            result = await self.connection.run(
                f"rm -rf {shlex.quote(work)}", encoding=None, check=False
            )
            removed = result.exit_status == 0
        except (asyncssh.Error, OSError):
            removed = False
        if not removed:
            # Never raised: it would replace the upload's own result.
            LOGGER.warning("Couldn't remove transcode folder %s", work)

    async def _duration(self, path: str) -> float | None:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ]
        try:
            result = await self.connection.run(
                _PATH_SETUP + shlex.join(command),
                encoding=None,
                check=False,
                timeout=FRAME_TIMEOUT_SECONDS,
            )
        except asyncssh.TimeoutError:
            return None
        if result.exit_status == 127:
            raise BoxCommandError("ffprobe is not installed on the box")
        try:
            duration = float(_text(result.stdout).strip())
        except ValueError:
            return None
        return duration if duration > 0 else None

    async def _frame(self, path: str, offset: float) -> bytes | None:
        scale = (
            f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=decrease"
        )
        command = [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            f"{offset:.3f}",
            "-i",
            path,
            "-frames:v",
            "1",
            "-vf",
            scale,
            "-q:v",
            "5",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-",
        ]
        try:
            result = await self.connection.run(
                _PATH_SETUP + shlex.join(command),
                encoding=None,
                check=False,
                timeout=FRAME_TIMEOUT_SECONDS,
            )
        except asyncssh.TimeoutError:
            return None
        if result.exit_status == 127:
            raise BoxCommandError("ffmpeg is not installed on the box")
        if result.exit_status != 0 or not result.stdout:
            return None
        return result.stdout


def read_transcode_template(path: str) -> str:
    """
    The contents of a transcode template.

    :raises OSError: if the file can't be read
    :raises ValueError: if it doesn't use both of the required variables
    """
    template = Path(path).expanduser().read_text()
    for variable in ("INPUT", "OUTPUT"):
        if not re.search(rf"\$\{{?{variable}\b", template):
            raise ValueError(f"{path} doesn't use ${variable}")
    return template


def _transcode_error(stderr: Any) -> str:
    """The end of a failed command's output, without ffmpeg's progress lines."""
    lines = [
        line.strip()
        for line in _text(stderr).replace("\r", "\n").split("\n")
        if line.strip() and not _PROGRESS_LINE.match(line.strip())
    ]
    return " / ".join(lines[-3:])[:500] or "no error output"


def _redact(text: str, link: str) -> str:
    """Remove an upload link's token, which is a credential, from text."""
    return text.replace(link.rsplit("/", 1)[-1], "[upload link]")


def _studio_error(body: str, status: str) -> str:
    try:
        detail = json.loads(body).get("detail")
    except (ValueError, AttributeError):
        detail = None
    return detail if isinstance(detail, str) else f"Studio answered HTTP {status}"


@asynccontextmanager
async def connect_box(endpoint: SFTPEndpoint) -> AsyncIterator[Box]:
    """Open an SSH session to the box, verifying its host key."""
    async with asyncssh.connect(
        endpoint.host,
        port=endpoint.port,
        username=endpoint.username,
        client_keys=[os.path.expanduser(endpoint.key_file)],
        known_hosts=os.path.expanduser(endpoint.known_hosts_file),
        connect_timeout=15,
        # Uploads can run for hours without any output on the channel.
        keepalive_interval=30,
    ) as connection:
        yield Box(connection)
