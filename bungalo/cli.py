import asyncio
import os
from pathlib import Path
from tomllib import loads as toml_loads

from click import group

from bungalo.backups.iphoto import main as iphoto_main
from bungalo.backups.remote import main as remote_main
from bungalo.app_manager import AppManager
from bungalo.config import BungaloConfig
from bungalo.constants import DEFAULT_CONFIG_FILE
from bungalo.dashboard import start_dashboard_services
from bungalo.io import async_to_sync
from bungalo.nut.cli import main as battery_main
from bungalo.plugins.plex import main as plex_main
from bungalo.ssh import main as ssh_main


@group()
def cli():
    pass


@cli.command()
@async_to_sync
async def run_all():
    """Run all bungalo workflows."""
    config = get_config()

    dashboard_port_raw = os.environ.get("BUNGALO_NEXT_PORT")
    try:
        dashboard_port = int(dashboard_port_raw) if dashboard_port_raw else 3000
    except ValueError:
        dashboard_port = 3000
    os.environ.setdefault("BUNGALO_DASHBOARD_URL", f"http://127.0.0.1:{dashboard_port}")
    AppManager.get()  # Ensure singleton initializes with dashboard URL

    tasks = [
        start_dashboard_services(),
        battery_main(config),
        iphoto_main(config),
        remote_main(config),
    ]
    if config.media_server and config.media_server.plugin == "plex":
        tasks.append(plex_main(config))
    await asyncio.gather(*tasks)


@cli.command()
@async_to_sync
async def auto_shutdown():
    """Launch a daemon to monitor battery status and shutdown local machines when low."""
    config = get_config()
    await battery_main(config)


@cli.command()
@async_to_sync
async def iphoto_backup():
    """Backup iPhoto library to NAS."""
    config = get_config()
    await iphoto_main(config)


@cli.command()
@async_to_sync
async def remote_backup():
    """Backup NAS files to a remote server using rclone."""
    config = get_config()
    await remote_main(config)


@cli.command()
@async_to_sync
async def ssh_setup():
    """Generate SSH key and show instructions for UniFi Network setup."""
    await ssh_main()


@cli.command()
@async_to_sync
async def plex():
    """Launch the Plex media server plugin."""
    config = get_config()
    await plex_main(config)


def get_config():
    config_raw = Path(DEFAULT_CONFIG_FILE).expanduser().read_text()
    return BungaloConfig.model_validate(toml_loads(config_raw))
