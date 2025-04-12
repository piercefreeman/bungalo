from click import group

from bungalo.io import async_to_sync
from bungalo.nut.battery import main as battery_main


@group()
def cli():
    pass


@cli.command()
@async_to_sync
async def auto_shutdown():
    await battery_main()
