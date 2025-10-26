import asyncio
import os
from contextlib import ExitStack
from pathlib import Path

from bungalo.app_manager import AppManager
from bungalo.backups.nas import mount_smb
from bungalo.config import BungaloConfig
from bungalo.config.endpoints import NASEndpoint
from bungalo.logger import CONSOLE
from bungalo.slack import SlackClient

PLEX_IMAGE = "plexinc/pms-docker:latest"
CONTAINER_NAME = "bungalo-plex"
ENV_PASSTHROUGH = ("PLEX_CLAIM", "PLEX_UID", "PLEX_GID", "TZ")


def _get_root() -> Path:
    """Return the root directory for Plex runtime data."""
    return Path(os.environ.get("BUNGALO_PLEX_ROOT", "~/.bungalo/plex")).expanduser()


def _ensure_directories() -> tuple[Path, Path]:
    """
    Ensure the default directory structure required for Plex exists.

    Returns:
        Tuple of (config_dir, mount_root).
    """
    root = _get_root()
    config_dir = root / "config"
    mount_root = root / "mounts"

    config_dir.mkdir(parents=True, exist_ok=True)
    mount_root.mkdir(parents=True, exist_ok=True)
    return config_dir, mount_root


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
    app_manager = AppManager.get()
    service_name = "plex"
    slack_client = SlackClient(
        app_token=config.slack.app_token,
        bot_token=config.slack.bot_token,
        channel_id=config.slack.channel,
    )

    media_config = config.media_server
    if not media_config:
        raise ValueError("Media server config not defined")
    if media_config.plugin != "plex":
        raise ValueError(
            f"Unsupported media server plugin '{media_config.plugin}', expected 'plex'"
        )

    nas_endpoints = _resolve_nas_endpoints(config)
    config_dir, mount_root = _ensure_directories()

    volume_args: list[str] = [
        "-v",
        f"{config_dir}:/config",
    ]

    with ExitStack() as stack:
        transcode_endpoint = nas_endpoints.get(media_config.transcode.endpoint_nickname)
        if not transcode_endpoint:
            raise ValueError(
                f"NAS endpoint '{media_config.transcode.endpoint_nickname}' "
                "referenced by transcode path is not configured"
            )

        transcode_mount_point = mount_root / "transcode"
        transcode_mount_point.mkdir(parents=True, exist_ok=True)

        transcode_password = transcode_endpoint.password.get_secret_value()
        transcode_mount = stack.enter_context(
            mount_smb(
                server=transcode_endpoint.ip_address,
                share=media_config.transcode.drive_name,
                username=transcode_endpoint.username,
                password=transcode_password,
                domain=transcode_endpoint.domain,
                mount_point=transcode_mount_point,
            )
        )

        transcode_relative = media_config.transcode.path.strip("/")
        transcode_local_path = (
            transcode_mount / transcode_relative if transcode_relative else transcode_mount
        )
        transcode_local_path.mkdir(parents=True, exist_ok=True)

        volume_args.extend(
            [
                "-v",
                f"{transcode_local_path}:/transcode",
            ]
        )

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
        await app_manager.update_service(
            service_name,
            state="running",
            detail="Starting Plex media server container",
        )
        process = await asyncio.create_subprocess_exec(*docker_cmd)
        await app_manager.update_service(
            service_name,
            state="running",
            detail="Plex media server running",
        )
        plex_host = (
            os.environ.get("PLEX_EXTERNAL_HOST")
            or (
                f"http://{config.root.self_ip}:32400/web"
                if config.root.self_ip
                else "http://127.0.0.1:32400/web"
            )
        )
        await slack_client.create_status(
            f"Plex is now running → {plex_host}"
        )
        returncode = await process.wait()

        if returncode:
            await app_manager.update_service(
                service_name,
                state="error",
                detail=f"Plex container exited with code {returncode}",
            )
            raise RuntimeError(f"Plex container exited with code {returncode}")
        await app_manager.update_service(
            service_name,
            state="completed",
            detail="Plex container stopped",
        )
