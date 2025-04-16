import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, Optional, TypeVar, Union

from bungalo.logger import CONSOLE

T = TypeVar("T")
R = TypeVar("R")


@contextmanager
def mount_smb(
    server: str,
    share: str = "",  # Empty string for root share
    username: str = "",
    password: str = "",
    domain: Optional[str] = None,
    mount_options: Optional[Dict[str, Any]] = None,
    mount_point: Optional[Union[str, Path]] = None,
) -> Generator[Path, None, None]:
    """
    Context manager that temporarily mounts an SMB share and yields the mount path.
    Linux-only implementation using mount.cifs.

    Args:
        server: The SMB server hostname or IP address (e.g., "192.168.1.172")
        share: The name of the share on the server. This is the specific folder being shared.
               For example "backup", "media", "documents", etc. Use empty string for root.
        username: Username for authentication. Use empty string for guest/anonymous access.
        password: Password for authentication. Use empty string for guest/anonymous access.
        domain: Optional Windows domain for authentication (often not needed for home networks)
        mount_options: Optional dictionary of additional mount options
        mount_point: Optional specific mount point, if not provided a temporary directory will be created

    Yields:
        Path object pointing to the mounted directory

    Raises:
        subprocess.CalledProcessError: If mounting or unmounting fails

    Note:
        If you only have an SMB URL like "smb://192.168.1.172", the server is "192.168.1.172"
        and you'll need to know which share to connect to on that server.
    """
    is_temp_dir = mount_point is None

    try:
        if is_temp_dir:
            # Create a temporary directory for mounting
            temp_dir = tempfile.TemporaryDirectory()
            mount_point = temp_dir.name

        mount_path = Path(mount_point)
        os.makedirs(mount_path, exist_ok=True)

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
            str(mount_path),
            "-o",
            options_str,
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        # Yield the mount point
        yield mount_path

    finally:
        # Unmount and clean up
        try:
            if os.path.ismount(str(mount_path)):
                subprocess.run(["umount", str(mount_path)], check=True)
        except Exception as e:
            CONSOLE.print(f"Warning: Failed to unmount {mount_path}: {e}")

        # Clean up the temporary directory if we created one
        if is_temp_dir:
            temp_dir.cleanup()
