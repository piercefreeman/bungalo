import asyncio
import os
from contextlib import ExitStack
from pathlib import Path

from bungalo.backups.nas import mount_smb
from bungalo.config import BungaloConfig
from bungalo.config.endpoints import NASEndpoint
from bungalo.logger import CONSOLE

PLEX_IMAGE = "plexinc/pms-docker:latest"
CONTAINER_NAME = "bungalo-plex"
ENV_PASSTHROUGH = ("PLEX_CLAIM", "PLEX_UID", "PLEX_GID", "TZ")


def _get_root() -> Path:
    """Return the root directory for Plex runtime data."""
    return Path(os.environ.get("BUNGALO_PLEX_ROOT", "~/.bungalo/plex")).expanduser()


def _ensure_directories() -> tuple[Path, Path, Path]:
    """
    Ensure the default directory structure required for Plex exists.

    Returns:
        Tuple of (config_dir, transcode_dir, mount_root).
    """
    root = _get_root()
    config_dir = root / "config"
    transcode_dir = root / "transcode"
    mount_root = root / "mounts"

    config_dir.mkdir(parents=True, exist_ok=True)
    transcode_dir.mkdir(parents=True, exist_ok=True)
    mount_root.mkdir(parents=True, exist_ok=True)
    return (config_dir, transcode_dir, mount_root)


def _resolve_nas_endpoints(config: BungaloConfig) -> dict[str, NASEndpoint]:
    endpoints_by_nickname = {endpoint.nickname: endpoint for endpoint in config.endpoints.nas}
    if not endpoints_by_nickname:
        raise ValueError("No NAS endpoints configured, cannot mount media shares")
    return endpoints_by_nickname


async def _remove_existing_container() -> None:
    """Best-effort removal of an existing Plex container with our managed name."""
    process = await asyncio.create_subprocess_exec(
        "docker",
        "rm",
        "-f",
        CONTAINER_NAME,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.wait()


def _build_env_args() -> list[str]:
    env_args: list[str] = []
    # Always default timezone to UTC if host is not configured.
    timezone = os.environ.get("TZ", "UTC")
    env_args.extend(["-e", f"TZ={timezone}"])

    for var in ENV_PASSTHROUGH:
        if var == "TZ":
            # Already handled above
            continue
        value = os.environ.get(var)
        if value:
            env_args.extend(["-e", f"{var}={value}"])
    return env_args


async def main(config: BungaloConfig) -> None:
    """
    Launch the Plex media server container after mounting configured NAS paths.
    """
    media_config = config.media_server
    if not media_config:
        raise ValueError("Media server config not defined")
    if media_config.plugin != "plex":
        raise ValueError(
            f"Unsupported media server plugin '{media_config.plugin}', expected 'plex'"
        )

    nas_endpoints = _resolve_nas_endpoints(config)
    config_dir, transcode_dir, mount_root = _ensure_directories()

    volume_args: list[str] = [
        "-v",
        f"{config_dir}:/config",
        "-v",
        f"{transcode_dir}:/transcode",
    ]

    with ExitStack() as stack:
        for mount in media_config.mounts:
            endpoint = nas_endpoints.get(mount.path.endpoint_nickname)
            if not endpoint:
                raise ValueError(
                    f"NAS endpoint '{mount.path.endpoint_nickname}' "
                    "referenced by media server mount is not configured"
                )

            password = endpoint.password.get_secret_value()
            mount_point = mount_root / mount.name
            mount_point.mkdir(parents=True, exist_ok=True)

            CONSOLE.print(
                f"Mounting NAS share '{endpoint.nickname}:{mount.path.drive_name}' "
                f"for media mount '{mount.name}'"
            )
            mounted_path = stack.enter_context(
                mount_smb(
                    server=endpoint.ip_address,
                    share=mount.path.drive_name,
                    username=endpoint.username,
                    password=password,
                    domain=endpoint.domain,
                    mount_point=mount_point,
                )
            )

            relative_path = mount.path.path.strip("/")
            local_media_path = mounted_path / relative_path if relative_path else mounted_path
            container_path = mount.container_path or f"/data/{mount.name}"

            if not local_media_path.exists():
                raise FileNotFoundError(
                    f"Mounted path '{local_media_path}' does not exist for media mount '{mount.name}'"
                )

            volume_spec = f"{local_media_path}:{container_path}:ro"
            volume_args.extend(["-v", volume_spec])
            CONSOLE.print(
                f"Exposing '{local_media_path}' to Plex at '{container_path}'"
            )

        env_args = _build_env_args()
        docker_cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            CONTAINER_NAME,
            "--network",
            "host",
            *env_args,
            *volume_args,
            PLEX_IMAGE,
        ]

        CONSOLE.print(
            f"Starting Plex container '{CONTAINER_NAME}' with image '{PLEX_IMAGE}'"
        )
        await _remove_existing_container()
        process = await asyncio.create_subprocess_exec(*docker_cmd)
        returncode = await process.wait()

        if returncode:
            raise RuntimeError(f"Plex container exited with code {returncode}")
