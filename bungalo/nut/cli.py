import sys

from bungalo.config import BungaloConfig
from bungalo.constants import NUT_SERVER_PORT
from bungalo.logger import CONSOLE, LOGGER
from bungalo.nut.battery import UPSMonitor
from bungalo.nut.bootstrap import bootstrap_nut
from bungalo.nut.client_manager import ClientMachine, ClientManager
from bungalo.slack import SlackClient


async def main(config: BungaloConfig):
    # First try to bootstrap NUT if we're on Linux
    if sys.platform == "linux":
        try:
            await bootstrap_nut()
        except Exception as e:
            CONSOLE.print(f"[red]Failed to bootstrap NUT: {e}[/red]")
            sys.exit(1)

    client_manager = ClientManager(
        [
            ClientMachine(
                hostname=client.local_ip,
                username=client.username,
            )
            for client in config.managed_hardware
        ]
    )
    slack_client = SlackClient(config.slack_webhook_url)

    clients_shutdown = False

    # Update these values based on your NUT server configuration
    monitor = UPSMonitor(host="localhost", port=NUT_SERVER_PORT, ups_name="ups")
    async for status in monitor.poll():
        CONSOLE.print(f"[green]UPS status changed: {status}[/green]")

        # Handle battery thresholds for client machines
        if status.battery_charge is None:
            continue

        # Send a slack message
        if not clients_shutdown and status.statuses.is_on_battery():
            await slack_client.send_message(f"Battery at {status.battery_charge}%")

        if (
            status.battery_charge <= config.nut_shutdown_threshold
            and not clients_shutdown
            and status.statuses.is_on_battery()
        ):
            LOGGER.warning(
                f"Battery at {status.battery_charge}% - below shutdown threshold. "
                "Shutting down client machines..."
            )
            await client_manager.shutdown_clients()
            await slack_client.send_message(
                f"Battery at {status.battery_charge}% - below shutdown threshold. Shutting down client machines..."
            )
            clients_shutdown = True
        elif (
            status.battery_charge >= config.nut_startup_threshold
            and clients_shutdown
            and not status.statuses.is_on_battery()
        ):
            LOGGER.info(
                f"Battery at {status.battery_charge}% - above startup threshold "
                "and on AC power. Waking client machines..."
            )
            await client_manager.wake_clients()
            clients_shutdown = False
