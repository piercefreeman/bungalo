import asyncio
import sys
from typing import Annotated, Dict, Optional

import nut2 as nut
from pydantic import BaseModel, Field

from bungalo.constants import NUT_SERVER_PORT
from bungalo.logger import CONSOLE, LOGGER
from bungalo.nut.bootstrap import bootstrap_nut
from bungalo.nut.status import UPSStatuses


class StatusSummary(BaseModel):
    statuses: Annotated[UPSStatuses | None, Field(alias="ups.status")] = None
    battery_charge: Annotated[int | None, Field(alias="battery.charge")] = None
    runtime: Annotated[int | None, Field(alias="battery.runtime")] = None  # seconds
    load: Annotated[int | None, Field(alias="ups.load")] = None
    temperature: Annotated[int | None, Field(alias="ups.temperature")] = None


class UPSMonitor:
    def __init__(
        self,
        host: str = "localhost",
        port: int = NUT_SERVER_PORT,
        ups_name: str = "ups",
    ):
        """
        Initialize the UPS monitor using NUT (Network UPS Tools).

        :param host: Hostname or IP of the NUT server
        :param port: Port number of the NUT server (default: NUT_SERVER_PORT)
        :param ups_name: Name of the UPS device as configured in NUT
        """
        self.host = host
        self.port = port
        self.ups_name = ups_name

    async def get_status(self) -> Dict[str, str]:
        """
        Get the current status of the UPS.

        :return: Dictionary of UPS variables and their values
        """
        try:
            # Run the blocking NUT operations in a thread pool
            LOGGER.info(f"Connecting to NUT server at {self.host}:{self.port}")
            client = await asyncio.to_thread(
                nut.PyNUTClient, host=self.host, port=self.port
            )
            LOGGER.debug(
                f"Connected to NUT server, fetching variables for '{self.ups_name}'..."
            )
            vars = await asyncio.to_thread(client.list_vars, self.ups_name)
            if not vars:
                LOGGER.warning(f"No variables returned for UPS '{self.ups_name}'")
            else:
                LOGGER.debug(f"Retrieved {len(vars)} variables from UPS")
            return vars
        except Exception as e:
            LOGGER.error(f"Error getting UPS status: {str(e)}", exc_info=True)
            return {}

    def _parse_status(self, status: Dict[str, str]) -> Optional[bool]:
        """
        Parse the UPS status to determine if it's on battery.

        :param status: Dictionary of UPS variables
        :return: True if on battery, False if on AC, None if unknown
        """
        if not status:
            LOGGER.warning("No status data available to parse")
            return None

        LOGGER.info(f"Raw status payload: {status}")

        # Check ups.status variable
        summary = StatusSummary.model_validate(status)
        if not summary.statuses:
            LOGGER.warning("No 'ups.status' variable found in status data")
            return None

        # Log all detected statuses
        for status in summary.statuses:
            LOGGER.debug(f"Detected UPS status: {status.name}")

        # Determine if we're on battery
        is_battery = summary.statuses.is_on_battery()
        if is_battery:
            LOGGER.warning("UPS is running on battery power")
        elif is_battery is False:
            LOGGER.info("UPS is running on utility power")
        else:
            LOGGER.warning(
                f"Could not determine power state from statuses: {[s.name for s in summary.statuses]}"
            )

        return summary

    async def poll(self, interval_seconds: int = 10):
        """
        Continuously poll the UPS status. Yields when there is a change in the UPS status.

        :param interval_seconds: Time between status checks in seconds
        """
        LOGGER.info(f"Starting UPS status monitoring for {self.host}")
        last_status: StatusSummary | None = None
        while True:
            try:
                LOGGER.debug("Polling UPS status...")
                raw_status = await self.get_status()
                if raw_status:
                    LOGGER.debug(f"Raw UPS status data: {raw_status}")
                status = self._parse_status(raw_status)

                # Only yield differences in the charge state or the charge level
                if (
                    last_status is None
                    or last_status.statuses.is_on_battery()
                    != status.statuses.is_on_battery()
                    or last_status.battery_charge != status.battery_charge
                ):
                    yield status

                last_status = status

            except Exception as e:
                LOGGER.error(f"Error polling UPS: {str(e)}", exc_info=True)

            LOGGER.debug(f"Waiting {interval_seconds} seconds before next poll...")
            await asyncio.sleep(interval_seconds)


async def main():
    # First try to bootstrap NUT if we're on Linux
    if sys.platform == "linux":
        try:
            await bootstrap_nut()
        except Exception as e:
            CONSOLE.print(f"[red]Failed to bootstrap NUT: {e}[/red]")
            sys.exit(1)

    # Update these values based on your NUT server configuration
    monitor = UPSMonitor(host="localhost", port=NUT_SERVER_PORT, ups_name="ups")
    async for status in monitor.poll():
        CONSOLE.print(f"[green]UPS status changed: {status}[/green]")
