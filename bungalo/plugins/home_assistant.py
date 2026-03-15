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
MATTER_SERVER_IMAGE = "ghcr.io/matter-js/python-matter-server:stable"
CONTAINER_NAME = "bungalo-home-assistant"
MATTER_CONTAINER_NAME = "bungalo-matter-server"
DOCKER_READY_TIMEOUT = 60  # seconds


def _get_root() -> Path:
    """Return the root directory for Home Assistant runtime data."""
    return Path(
        os.environ.get("BUNGALO_HOME_ASSISTANT_ROOT", "~/.bungalo/home_assistant")
    ).expanduser()


def _ensure_directories() -> tuple[Path, Path]:
    """
    Ensure the default directory structure required for Home Assistant exists.

    Returns:
        Tuple of (config_dir, matter_data_dir).
    """
    root = _get_root()
    config_dir = root / "config"
    matter_data_dir = root / "matter"
    config_dir.mkdir(parents=True, exist_ok=True)
    matter_data_dir.mkdir(parents=True, exist_ok=True)
    return config_dir, matter_data_dir


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


async def _remove_existing_container(name: str) -> None:
    """Best-effort removal of an existing container by name."""
    process = await asyncio.create_subprocess_exec(
        "docker",
        "rm",
        "-f",
        name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.wait()


async def _pull_image(image: str, service_name: str, app_manager: AppManager) -> None:
    """Pull a Docker image, updating service status."""
    CONSOLE.print(f"Pulling image '{image}'")
    await app_manager.update_service(
        service_name,
        state="pulling",
        detail=f"Pulling image {image}",
    )
    pull_process = await asyncio.create_subprocess_exec(
        "docker",
        "pull",
        image,
    )
    pull_rc = await pull_process.wait()
    if pull_rc:
        await app_manager.update_service(
            service_name,
            state="error",
            detail=f"Failed to pull image {image} (exit code {pull_rc})",
        )
        raise RuntimeError(f"Failed to pull {image} (exit code {pull_rc})")


async def _start_matter_server(matter_data_dir: Path) -> asyncio.subprocess.Process:
    """Launch the Matter Server container and return the process handle."""
    timezone = os.environ.get("TZ", "UTC")

    matter_cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        MATTER_CONTAINER_NAME,
        "--network",
        "host",
        "--privileged",
        "-e",
        f"TZ={timezone}",
        "-v",
        f"{matter_data_dir}:/data",
        MATTER_SERVER_IMAGE,
    ]

    CONSOLE.print(f"Starting Matter Server container '{MATTER_CONTAINER_NAME}'")
    await _remove_existing_container(MATTER_CONTAINER_NAME)
    return await asyncio.create_subprocess_exec(*matter_cmd)


async def main(config: BungaloConfig) -> None:
    """
    Launch the Home Assistant and Matter Server containers via Docker-in-Docker.

    Both containers run on host network. The Matter Server listens on port 5580
    and Home Assistant connects to it for Matter device support.
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

    config_dir, matter_data_dir = _ensure_directories()
    _seed_default_config(config_dir)

    timezone = os.environ.get("TZ", "UTC")

    # Pull both images
    await _pull_image(MATTER_SERVER_IMAGE, service_name, app_manager)
    await _pull_image(HOME_ASSISTANT_IMAGE, service_name, app_manager)

    # Start Matter Server first so it's ready when HA boots
    matter_process = await _start_matter_server(matter_data_dir)

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

    CONSOLE.print(f"Starting Home Assistant container '{CONTAINER_NAME}'")
    await _remove_existing_container(CONTAINER_NAME)
    await app_manager.update_service(
        service_name,
        state="running",
        detail="Home Assistant running",
    )
    ha_process = await asyncio.create_subprocess_exec(*docker_cmd)

    ha_port = ha_config.port
    ha_host = os.environ.get("HOME_ASSISTANT_EXTERNAL_HOST") or (
        f"http://{config.root.self_ip}:{ha_port}"
        if config.root.self_ip
        else f"http://127.0.0.1:{ha_port}"
    )
    await slack_client.create_status(f"Home Assistant is now running → {ha_host}")

    # Wait for either container to exit
    done, _ = await asyncio.wait(
        [
            asyncio.create_task(ha_process.wait()),
            asyncio.create_task(matter_process.wait()),
        ],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # If HA exited, clean up Matter Server too
    if ha_process.returncode is not None:
        await _remove_existing_container(MATTER_CONTAINER_NAME)
        returncode = ha_process.returncode
    else:
        # Matter Server exited unexpectedly — report but keep HA running
        await _remove_existing_container(CONTAINER_NAME)
        returncode = matter_process.returncode

    if returncode:
        await app_manager.update_service(
            service_name,
            state="error",
            detail=f"Home Assistant exited with code {returncode}",
        )
        raise RuntimeError(f"Home Assistant exited with code {returncode}")
    await app_manager.update_service(
        service_name,
        state="completed",
        detail="Home Assistant stopped",
    )
