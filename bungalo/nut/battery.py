import asyncio
import sys
from typing import Dict, Optional

import nut2 as nut

from bungalo.constants import NUT_SERVER_PORT
from bungalo.logger import CONSOLE, LOGGER
from bungalo.nut.bootstrap import bootstrap_nut
from bungalo.nut.status import UPSStatus


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
        self._last_status: Optional[Dict[str, str]] = None

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
            LOGGER.debug("Connected to NUT server, fetching variables...")
            vars = await asyncio.to_thread(client.list_vars, self.ups_name)
            if not vars:
                LOGGER.warning(f"No variables returned for UPS '{self.ups_name}'")
            else:
                LOGGER.debug(f"Retrieved {len(vars)} variables from UPS")
            self._last_status = vars
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

        # Check ups.status variable
        ups_status = status.get("ups.status", "")
        if not ups_status:
            LOGGER.warning("No 'ups.status' variable found in status data")
            return None

        LOGGER.info(f"Current UPS status: {ups_status}")

        # Parse the status string into UPSStatus enums
        statuses = UPSStatus.parse(ups_status)
        if not statuses:
            LOGGER.warning(f"Unknown UPS status value: {ups_status}")
            return None

        # Log all detected statuses
        for status in statuses:
            LOGGER.debug(f"Detected UPS status: {status.name}")

        # Determine if we're on battery
        is_battery = UPSStatus.is_on_battery(statuses)
        if is_battery:
            LOGGER.warning("UPS is running on battery power")
        elif is_battery is False:
            LOGGER.info("UPS is running on utility power")
        else:
            LOGGER.warning(
                f"Could not determine power state from statuses: {[s.name for s in statuses]}"
            )

        return is_battery

    def get_status_summary(self, status: Dict[str, str]) -> str:
        """
        Get a human-readable summary of the UPS status.

        :param status: Dictionary of UPS variables
        :return: Formatted status summary
        """
        summary = []

        battery_charge = status.get("battery.charge")
        if battery_charge:
            summary.append(f"🔋 Battery charge: {battery_charge}%")

        runtime = status.get("battery.runtime")
        if runtime:
            minutes = int(runtime) // 60
            summary.append(f"⏱️ Runtime remaining: {minutes} minutes")

        load = status.get("ups.load")
        if load:
            summary.append(f"⚡ Load: {load}%")

        temperature = status.get("ups.temperature")
        if temperature:
            summary.append(f"🌡️ Temperature: {temperature}°C")

        return "\n".join(summary)

    async def poll_status(self, interval_seconds: int = 10):
        """
        Continuously poll the UPS status and print the power state.

        :param interval_seconds: Time between status checks in seconds
        """
        LOGGER.info(f"Starting UPS status monitoring for {self.host}")
        while True:
            try:
                LOGGER.debug("Polling UPS status...")
                status = await self.get_status()
                if status:
                    LOGGER.debug(f"Raw UPS status data: {status}")
                is_on_battery = self._parse_status(status)

                if is_on_battery is None:
                    LOGGER.warning(
                        "Could not determine UPS status - check if UPS is properly connected and detected"
                    )
                elif is_on_battery:
                    LOGGER.warning("UPS is on battery power!")
                else:
                    LOGGER.info("UPS is on utility power")

                summary = self.get_status_summary(status)
                if summary:
                    LOGGER.info("\n" + summary)
                else:
                    LOGGER.warning(
                        "No status summary available - check if UPS is responding with data"
                    )

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
    await monitor.poll_status()
