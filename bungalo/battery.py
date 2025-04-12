import asyncio
import subprocess
from datetime import datetime
from typing import Dict, Optional, Tuple
import logging
import nut2 as nut
import sys
import os
from enum import Enum, auto

# Configure logging with a more detailed format and ensure we capture debug and above
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True  # Override any existing logging configuration
)

# Get logger for this module specifically
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Port hosting our local watch server
NUT_SERVER_PORT = 3493

class UPSStatus(Enum):
    """
    Standard NUT UPS status codes and their meanings.
    """
    # Primary states
    ONLINE = ('ol', 'online')  # On utility power
    ON_BATTERY = ('ob', 'onbatt')  # On battery power
    LOW_BATTERY = ('lb',)  # Low battery
    REPLACE_BATTERY = ('rb',)  # Replace battery
    CHARGING = ('chrg',)  # Battery charging
    DISCHARGING = ('dischrg',)  # Battery discharging
    BYPASS = ('bypass',)  # Bypass active
    CALIBRATING = ('cal',)  # Calibration in progress
    OFFLINE = ('off',)  # UPS is offline
    OVERLOADED = ('over',)  # UPS is overloaded
    TRIMMING = ('trim',)  # Trimming voltage
    BOOSTING = ('boost',)  # Boosting voltage
    FORCED_SHUTDOWN = ('fsd',)  # Forced shutdown

    def __init__(self, *status_codes: str):
        self.status_codes = status_codes

    @classmethod
    def parse(cls, status_str: str) -> set['UPSStatus']:
        """
        Parse a NUT status string into a set of UPS statuses.
        
        :param status_str: Raw status string from NUT
        :return: Set of matching UPSStatus enums
        """
        status_str = status_str.lower()
        return {
            status for status in cls 
            if any(code in status_str for code in status.status_codes)
        }

    @classmethod
    def is_on_battery(cls, statuses: set['UPSStatus']) -> Optional[bool]:
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

async def bootstrap_nut() -> Tuple[bool, str]:
    """
    Bootstrap the NUT installation and configuration on Linux systems.
    Only proceeds if the UPS device entry doesn't already exist.
    
    :return: Tuple of (success: bool, message: str)
    """
    if sys.platform != "linux":
        return False, "Bootstrap is only supported on Linux systems"
    
    ups_conf_path = "/etc/nut/ups.conf"
    
    # For now we assume that if ups.conf exists we've already bootstrapped the entries
    # We can't read the contents of the file because our non-sudo executing user won't
    # have the permissions to do so
    if os.path.exists(ups_conf_path):
        logger.info("UPS device entry already exists in NUT configuration")
        return True, "NUT already configured with UPS device"

    try:
        # Install NUT
        logger.info("Installing NUT packages...")
        subprocess.run(['sudo', 'apt-get', 'update'], check=True)
        subprocess.run(['sudo', 'apt-get', 'install', '-y', 'nut'], check=True)
        
        # Basic NUT configuration
        nut_conf_path = "/etc/nut/nut.conf"
        upsd_conf_path = "/etc/nut/upsd.conf"
        users_conf_path = "/etc/nut/upsd.users"
        upsmon_conf_path = "/etc/nut/upsmon.conf"
        
        # Create /etc/nut directory if it doesn't exist and set permissions
        logger.info("Setting up NUT configuration directory...")
        subprocess.run(['sudo', 'mkdir', '-p', '/etc/nut'], check=True)
        subprocess.run(['sudo', 'chown', '-R', f'{os.getuid()}:nut', '/etc/nut'], check=True)
        subprocess.run(['sudo', 'chmod', '750', '/etc/nut'], check=True)
        
        # Configure NUT to run in standalone mode
        logger.info("Configuring NUT...")
        with open(nut_conf_path, 'w') as f:
            f.write('MODE=standalone\n')
        
        # Create basic ups.conf with required settings
        with open(ups_conf_path, 'w') as f:
            f.write('[ups]\n')
            f.write('driver = usbhid-ups\n')
            f.write('port = auto\n')
            f.write('desc = "Local UPS"\n')
            # Add polling to ensure driver keeps checking for UPS
            f.write('pollinterval = 2\n')
        
        # Create upsd.conf with network settings
        with open(upsd_conf_path, 'w') as f:
            f.write(f'LISTEN 127.0.0.1 {NUT_SERVER_PORT}\n')
            f.write(f'LISTEN ::1 {NUT_SERVER_PORT}\n')
            f.write('MAXAGE 15\n')
        
        # Create upsd.users with admin user
        with open(users_conf_path, 'w') as f:
            f.write('[admin]\n')
            f.write('password = admin\n')
            f.write('actions = SET\n')
            f.write('instcmds = ALL\n')
            f.write('\n[monmaster]\n')
            f.write('password = monmaster\n')
            f.write('upsmon master\n')
        
        # Create upsmon.conf
        with open(upsmon_conf_path, 'w') as f:
            f.write('MONITOR ups@localhost 1 monmaster monmaster master\n')
            f.write('MINSUPPLIES 1\n')
            f.write('SHUTDOWNCMD "shutdown -h now"\n')
            f.write('POLLFREQ 5\n')
            f.write('POLLFREQALERT 5\n')
            f.write('HOSTSYNC 15\n')
            f.write('DEADTIME 15\n')
        
        # Set correct permissions after writing
        for conf_file in [nut_conf_path, ups_conf_path, upsd_conf_path, users_conf_path, upsmon_conf_path]:
            subprocess.run(['sudo', 'chown', 'root:nut', conf_file], check=True)
            subprocess.run(['sudo', 'chmod', '640', conf_file], check=True)
        
        # Ensure NUT can access USB devices
        logger.info("Setting up USB permissions...")
        subprocess.run(['sudo', 'usermod', '-a', '-G', 'nut', os.getenv('USER')], check=True)
        subprocess.run(['sudo', 'udevadm', 'control', '--reload-rules'], check=True)
        
        # Stop all NUT services first
        logger.info("Stopping NUT services...")
        for service in ['nut-client', 'nut-server', 'nut-driver']:
            try:
                subprocess.run(['sudo', 'systemctl', 'stop', service], check=False)
            except Exception:
                pass  # Ignore errors when stopping services
        
        # Start services in correct order
        # NOTE: sudo upsdrvctl -D start can be used to help debug
        logger.info("Starting NUT services...")
        try:
            # Start driver first and wait
            subprocess.run(['sudo', 'systemctl', 'start', 'nut-driver'], check=True)
            await asyncio.sleep(2)  # Give driver time to detect UPS
            
            # Start server
            subprocess.run(['sudo', 'systemctl', 'start', 'nut-server'], check=True)
            await asyncio.sleep(1)
            
            # Start monitoring
            subprocess.run(['sudo', 'systemctl', 'start', 'nut-client'], check=True)
            
            # Check driver status
            driver_status = subprocess.run(
                ['upsc', 'ups@localhost'],
                capture_output=True,
                text=True,
                check=False
            )
            
            if driver_status.returncode != 0:
                logger.error(f"Driver status check failed:\n{driver_status.stderr}")
                # Get detailed service status
                status_output = subprocess.run(
                    ['systemctl', 'status', 'nut-driver.service'],
                    capture_output=True,
                    text=True,
                    check=False
                ).stdout
                logger.error(f"NUT driver status:\n{status_output}")
                return False, "Failed to detect UPS. Please check USB connection and permissions."
            
            logger.info("UPS detected successfully!")
            return True, "NUT installation and configuration completed successfully"
            
        except subprocess.CalledProcessError as e:
            # Get detailed service status if nut-server fails
            try:
                status_output = subprocess.run(
                    ['systemctl', 'status', 'nut-server.service'],
                    capture_output=True,
                    text=True,
                    check=False
                ).stdout
                logger.error(f"NUT server failed to start. Service status:\n{status_output}")
                
                # Check common issues
                if "no UPS definitions" in status_output:
                    return False, "NUT server failed: No valid UPS definitions found. Please check your UPS connection."
                elif "already running" in status_output:
                    return False, "NUT server failed: Service is already running. Try stopping it first."
                elif "Permission denied" in status_output:
                    return False, "NUT server failed: Permission issues with configuration files."
                else:
                    return False, f"NUT server failed to start: {str(e)}\nCheck logs for details."
            except Exception as status_err:
                logger.error(f"Failed to get service status: {str(status_err)}")
                return False, f"NUT server failed to start and couldn't get status: {str(e)}"
        
    except subprocess.CalledProcessError as e:
        return False, f"Installation failed: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error during installation: {str(e)}"

class UPSMonitor:
    def __init__(self, host: str = "localhost", port: int = NUT_SERVER_PORT, ups_name: str = "ups"):
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
            logger.info(f"Connecting to NUT server at {self.host}:{self.port}")
            client = await asyncio.to_thread(nut.PyNUTClient, host=self.host, port=self.port)
            logger.debug("Connected to NUT server, fetching variables...")
            vars = await asyncio.to_thread(client.list_vars, self.ups_name)
            if not vars:
                logger.warning(f"No variables returned for UPS '{self.ups_name}'")
            else:
                logger.debug(f"Retrieved {len(vars)} variables from UPS")
            self._last_status = vars
            return vars
        except Exception as e:
            logger.error(f"Error getting UPS status: {str(e)}", exc_info=True)
            return {}

    def _parse_status(self, status: Dict[str, str]) -> Optional[bool]:
        """
        Parse the UPS status to determine if it's on battery.
        
        :param status: Dictionary of UPS variables
        :return: True if on battery, False if on AC, None if unknown
        """
        if not status:
            logger.warning("No status data available to parse")
            return None
            
        # Check ups.status variable
        ups_status = status.get('ups.status', '')
        if not ups_status:
            logger.warning("No 'ups.status' variable found in status data")
            return None

        logger.info(f"Current UPS status: {ups_status}")
        
        # Parse the status string into UPSStatus enums
        statuses = UPSStatus.parse(ups_status)
        if not statuses:
            logger.warning(f"Unknown UPS status value: {ups_status}")
            return None

        # Log all detected statuses
        for status in statuses:
            logger.debug(f"Detected UPS status: {status.name}")
        
        # Determine if we're on battery
        is_battery = UPSStatus.is_on_battery(statuses)
        if is_battery:
            logger.warning("UPS is running on battery power")
        elif is_battery is False:
            logger.info("UPS is running on utility power")
        else:
            logger.warning(f"Could not determine power state from statuses: {[s.name for s in statuses]}")
        
        return is_battery

    def get_status_summary(self, status: Dict[str, str]) -> str:
        """
        Get a human-readable summary of the UPS status.
        
        :param status: Dictionary of UPS variables
        :return: Formatted status summary
        """
        summary = []
        
        battery_charge = status.get('battery.charge')
        if battery_charge:
            summary.append(f"🔋 Battery charge: {battery_charge}%")
        
        runtime = status.get('battery.runtime')
        if runtime:
            minutes = int(runtime) // 60
            summary.append(f"⏱️ Runtime remaining: {minutes} minutes")
            
        load = status.get('ups.load')
        if load:
            summary.append(f"⚡ Load: {load}%")
            
        temperature = status.get('ups.temperature')
        if temperature:
            summary.append(f"🌡️ Temperature: {temperature}°C")
            
        return "\n".join(summary)

    async def poll_status(self, interval_seconds: int = 10):
        """
        Continuously poll the UPS status and print the power state.
        
        :param interval_seconds: Time between status checks in seconds
        """
        logger.info(f"Starting UPS status monitoring for {self.host}")
        while True:
            try:
                logger.debug("Polling UPS status...")
                status = await self.get_status()
                if status:
                    logger.debug(f"Raw UPS status data: {status}")
                is_on_battery = self._parse_status(status)
                
                if is_on_battery is None:
                    logger.warning("Could not determine UPS status - check if UPS is properly connected and detected")
                elif is_on_battery:
                    logger.warning("UPS is on battery power!")
                else:
                    logger.info("UPS is on utility power")
                
                summary = self.get_status_summary(status)
                if summary:
                    logger.info("\n" + summary)
                else:
                    logger.warning("No status summary available - check if UPS is responding with data")
                    
            except Exception as e:
                logger.error(f"Error polling UPS: {str(e)}", exc_info=True)
            
            logger.debug(f"Waiting {interval_seconds} seconds before next poll...")
            await asyncio.sleep(interval_seconds)

async def main():
    # First try to bootstrap NUT if we're on Linux
    if sys.platform == "linux":
        success, message = await bootstrap_nut()
        print(message)
        if not success:
            sys.exit(1)
    
    # Update these values based on your NUT server configuration
    monitor = UPSMonitor(
        host="localhost",
        port=NUT_SERVER_PORT,
        ups_name="ups"
    )
    await monitor.poll_status()
