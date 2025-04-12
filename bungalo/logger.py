import logging

from rich.console import Console

# Configure logging with a more detailed format and ensure we capture debug and above
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True,  # Override any existing logging configuration
)

# Get logger for this module specifically
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)

CONSOLE = Console()
