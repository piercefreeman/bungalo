from enum import Enum


class UPSStatus(Enum):
    """
    Standard NUT UPS status codes and their meanings.
    """

    # Primary states
    ONLINE = ("ol", "online")  # On utility power
    ON_BATTERY = ("ob", "onbatt")  # On battery power
    LOW_BATTERY = ("lb",)  # Low battery
    REPLACE_BATTERY = ("rb",)  # Replace battery
    CHARGING = ("chrg",)  # Battery charging
    DISCHARGING = ("dischrg",)  # Battery discharging
    BYPASS = ("bypass",)  # Bypass active
    CALIBRATING = ("cal",)  # Calibration in progress
    OFFLINE = ("off",)  # UPS is offline
    OVERLOADED = ("over",)  # UPS is overloaded
    TRIMMING = ("trim",)  # Trimming voltage
    BOOSTING = ("boost",)  # Boosting voltage
    FORCED_SHUTDOWN = ("fsd",)  # Forced shutdown

    def __init__(self, *status_codes: str):
        self.status_codes = status_codes

    @classmethod
    def parse(cls, status_str: str) -> set["UPSStatus"]:
        """
        Parse a NUT status string into a set of UPS statuses.

        :param status_str: Raw status string from NUT
        :return: Set of matching UPSStatus enums
        """
        status_str = status_str.lower()
        return {
            status
            for status in cls
            if any(code in status_str for code in status.status_codes)
        }

    @classmethod
    def is_on_battery(cls, statuses: set["UPSStatus"]) -> bool | None:
        """
        Determine if the UPS is running on battery from a set of statuses.

        :param statuses: Set of UPSStatus enums
        :return: True if on battery, False if on utility power, None if unknown
        """
        if cls.ON_BATTERY in statuses or cls.DISCHARGING in statuses:
            return True
        elif cls.ONLINE in statuses or cls.CHARGING in statuses:
            return False
        return None
