import asyncio
import os
import subprocess
import sys

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


async def check_existing_bootstrap(ups_conf_path: str):
    # For now we assume that if ups.conf exists we've already bootstrapped the entries
    # We can't read the contents of the file because our non-sudo executing user won't
    # have the permissions to do so
    if not os.path.exists(ups_conf_path):
        return False

    LOGGER.info("UPS device entry already exists in NUT configuration")
    CONSOLE.print("NUT already configured with UPS device")

    # Check if services are running and start them if needed
    services = ["nut-driver", "nut-server", "nut-client"]
    services_to_start: list[str] = []

    # Check service status without sudo
    for service in services:
        try:
            status = subprocess.run(
                ["systemctl", "is-active", service],
                capture_output=True,
                text=True,
                check=False,
            )
            if status.stdout.strip() != "active":
                LOGGER.info(f"{service} is not running")
                services_to_start.append(service)
            else:
                LOGGER.info(f"{service} is already running")
        except Exception as e:
            LOGGER.error(f"Error checking {service} status: {str(e)}")
            services_to_start.append(service)

    # Start services that aren't running
    if services_to_start:
        LOGGER.info("Starting inactive services...")
        for service in services_to_start:
            try:
                subprocess.run(["sudo", "systemctl", "start", service], check=True)
                await asyncio.sleep(2)  # Give service time to start
                LOGGER.info(f"Started {service}")
            except subprocess.CalledProcessError as e:
                LOGGER.error(f"Failed to start {service}: {str(e)}")
                raise NutFailedToStart(f"Failed to start {service}")

    return True


async def bootstrap_nut():
    """
    Bootstrap the NUT installation and configuration on Linux systems.
    Only proceeds if the UPS device entry doesn't already exist.

    """
    if sys.platform != "linux":
        return False, "Bootstrap is only supported on Linux systems"

    ups_conf_path = "/etc/nut/ups.conf"
    if await check_existing_bootstrap(ups_conf_path):
        return

    # Install NUT
    LOGGER.info("Installing NUT packages...")
    subprocess.run(["sudo", "apt-get", "update"], check=True)
    subprocess.run(["sudo", "apt-get", "install", "-y", "nut"], check=True)

    # Basic NUT configuration
    nut_conf_path = "/etc/nut/nut.conf"
    upsd_conf_path = "/etc/nut/upsd.conf"
    users_conf_path = "/etc/nut/upsd.users"
    upsmon_conf_path = "/etc/nut/upsmon.conf"

    # Create /etc/nut directory if it doesn't exist and set permissions
    LOGGER.info("Setting up NUT configuration directory...")
    subprocess.run(["sudo", "mkdir", "-p", "/etc/nut"], check=True)
    subprocess.run(
        ["sudo", "chown", "-R", f"{os.getuid()}:nut", "/etc/nut"], check=True
    )
    subprocess.run(["sudo", "chmod", "750", "/etc/nut"], check=True)

    # Configure NUT to run in standalone mode. Unexpectedly this is a command versus a simple key=value pair
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
                    # Add polling to ensure driver keeps checking for UPS
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
        subprocess.run(["sudo", "chown", "root:nut", conf_file], check=True)
        subprocess.run(["sudo", "chmod", "640", conf_file], check=True)

    # Ensure NUT can access USB devices
    LOGGER.info("Setting up USB permissions...")
    user_name = os.getenv("USER")
    if not user_name:
        raise NutFailedToStart("Failed to get user name")
    subprocess.run(["sudo", "usermod", "-a", "-G", "nut", user_name], check=True)
    subprocess.run(["sudo", "udevadm", "control", "--reload-rules"], check=True)

    # Stop all NUT services first
    LOGGER.info("Stopping NUT services...")
    for service in ["nut-client", "nut-server", "nut-driver"]:
        try:
            subprocess.run(["sudo", "systemctl", "stop", service], check=False)
        except Exception:
            pass  # Ignore errors when stopping services

    # Start services in correct order
    # NOTE: sudo upsdrvctl -D start can be used to help debug
    LOGGER.info("Starting NUT services...")
    try:
        # Start driver first and wait
        services = ["nut-driver", "nut-server", "nut-client"]
        for service in services:
            subprocess.run(["sudo", "systemctl", "start", service], check=True)
            await asyncio.sleep(2)  # Give driver time to detect UPS

        # Check driver status
        driver_status = subprocess.run(
            ["upsc", "ups@localhost"], capture_output=True, text=True, check=False
        )

        if driver_status.returncode != 0:
            LOGGER.error(f"Driver status check failed:\n{driver_status.stderr}")
            # Get detailed service status
            status_output = subprocess.run(
                ["systemctl", "status", "nut-driver.service"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            LOGGER.error(f"NUT driver status:\n{status_output}")

            raise NutFailedToStart(
                "Failed to detect UPS. Please check USB connection and permissions."
            )

        LOGGER.info("UPS detected successfully!")

    except subprocess.CalledProcessError as e:
        # Get detailed service status if nut-server fails
        try:
            status_output = subprocess.run(
                ["systemctl", "status", "nut-server.service"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            LOGGER.error(
                f"NUT server failed to start. Service status:\n{status_output}"
            )

            # Check common issues
            if "no UPS definitions" in status_output:
                raise NutNoUPSFound()
            elif "already running" in status_output:
                raise NutAlreadyRunning()
            elif "Permission denied" in status_output:
                raise NutPermissionDenied()
            else:
                raise NutFailedToStart(str(e))
        except Exception as status_err:
            LOGGER.error(f"Failed to get service status: {str(status_err)}")
            raise NutFailedToStart(str(status_err))
