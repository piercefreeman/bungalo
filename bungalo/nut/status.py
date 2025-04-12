from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema


@dataclass
class StatusDefinition:
    status_codes: str | set[str]

    def matches_status(self, status_str: str) -> bool:
        status_str = status_str.lower()
        status_codes = (
            self.status_codes
            if isinstance(self.status_codes, set)
            else {self.status_codes}
        )
        return any(code in status_str for code in status_codes)


class UPSStatus(Enum):
    """
    Standard NUT UPS status codes and their meanings.
    """

    # Primary states
    ONLINE = StatusDefinition({"ol", "online"})  # On utility power
    ON_BATTERY = StatusDefinition({"ob", "onbatt"})  # On battery power
    LOW_BATTERY = StatusDefinition("lb")  # Low battery
    REPLACE_BATTERY = StatusDefinition("rb")  # Replace battery
    CHARGING = StatusDefinition("chrg")  # Battery charging
    DISCHARGING = StatusDefinition("dischrg")  # Battery discharging
    BYPASS = StatusDefinition("bypass")  # Bypass active
    CALIBRATING = StatusDefinition("cal")  # Calibration in progress
    OFFLINE = StatusDefinition("off")  # UPS is offline
    OVERLOADED = StatusDefinition("over")  # UPS is overloaded
    TRIMMING = StatusDefinition("trim")  # Trimming voltage
    BOOSTING = StatusDefinition("boost")  # Boosting voltage
    FORCED_SHUTDOWN = StatusDefinition("fsd")  # Forced shutdown


class UPSStatuses(list[UPSStatus]):
    def __init__(self, status_str: str):
        super().__init__(self._parse(status_str))

    def is_on_battery(self) -> bool | None:
        """
        Determine if the UPS is running on battery from a set of statuses.

        :param statuses: Set of UPSStatus enums
        :return: True if on battery, False if on utility power, None if unknown
        """
        if UPSStatus.ON_BATTERY in self or UPSStatus.DISCHARGING in self:
            return True
        elif UPSStatus.ONLINE in self or UPSStatus.CHARGING in self:
            return False
        return None

    def _parse(self, status_str: str) -> set[UPSStatus]:
        """
        Parse a NUT status string into a set of UPS statuses.

        :param status_str: Raw status string from NUT
        :return: Set of matching UPSStatus enums
        """
        return {
            status for status in UPSStatus if status.value.matches_status(status_str)
        }

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(cls, handler(str))
