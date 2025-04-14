import logging
from os import getenv

from rich.console import Console


def configure_logger():
    # Configure logging with a more detailed format and ensure we capture debug and above
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        force=True,  # Override any existing logging configuration
    )

    log_level_str = getenv("BUNGALO_LOG_LEVEL", "INFO")
    if log_level_str == "DEBUG":
        log_level = logging.DEBUG
    elif log_level_str == "INFO":
        log_level = logging.INFO
    elif log_level_str == "WARNING":
        log_level = logging.WARNING
    elif log_level_str == "ERROR":
        log_level = logging.ERROR
    elif log_level_str == "CRITICAL":
        log_level = logging.CRITICAL
    else:
        log_level = logging.INFO

    # Get logger for this module specifically
    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)

    return logger


LOGGER = configure_logger()
CONSOLE = Console()
