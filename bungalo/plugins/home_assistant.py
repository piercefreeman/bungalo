import asyncio
import os
import shutil
from importlib import resources
from pathlib import Path

from bungalo.app_manager import AppManager
from bungalo.config import BungaloConfig
from bungalo.logger import CONSOLE
from bungalo.slack import SlackClient

HOME_ASSISTANT_IMAGE = "ghcr.io/home-assistant/home-assistant:stable"
CONTAINER_NAME = "bungalo-home-assistant"
DOCKER_READY_TIMEOUT = 60  # seconds


def _get_root() -> Path:
    """Return the root directory for Home Assistant runtime data."""
    return Path(
        os.environ.get("BUNGALO_HOME_ASSISTANT_ROOT", "~/.bungalo/home_assistant")
    ).expanduser()


def _ensure_directories() -> Path:
    """
    Ensure the default directory structure required for Home Assistant exists.

    Returns:
        The config directory path.
    """
    root = _get_root()
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def _seed_default_config(config_dir: Path) -> None:
    """
    Copy seed configuration files into the Home Assistant config directory.

    Only writes files that do not already exist, so user customizations
    are never overwritten.
    """
    seed_package = resources.files("bungalo.plugins.home_assistant_seed")

    for item in seed_package.iterdir():
        if not item.name.endswith(".yaml"):
            continue

        dest = config_dir / item.name
        if dest.exists():
            CONSOLE.print(
                f"Skipping seed file '{item.name}' — already exists at '{dest}'"
            )
            continue

        with resources.as_file(item) as src_path:
            shutil.copy2(src_path, dest)
        CONSOLE.print(f"Seeded default config '{item.name}' → '{dest}'")


async def _ensure_docker_ready() -> None:
    """
    Ensure the inner Docker daemon is ready to accept commands.

    The entrypoint script should have already started dockerd, but this provides
    an additional safety check in case home_assistant is run independently.
    """
    CONSOLE.print("Verifying Docker daemon is ready...")

    for attempt in range(DOCKER_READY_TIMEOUT):
        process = await asyncio.create_subprocess_exec(
            "docker",
            "info",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        returncode = await process.wait()

        if returncode == 0:
            CONSOLE.print("Docker daemon is ready!")
            return

        if attempt == 0:
            CONSOLE.print("Docker daemon not yet ready, waiting...")

        await asyncio.sleep(1)

    raise RuntimeError(
        f"Docker daemon not ready after {DOCKER_READY_TIMEOUT}s. "
        "Ensure the container is running with --privileged and dockerd is started."
    )


async def _remove_existing_container() -> None:
    """Best-effort removal of an existing Home Assistant container with our managed name."""
    process = await asyncio.create_subprocess_exec(
        "docker",
        "rm",
        "-f",
        CONTAINER_NAME,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.wait()


async def main(config: BungaloConfig) -> None:
    """
    Launch the Home Assistant container via Docker-in-Docker.

    Home Assistant runs on host network with a persistent config volume.
    """
    app_manager = AppManager.get()
    service_name = "home_assistant"

    ha_config = config.home_assistant
    AppManager.register_port_check("home_assistant", ha_config.port)

    slack_client = SlackClient(
        app_token=config.slack.app_token,
        bot_token=config.slack.bot_token,
        channel_id=config.slack.channel,
    )
    if not ha_config.enabled:
        raise ValueError("Home Assistant is not enabled in config")

    await _ensure_docker_ready()

    config_dir = _ensure_directories()
    _seed_default_config(config_dir)

    timezone = os.environ.get("TZ", "UTC")

    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        CONTAINER_NAME,
        "--network",
        "host",
        "--privileged",
        "-e",
        f"TZ={timezone}",
        "-v",
        f"{config_dir}:/config",
        HOME_ASSISTANT_IMAGE,
    ]

    CONSOLE.print(f"Pulling Home Assistant image '{HOME_ASSISTANT_IMAGE}'")
    await app_manager.update_service(
        service_name,
        state="pulling",
        detail=f"Pulling image {HOME_ASSISTANT_IMAGE}",
    )
    pull_process = await asyncio.create_subprocess_exec(
        "docker",
        "pull",
        HOME_ASSISTANT_IMAGE,
    )
    pull_rc = await pull_process.wait()
    if pull_rc:
        await app_manager.update_service(
            service_name,
            state="error",
            detail=f"Failed to pull image {HOME_ASSISTANT_IMAGE} (exit code {pull_rc})",
        )
        raise RuntimeError(f"Failed to pull Home Assistant image (exit code {pull_rc})")

    CONSOLE.print(f"Starting Home Assistant container '{CONTAINER_NAME}'")
    await _remove_existing_container()
    await app_manager.update_service(
        service_name,
        state="running",
        detail="Home Assistant running",
    )
    process = await asyncio.create_subprocess_exec(*docker_cmd)

    ha_port = ha_config.port
    ha_host = os.environ.get("HOME_ASSISTANT_EXTERNAL_HOST") or (
        f"http://{config.root.self_ip}:{ha_port}"
        if config.root.self_ip
        else f"http://127.0.0.1:{ha_port}"
    )
    await slack_client.create_status(f"Home Assistant is now running → {ha_host}")
    returncode = await process.wait()

    if returncode:
        await app_manager.update_service(
            service_name,
            state="error",
            detail=f"Home Assistant container exited with code {returncode}",
        )
        raise RuntimeError(f"Home Assistant container exited with code {returncode}")
    await app_manager.update_service(
        service_name,
        state="completed",
        detail="Home Assistant container stopped",
    )
