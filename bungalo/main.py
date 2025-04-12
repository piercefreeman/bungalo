from click import group

from bungalo.battery import main as battery_main
from bungalo.io import async_to_sync


@group()
def cli():
    pass


@cli.command()
@async_to_sync
async def auto_shutdown():
    await battery_main()
