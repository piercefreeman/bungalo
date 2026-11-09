"""
Everything that happens on the machine holding the footage. Commands run over
SSH so video never travels through Bungalo: frames come back as small JPEGs,
and uploads go straight from the box to Studio with curl.
"""

import asyncio
import json
import os
import re
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, AsyncContextManager, Literal
from urllib.parse import quote

import asyncssh
from pydantic import BaseModel, ValidationError

from bungalo.config.endpoints import SFTPEndpoint
from bungalo.logger import LOGGER
from bungalo.media_ingest.state import UploadSessionID

# Studio's video formats (editor/core/media.py); it refuses anything else.
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mxf")
FRAMES_PER_VIDEO = 5
FRAME_WIDTH = 320
FRAME_HEIGHT = 180

SCAN_TIMEOUT_SECONDS = 600
FRAME_TIMEOUT_SECONDS = 120

# Studio's upload script sends this many parts at once.
PARALLEL_PARTS = 4
STUDIO_ATTEMPTS = 11
# How often to read a running transcode's progress file.
PROGRESS_SECONDS = 15.0
RETRY_DELAY_SECONDS = 1.0
MAX_RETRY_DELAY_SECONDS = 60.0
# Answers that say to try again. A 429 isn't one: Studio sends it when too many
# uploads are open, and the person who asked should hear that.
_RETRYABLE_STATUSES = {408, 500, 502, 503, 504}
# curl's exit statuses for failing to resolve or connect: nothing was sent.
_NEVER_SENT = {6, 7}
# Higher than any exit status curl uses.
_READ_FAILED = 125

StudioMethod = Literal["GET", "POST", "DELETE"]

_UUID_PREFIX = "uuid:"

# Non-interactive SSH sessions on macOS don't include Homebrew in PATH.
_PATH_SETUP = "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; "

_PROGRESS_LINE = re.compile(r"^(\w+=\s*\S*|frame=.*|size=.*)$")

# The link is the credential, so each URL goes to curl on stdin to stay out of `ps`.
_CURL = [
    "curl",
    "--config",
    "-",
    "--silent",
    "--show-error",
    "--globoff",
    "--connect-timeout",
    "30",
    "--write-out",
    "\n%{http_code}",
]

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


class StudioUpload(BaseModel):
    """A file open on Studio, and what it still needs."""

    upload_id: UploadSessionID
    size_bytes: int
    part_bytes: int
    missing: list[int]


@dataclass(frozen=True)
class TranscodeProgress:
    encoded_seconds: float
    duration: float | None
    speed: float | None
    """Seconds of video encoded per second of real time."""


TranscodeReporter = Callable[[TranscodeProgress], Awaitable[None]]
UploadReporter = Callable[[int, int], Awaitable[None]]
"""Called with the bytes Studio has so far and the file's total."""


class UploadFailed(RuntimeError):
    def __init__(self, reason: str, *, link_closed: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.link_closed = link_closed


def has_stable_id(volume_id: str) -> bool:
    """Whether a volume is known by its UUID, not just the name it's mounted under."""
    return volume_id.startswith(_UUID_PREFIX)


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
            volume_id = f"{_UUID_PREFIX}{uuid}" if uuid else f"name:{mounted}"
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


def _decode(output: bytes) -> str:
    """Command output, which can hold file names in any encoding."""
    return output.decode("utf-8", "replace")


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
        stderr = _decode(result.stderr).strip()
        if result.exit_status != 0:
            raise BoxCommandError(
                f"Volume scan exited with {result.exit_status}: {stderr}"
            )
        return ScanResult(
            volumes=parse_scan(_decode(result.stdout)),
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

    async def open_upload(
        self, path: str, link: str, resume_id: UploadSessionID | None = None
    ) -> StudioUpload:
        """
        Start a file on Studio, or pick `resume_id` back up if Studio still has
        it for a file this size. Only resume an upload of these exact bytes:
        Studio checks the size of each part, not its contents.
        """
        size, _ = await self._file_version(path)
        if resume_id:
            status, body = await self._call(link, "GET", f"/uploads/{resume_id}")
            if status == 200:
                upload = _parse_upload(body)
                if upload.size_bytes == size:
                    return upload
            elif status not in (404, 410):
                raise _refused(status, body, link)
        name = quote(PurePosixPath(path).name, safe="")
        status, body = await self._call(
            link,
            "POST",
            f"/uploads?filename={name}&size_bytes={size}",
            repeatable=False,
        )
        if status != 200:
            # Only here does a 404 mean the link: elsewhere it can be one upload.
            raise _refused(status, body, link, link_closed=status == 404)
        return _parse_upload(body)

    async def send_upload(
        self,
        path: str,
        link: str,
        upload: StudioUpload,
        on_progress: UploadReporter | None = None,
    ) -> None:
        """
        Send what an open upload is missing, then assemble it on Studio. A file
        that changes along the way is discarded rather than assembled from a
        mix of both versions.
        """
        version = await self._file_version(path)
        await self._send_parts(path, link, upload, on_progress)
        if await self._file_version(path) != version:
            await self._cancel(link, upload)
            raise UploadFailed("The file changed while it was uploading")
        await self._complete(link, upload)

    async def upload(
        self, path: str, link: str, on_progress: UploadReporter | None = None
    ) -> None:
        """Send a file that won't exist to resume, discarding it on Studio if it fails."""
        upload = await self.open_upload(path, link)
        try:
            await self._send_parts(path, link, upload, on_progress)
        except UploadFailed:
            await self._cancel(link, upload)
            raise
        await self._complete(link, upload)

    @asynccontextmanager
    async def transcoded(
        self,
        source: str,
        output_name: str,
        template: str,
        work_dir: str,
        gate: AsyncContextManager[None],
        on_progress: TranscodeReporter | None = None,
    ) -> AsyncIterator[str]:
        """
        Run a transcode template on the box, yielding the path of its output.
        Only the transcode itself runs inside `gate`. The output is deleted on
        leaving the context, whatever happened.
        """
        work = await self._make_work_dir(work_dir)
        try:
            output = f"{work}/{output_name}"
            async with gate:
                await self._run_template(source, output, template, on_progress)
            yield output
        finally:
            await self._remove_work_dir(work)

    async def delete(self, volume: Volume, video: VideoFile) -> None:
        """
        Permanently remove a video, checking on the box in the same command that
        the disk and the file are still the ones expected.

        :raises BoxCommandError: if they aren't, or it couldn't be removed
        """
        if not has_stable_id(volume.id):
            raise BoxCommandError("This volume can't be identified reliably")
        mount = shlex.quote(volume.mount_point)
        path = shlex.quote(volume.path_of(video))
        expected_uuid = shlex.quote(volume.id.removeprefix(_UUID_PREFIX))
        script = (
            f"uuid=$(diskutil info {mount} 2>/dev/null "
            "| sed -n 's/^ *Volume UUID: *//p' | head -n 1)\n"
            f'[ "$uuid" = {expected_uuid} ] '
            '|| { echo "A different disk is mounted there now" >&2; exit 1; }\n'
            f'[ "$(stat -f "%z %m" {path})" = "{video.size} {video.mtime}" ] '
            '|| { echo "The file changed after it was checked" >&2; exit 1; }\n'
            f"rm -- {path}\n"
        )
        result = await self.connection.run(
            f"/bin/sh -c {shlex.quote(script)}", encoding=None, check=False
        )
        if result.exit_status != 0:
            raise BoxCommandError(
                _decode(result.stderr).strip() or f"rm exited with {result.exit_status}"
            )

    async def _file_version(self, path: str) -> tuple[int, int]:
        """A file's size and modification time."""
        result = await self.connection.run(
            f"stat -f '%z %m' {shlex.quote(path)}", encoding=None, check=False
        )
        fields = _decode(result.stdout).split()
        if result.exit_status != 0 or len(fields) != 2:
            raise UploadFailed("Couldn't read the file's details on the box")
        return int(fields[0]), int(fields[1])

    async def _send_parts(
        self,
        path: str,
        link: str,
        upload: StudioUpload,
        on_progress: UploadReporter | None = None,
    ) -> None:
        remaining = iter(upload.missing)

        def length(number: int) -> int:
            return min(
                upload.part_bytes, upload.size_bytes - (number - 1) * upload.part_bytes
            )

        sent = upload.size_bytes - sum(length(number) for number in upload.missing)
        if on_progress:
            await on_progress(sent, upload.size_bytes)

        async def send_remaining() -> None:
            nonlocal sent
            # A shared iterator hands each part to exactly one sender.
            for number in remaining:
                await self._send_part(path, link, upload, number)
                sent += length(number)
                if on_progress:
                    await on_progress(sent, upload.size_bytes)

        results = await asyncio.gather(
            *(send_remaining() for _ in range(PARALLEL_PARTS)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _send_part(
        self, path: str, link: str, upload: StudioUpload, number: int
    ) -> None:
        # A part is copied out first so curl sends it with a known length.
        curl = shlex.join(
            [*_CURL, "--speed-limit", "1024", "--speed-time", "60", "--upload-file"]
        )
        script = (
            f'slice=$(mktemp "${{TMPDIR:-/tmp}}/bungalo-part.XXXXXX") '
            f"|| exit {_READ_FAILED}\n"
            "trap 'rm -f \"$slice\"' EXIT\n"
            f'dd if={shlex.quote(path)} of="$slice" bs={upload.part_bytes} '
            f"skip={number - 1} count=1 2>/dev/null || exit {_READ_FAILED}\n"
            f'{curl} "$slice"\n'
        )
        status, body = await self._request(
            f"/bin/sh -c {shlex.quote(script)}",
            link,
            f"/uploads/{upload.upload_id}/parts/{number}",
        )
        if status != 204:
            raise _refused(status, body, link)

    async def _complete(self, link: str, upload: StudioUpload) -> None:
        status, body = await self._call(
            link, "POST", f"/uploads/{upload.upload_id}/complete"
        )
        if status != 200:
            raise _refused(status, body, link)

    async def _cancel(self, link: str, upload: StudioUpload) -> None:
        try:
            await self._call(link, "DELETE", f"/uploads/{upload.upload_id}")
        except (UploadFailed, asyncssh.Error, OSError):
            # Never raised: it would replace the upload's own failure.
            LOGGER.warning("Couldn't cancel upload %s on Studio", upload.upload_id)

    async def _call(
        self,
        link: str,
        method: StudioMethod,
        suffix: str,
        *,
        repeatable: bool = True,
    ) -> tuple[int, str]:
        command = shlex.join([*_CURL, "--max-time", "600", "--request", method])
        return await self._request(command, link, suffix, repeatable=repeatable)

    async def _request(
        self, command: str, link: str, suffix: str, *, repeatable: bool = True
    ) -> tuple[int, str]:
        """
        Run a curl command against a Studio link until Studio gives a real
        answer. A request that isn't `repeatable` is only tried again when it
        can't have been sent, since Studio would act on it twice.
        """
        config = f'url = "{link.rstrip("/")}{suffix}"\n'.encode()
        attempt = 1
        while True:
            result = await self.connection.run(
                _PATH_SETUP + command, input=config, encoding=None, check=False
            )
            if result.exit_status == _READ_FAILED:
                raise UploadFailed("Couldn't read the file on the box")
            body, _, code = _decode(result.stdout).rpartition("\n")
            if result.exit_status == 0 and int(code) not in _RETRYABLE_STATUSES:
                return int(code), body
            sent = result.exit_status not in _NEVER_SENT
            if attempt == STUDIO_ATTEMPTS or (sent and not repeatable):
                if result.exit_status == 0:
                    raise _refused(int(code), body, link)
                raise UploadFailed(
                    _redact(_decode(result.stderr).strip(), link)
                    or f"curl exited with {result.exit_status}"
                )
            delay = RETRY_DELAY_SECONDS * 2 ** (attempt - 1)
            await asyncio.sleep(min(delay, MAX_RETRY_DELAY_SECONDS))
            attempt += 1

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
                f"Couldn't create a transcode folder: {_decode(result.stderr).strip()}"
            )
        return _decode(result.stdout).strip()

    async def _run_template(
        self,
        source: str,
        output: str,
        template: str,
        on_progress: TranscodeReporter | None = None,
    ) -> None:
        progress = f"{PurePosixPath(output).parent}/.progress"
        watching = (
            asyncio.create_task(self._watch_transcode(source, progress, on_progress))
            if on_progress
            else None
        )
        try:
            # ffmpeg reads stdin unless told not to, so give it nothing to wait on.
            result = await self.connection.run(
                f"{_PATH_SETUP}export INPUT={shlex.quote(source)} "
                f"OUTPUT={shlex.quote(output)} "
                f"PROGRESS={shlex.quote(progress)}; "
                f"/bin/sh -e -c {shlex.quote(template)} </dev/null",
                encoding=None,
                check=False,
            )
        finally:
            if watching:
                watching.cancel()
                await asyncio.gather(watching, return_exceptions=True)
        if result.exit_status != 0:
            raise UploadFailed(f"Transcode failed: {_transcode_error(result.stderr)}")
        check = await self.connection.run(
            f"test -s {shlex.quote(output)}", encoding=None, check=False
        )
        if check.exit_status != 0:
            raise UploadFailed("Transcode finished without writing $OUTPUT")

    async def _watch_transcode(
        self, source: str, path: str, report: TranscodeReporter
    ) -> None:
        """Pass on what ffmpeg writes to the progress file, until cancelled."""
        duration: float | None = None
        # Without a length it can still say how much has been encoded.
        try:
            duration = await self._duration(source)
        except (BoxCommandError, asyncssh.Error, OSError) as error:
            LOGGER.warning("Couldn't read the transcode's length: %s", error)
        while True:
            await asyncio.sleep(PROGRESS_SECONDS)
            # Only a progress line is lost if this fails, so never stop the encode.
            try:
                result = await self.connection.run(
                    f"tail -n 30 {shlex.quote(path)}", encoding=None, check=False
                )
                progress = (
                    parse_transcode_progress(_decode(result.stdout), duration)
                    if result.exit_status == 0
                    else None
                )
                if progress:
                    await report(progress)
            except (asyncssh.Error, OSError, ValueError) as error:
                LOGGER.warning("Couldn't read transcode progress: %s", error)

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
            duration = float(_decode(result.stdout).strip())
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


def parse_transcode_progress(
    text: str, duration: float | None
) -> TranscodeProgress | None:
    """The newest reading in ffmpeg's `-progress` output, or None before its first."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        # A value is "N/A" until ffmpeg has one, so keep the last real reading.
        if separator and value.strip() != "N/A":
            values[key.strip()] = value.strip()
    try:
        encoded = int(values["out_time_us"]) / 1_000_000
    except (KeyError, ValueError):
        return None
    try:
        speed = float(values.get("speed", "").removesuffix("x"))
    except ValueError:
        speed = None
    return TranscodeProgress(
        encoded_seconds=encoded, duration=duration, speed=speed or None
    )


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


def _transcode_error(stderr: bytes) -> str:
    """The end of a failed command's output, without ffmpeg's progress lines."""
    lines = [
        line.strip()
        for line in _decode(stderr).replace("\r", "\n").split("\n")
        if line.strip() and not _PROGRESS_LINE.match(line.strip())
    ]
    return " / ".join(lines[-3:])[:500] or "no error output"


def _redact(text: str, link: str) -> str:
    """Remove an upload link's token, which is a credential, from text."""
    return text.replace(link.rstrip("/").rsplit("/", 1)[-1], "[upload link]")


def _studio_error(body: str, status: str) -> str:
    try:
        detail = json.loads(body).get("detail")
    except (ValueError, AttributeError):
        detail = None
    return detail if isinstance(detail, str) else f"Studio answered HTTP {status}"


def _refused(
    status: int, body: str, link: str, *, link_closed: bool = False
) -> UploadFailed:
    return UploadFailed(
        _redact(_studio_error(body, str(status)), link), link_closed=link_closed
    )


def _parse_upload(body: str) -> StudioUpload:
    try:
        return StudioUpload.model_validate_json(body)
    except ValidationError as error:
        raise UploadFailed("Studio sent an answer Bungalo couldn't read") from error


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
