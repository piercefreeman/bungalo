import asyncio
import os
import subprocess
from dataclasses import dataclass
from typing import Literal

from bungalo.constants import NUT_SERVER_PORT
from bungalo.logger import CONSOLE, LOGGER
from bungalo.nut.formatter import Command, Section


class NutNoUPSFound(Exception):
    def __init__(self):
        super().__init__(
            "NUT server failed: No valid UPS definitions found. Please check your UPS connection."
        )


class NutAlreadyRunning(Exception):
    def __init__(self):
        super().__init__(
            "NUT server failed: Service is already running. Try stopping it first."
        )


class NutPermissionDenied(Exception):
    def __init__(self):
        super().__init__(
            "NUT server failed: Permission issues with configuration files."
        )


class NutFailedToStart(Exception):
    def __init__(self, message: str):
        super().__init__(
            f"NUT server failed to start: {message}, check logs for details"
        )


NutService = Literal["nut-driver", "nut-server", "nut-monitor"]


@dataclass
class NutEntrypoint:
    name: NutService
    start_command: str
    stop_command: str


NUT_ENTRYPOINTS = [
    NutEntrypoint(
        name="nut-driver",
        start_command="upsdrvctl -u root start",
        stop_command="upsdrvctl stop",
    ),
    NutEntrypoint(name="nut-server", start_command="upsd", stop_command="upsd -c stop"),
    NutEntrypoint(
        name="nut-monitor", start_command="upsmon", stop_command="upsmon -c stop"
    ),
]


async def start_nut_service(entrypoint: NutEntrypoint):
    """Start a NUT process."""
    LOGGER.info(f"Starting {entrypoint.name}...")
    process = run_command(
        entrypoint.start_command,
        capture_output=True,
        text=True,
        check=False,
    )

    if process.returncode != 0:
        LOGGER.error(
            f"Driver debug output:\n{process.stderr}\n{process.stdout}\nReturn code: {process.returncode}"
        )

        if entrypoint.name == "nut-driver":
            diagnose_env_errors()

        raise NutFailedToStart(f"Failed to start {entrypoint.name}")
    else:
        LOGGER.info(f"Started {entrypoint.name}")

    await asyncio.sleep(2)  # Give process time to start


async def stop_nut_service(entrypoint: NutEntrypoint):
    """Stop a NUT process."""
    try:
        run_command(entrypoint.stop_command, check=False)
    except Exception:
        pass  # Ignore errors when stopping processes


def diagnose_env_errors():
    # Check USB devices
    lsusb_output = run_command("lsusb", capture_output=True, text=True)
    LOGGER.info(f"Available USB devices:\n{lsusb_output.stdout}")

    # Check NUT permissions
    ls_output = run_command("ls -la /dev/bus/usb/", capture_output=True, text=True)
    LOGGER.info(f"USB device permissions:\n{ls_output.stdout}")

    # Check if nut user exists and its groups
    groups_output = run_command("id nut", capture_output=True, text=True)
    LOGGER.info(f"NUT user details:\n{groups_output.stdout}")


async def check_nut_status() -> bool:
    """Check if NUT is responding properly."""
    try:
        status = run_command(
            ["upsc", "ups@localhost"], capture_output=True, text=True, check=False
        )
        return status.returncode == 0
    except Exception:
        return False


async def bootstrap_files():
    """Bootstrap the necessary config files on disk for NUT. No-op if files already exist."""
    ups_conf_path = "/etc/nut/ups.conf"

    if os.path.exists(ups_conf_path):
        LOGGER.info("UPS device entry already exists in NUT configuration")
        CONSOLE.print("NUT already configured with UPS device")

        return

    # Basic NUT configuration
    nut_conf_path = "/etc/nut/nut.conf"
    upsd_conf_path = "/etc/nut/upsd.conf"
    users_conf_path = "/etc/nut/upsd.users"
    upsmon_conf_path = "/etc/nut/upsmon.conf"

    # Create /etc/nut directory if it doesn't exist and set permissions
    LOGGER.info("Setting up NUT configuration directory...")
    run_command(["mkdir", "-p", "/etc/nut"], check=True)
    run_command(["chown", "-R", f"{os.getuid()}:nut", "/etc/nut"], check=True)
    run_command(["chmod", "750", "/etc/nut"], check=True)

    # Configure NUT to run in standalone mode
    LOGGER.info("Configuring NUT...")
    with open(nut_conf_path, "w") as f:
        f.write(Section(list_values=[Command(values=["MODE=standalone"])]).render())

    # Create basic ups.conf with required settings
    with open(ups_conf_path, "w") as f:
        f.write(
            Section(
                title="ups",
                dict_values={
                    "driver": "usbhid-ups",
                    "port": "auto",
                    "desc": "Local UPS",
                    "pollinterval": 2,
                },
            ).render()
        )

    # Create upsd.conf with network settings
    with open(upsd_conf_path, "w") as f:
        f.write(
            Section(
                list_values=[
                    Command(values=["LISTEN", "127.0.0.1", NUT_SERVER_PORT]),
                    Command(values=["LISTEN", "::1", NUT_SERVER_PORT]),
                    Command(values=["MAXAGE", 15]),
                ]
            ).render()
        )

    # Create upsd.users with admin user
    with open(users_conf_path, "w") as f:
        f.write(
            Section(
                title="admin",
                dict_values={"password": "admin", "actions": "SET", "instcmds": "ALL"},
            ).render()
        )
        f.write(
            Section(
                title="monmaster",
                dict_values={
                    "password": "monmaster",
                },
                list_values=[Command(values=["upsmon", "master"])],
            ).render()
        )

    # Create upsmon.conf
    with open(upsmon_conf_path, "w") as f:
        f.write(
            Section(
                list_values=[
                    Command(
                        values=[
                            "MONITOR",
                            "ups@localhost",
                            1,
                            "monmaster",
                            "monmaster",
                            "master",
                        ]
                    ),
                    Command(values=["MINSUPPLIES", 1]),
                    Command(values=["SHUTDOWNCMD", "shutdown -h now"]),
                    Command(values=["POLLFREQ", 5]),
                    Command(values=["POLLFREQALERT", 5]),
                    Command(values=["HOSTSYNC", 15]),
                    Command(values=["DEADTIME", 15]),
                ]
            ).render()
        )

    # Set correct permissions after writing
    for conf_file in [
        nut_conf_path,
        ups_conf_path,
        upsd_conf_path,
        users_conf_path,
        upsmon_conf_path,
    ]:
        run_command(["chown", "root:nut", conf_file], check=True)
        run_command(["chmod", "640", conf_file], check=True)


async def bootstrap_nut() -> None:
    """Bootstrap the NUT installation and configuration."""
    await bootstrap_files()

    # Stop any running services first
    for entrypoint in NUT_ENTRYPOINTS:
        await stop_nut_service(entrypoint)

    # Start services in correct order
    LOGGER.info("Starting NUT services...")
    for entrypoint in NUT_ENTRYPOINTS:
        await start_nut_service(entrypoint)

    if not await check_nut_status():
        LOGGER.error("Failed to detect UPS")
        raise NutFailedToStart(
            "Failed to detect UPS. Please check USB connection and permissions."
        )

    LOGGER.info("UPS detected successfully!")


def run_command(
    cmd: str | list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    text: bool = False,
) -> subprocess.CompletedProcess:
    """
    Run a command.

    :param cmd: Command to run as string or list
    :param check: Whether to check return code
    :param capture_output: Whether to capture output
    :param text: Whether to return string output

    """
    if isinstance(cmd, str):
        cmd = cmd.split()

    return subprocess.run(cmd, check=check, capture_output=capture_output, text=text)
