import asyncio

from click import group

from bungalo.config import BungaloConfig
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
    config = BungaloConfig()  # type: ignore
    await asyncio.gather(
        battery_main(config),
    )


@cli.command()
@async_to_sync
async def auto_shutdown():
    """Launch a daemon to monitor battery status and shutdown local machines when low."""
    config = BungaloConfig()  # type: ignore
    await battery_main(config)


@cli.command()
@async_to_sync
async def ssh_setup():
    """Generate SSH key and show instructions for UniFi Network setup."""
    await ssh_main()
