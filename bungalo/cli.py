import asyncio
from pathlib import Path
from tomllib import loads as toml_loads

from click import group

from bungalo.backups.iphoto import main as iphoto_main
from bungalo.config import BungaloConfig
from bungalo.constants import DEFAULT_CONFIG_FILE
from bungalo.io import async_to_sync
from bungalo.nut.cli import main as battery_main
from bungalo.ssh import main as ssh_main


@group()
def cli():
    pass


@cli.command()
@async_to_sync
async def run_all():
    """Run all bungalo workflows."""
    config = get_config()
    await asyncio.gather(
        battery_main(config),
        iphoto_main(config),
    )


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
async def ssh_setup():
    """Generate SSH key and show instructions for UniFi Network setup."""
    await ssh_main()


def get_config():
    config_raw = Path(DEFAULT_CONFIG_FILE).expanduser().read_text()
    return BungaloConfig.model_validate(toml_loads(config_raw))
