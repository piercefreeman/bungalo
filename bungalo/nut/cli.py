import asyncio
import sys
from datetime import timedelta
from traceback import format_exc

from bungalo.app_manager import AppManager
from bungalo.config import BungaloConfig
from bungalo.constants import NUT_SERVER_PORT
from bungalo.logger import CONSOLE, LOGGER
from bungalo.nut.battery import UPSMonitor
from bungalo.nut.bootstrap import bootstrap_nut, check_nut_status
from bungalo.nut.client_manager import ClientMachine, ClientManager
from bungalo.slack import SlackClient


async def main(config: BungaloConfig):
    client_manager = ClientManager(
        [
            ClientMachine(
                hostname=client.local_ip,
                username=client.username,
            )
            for client in config.nut.managed_hardware
        ]
    )
    slack_client = SlackClient(
        app_token=config.slack.app_token,
        bot_token=config.slack.bot_token,
        channel_id=config.slack.channel,
    )
    app_manager = AppManager.get()
    service_name = "nut_monitor"

    await app_manager.update_service(
        service_name,
        state="running",
        detail="Initializing UPS monitoring service",
    )

    # Run bootstrap, poll, and healthcheck tasks concurrently
    if sys.platform == "linux":
        await asyncio.gather(
            bootstrap_task(
                slack_client,
                config.nut.bootstrap_retry_interval,
                app_manager=app_manager,
                service_name=service_name,
            ),
            poll_task(
                client_manager,
                slack_client,
                config,
                app_manager=app_manager,
                service_name=service_name,
            ),
            healthcheck_task(
                client_manager,
                slack_client,
                app_manager=app_manager,
                service_name=service_name,
            ),
        )
    else:
        # Skip bootstrap on non-Linux platforms
        await asyncio.gather(
            poll_task(
                client_manager,
                slack_client,
                config,
                app_manager=app_manager,
                service_name=service_name,
            ),
            healthcheck_task(
                client_manager,
                slack_client,
                app_manager=app_manager,
                service_name=service_name,
            ),
        )


async def bootstrap_task(
    slack_client: SlackClient,
    retry_interval: timedelta,
    *,
    app_manager: AppManager | None = None,
    service_name: str = "nut_monitor",
):
    """
    Continuously attempt to bootstrap NUT with retry logic.

    :param slack_client: Slack client for notifications
    :param retry_interval: Time to wait between retry attempts
    """
    bootstrap_successful = False

    while True:
        if not bootstrap_successful:
            try:
                LOGGER.info("Attempting to bootstrap NUT...")
                await bootstrap_nut()
                await slack_client.create_status(
                    "✅ NUT bootstrap completed successfully"
                )
                if app_manager:
                    await app_manager.update_service(
                        service_name,
                        state="running",
                        detail="NUT bootstrap completed successfully",
                    )
                bootstrap_successful = True
                LOGGER.info("NUT bootstrap successful, monitoring for failures...")
            except Exception as e:
                error_msg = f"Failed to bootstrap NUT: {e}"
                CONSOLE.print(f"[red]{error_msg}[/red]")
                LOGGER.error(f"{error_msg}\n{format_exc()}")
                await slack_client.create_status(f"❌ {error_msg}")
                if app_manager:
                    await app_manager.update_service(
                        service_name,
                        state="error",
                        detail=error_msg,
                    )
                LOGGER.info(
                    f"Retrying NUT bootstrap in {retry_interval.total_seconds()} seconds..."
                )
                await asyncio.sleep(retry_interval.total_seconds())
                continue

        # If bootstrap was successful, check if NUT is still running
        try:
            if not await check_nut_status():
                LOGGER.warning(
                    "NUT is no longer responding, will attempt to re-bootstrap"
                )
                await slack_client.create_status(
                    "⚠️ NUT stopped responding, attempting to re-bootstrap..."
                )
                bootstrap_successful = False
                continue
        except Exception as e:
            LOGGER.error(f"Error checking NUT status: {e}")
            if app_manager:
                await app_manager.update_service(
                    service_name,
                    state="error",
                    detail=f"NUT status check failed: {e}",
                )
            bootstrap_successful = False
            continue

        # Wait before next health check
        await asyncio.sleep(60)  # Check NUT health every minute


async def poll_task(
    client_manager: ClientManager,
    slack_client: SlackClient,
    config: BungaloConfig,
    *,
    app_manager: AppManager | None = None,
    service_name: str = "nut_monitor",
):
    # Wait for NUT to be available before starting to poll
    LOGGER.info("Waiting for NUT to be available before starting polling...")
    while True:
        try:
            if await check_nut_status():
                LOGGER.info("NUT is available, starting UPS monitoring...")
                if app_manager:
                    await app_manager.update_service(
                        service_name,
                        state="running",
                        detail="Monitoring UPS telemetry",
                    )
                break
        except Exception as e:
            LOGGER.debug(f"NUT not yet available: {e}")
        await asyncio.sleep(10)  # Check every 10 seconds

    # Update these values based on your NUT server configuration
    monitor = UPSMonitor(host="localhost", port=NUT_SERVER_PORT, ups_name="ups")
    clients_shutdown = False

    try:
        async for status in monitor.poll():
            CONSOLE.print(f"[green]UPS status changed: {status}[/green]")

            # Handle battery thresholds for client machines
            if status.battery_charge is None:
                continue

            if app_manager:
                human_power = (
                    "On battery" if status.statuses.is_on_battery() else "On AC"
                )
                await app_manager.update_service(
                    service_name,
                    state="running",
                    detail=f"Battery {status.battery_charge}% · {human_power}",
                )

            # Send a slack message
            if not clients_shutdown and status.statuses.is_on_battery():
                await slack_client.create_status(f"Battery at {status.battery_charge}%")

            if (
                status.battery_charge <= config.nut.shutdown_threshold
                and not clients_shutdown
                and status.statuses.is_on_battery()
            ):
                LOGGER.warning(
                    f"Battery at {status.battery_charge}% - below shutdown threshold. "
                    "Shutting down client machines..."
                )
                await client_manager.shutdown_clients()
                await slack_client.create_status(
                    f"Battery at {status.battery_charge}% - below shutdown threshold. Shutting down client machines..."
                )
                clients_shutdown = True
            elif (
                status.battery_charge >= config.nut.startup_threshold
                and clients_shutdown
                and not status.statuses.is_on_battery()
            ):
                LOGGER.info(
                    f"Battery at {status.battery_charge}% - above startup threshold "
                    "and on AC power. Waking client machines..."
                )
                await client_manager.wake_clients()
                clients_shutdown = False
    except Exception as e:
        error_msg = f"UPS polling encountered an error: {e}"
        LOGGER.error(f"{error_msg}\n{format_exc()}")
        await slack_client.create_status(f"❌ {error_msg}")
        if app_manager:
            await app_manager.update_service(
                service_name,
                state="error",
                detail=error_msg,
            )
        # The poll method already has retry logic, so this shouldn't normally happen
        # but if it does, the entire main() function will restart


async def healthcheck_task(
    client_manager: ClientManager,
    slack_client: SlackClient,
    *,
    app_manager: AppManager | None = None,
    service_name: str = "nut_monitor",
    interval: int = 5 * 60,
):
    while True:
        results = await client_manager.healthcheck()
        failed_clients = [client for client, status in results.items() if not status]
        if failed_clients:
            await slack_client.create_status(
                f"Failed to connect to {', '.join(failed_clients)}"
            )
            if app_manager:
                await app_manager.update_service(
                    service_name,
                    state="warning",
                    detail=f"Unreachable clients: {', '.join(failed_clients)}",
                )
        await asyncio.sleep(interval)
