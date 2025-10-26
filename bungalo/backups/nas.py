import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, TypeVar

from bungalo.logger import CONSOLE

T = TypeVar("T")
R = TypeVar("R")


@contextmanager
def mount_smb(
    server: str,
    share: str = "",  # Empty string for root share
    username: str = "",
    password: str = "",
    domain: str | None = None,
    mount_options: dict[str, Any] | None = None,
    mount_point: str | Path | None = None,
) -> Generator[Path, None, None]:
    """
    Context manager that temporarily mounts an SMB share and yields the mount path.
    Linux-only implementation using mount.cifs.

    :param server: The SMB server hostname or IP address (e.g., "192.168.1.172")
    :param share: The name of the share on the server. This is the specific folder being shared.
        For example "backup", "media", "documents", etc. Use empty string for root.
    :param username: Username for authentication. Use empty string for guest/anonymous access.
    :param password: Password for authentication. Use empty string for guest/anonymous access.
    :param domain: Optional Windows domain for authentication (often not needed for home networks)
    :param mount_options: Optional dictionary of additional mount options
    :param mount_point: Optional specific mount point, if not provided a temporary directory will be created

    :yields: Path object pointing to the mounted directory

    :raises: subprocess.CalledProcessError: If mounting or unmounting fails

    Note:
        If you only have an SMB URL like "smb://192.168.1.172", the server is "192.168.1.172"
        and you'll need to know which share to connect to on that server.

    """
    # Fallback to a temporary directory if no mount point is provided
    temp_dir: tempfile.TemporaryDirectory | None = None
    mount_dir: Path
    if mount_point is None:
        temp_dir = tempfile.TemporaryDirectory(delete=False)
        mount_dir = Path(temp_dir.name)
    else:
        mount_dir = Path(mount_point)

    mount_dir.mkdir(parents=True, exist_ok=True)

    share_display = share or "(root)"
    CONSOLE.print(
        f"Attempting to mount SMB share '//{server}/{share_display}' at '{mount_dir}'"
    )

    mounted = False

    try:
        # Prepare mount options for Linux
        options_str = "vers=3.0"  # Default to SMB3 protocol

        if username:
            options_str += f",username={username}"
            if password:
                options_str += f",password={password}"
        else:
            options_str += ",guest"

        if domain:
            options_str += f",domain={domain}"

        # Add any additional options
        if mount_options:
            for key, value in mount_options.items():
                options_str += f",{key}={value}"

        # Prepare and execute mount.cifs command
        cmd = [
            "mount",
            "-t",
            "cifs",
            f"//{server}/{share}",
            str(mount_dir),
            "-o",
            options_str,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        mounted = True
        CONSOLE.print(
            f"Mounted SMB share '//{server}/{share_display}' at '{mount_dir}'"
        )

        # Yield the mount point
        yield mount_dir

    except subprocess.CalledProcessError as exc:
        stdout = exc.stdout.decode("utf-8", errors="ignore").strip() if exc.stdout else ""
        stderr = exc.stderr.decode("utf-8", errors="ignore").strip() if exc.stderr else ""
        detail = stderr or stdout or str(exc)
        CONSOLE.print(
            f"Error mounting SMB share '//{server}/{share_display}' at '{mount_dir}': {detail}"
        )
        raise
    finally:
        # Unmount and clean up
        try:
            if mounted and mount_dir.is_mount():
                subprocess.run(["umount", str(mount_dir)], check=True)
                CONSOLE.print(
                    f"Unmounted SMB share '//{server}/{share_display}' from '{mount_dir}'"
                )
        except Exception as e:
            CONSOLE.print(f"Warning: Failed to unmount {mount_dir}: {e}")
