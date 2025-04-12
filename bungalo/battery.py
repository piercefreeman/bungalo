import asyncio
import subprocess
from datetime import datetime
from typing import Dict, Optional, Tuple
import logging
import nut2 as nut
import sys
import os

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def bootstrap_nut() -> Tuple[bool, str]:
    """
    Bootstrap the NUT installation and configuration on Linux systems.
    
    :return: Tuple of (success: bool, message: str)
    """
    if sys.platform != "linux":
        return False, "Bootstrap is only supported on Linux systems"
    
    try:
        # Install NUT
        logger.info("Installing NUT packages...")
        subprocess.run(['sudo', 'apt-get', 'update'], check=True)
        subprocess.run(['sudo', 'apt-get', 'install', '-y', 'nut'], check=True)
        
        # Basic NUT configuration
        nut_conf_path = "/etc/nut/nut.conf"
        ups_conf_path = "/etc/nut/ups.conf"
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
            f.write('LISTEN 127.0.0.1 3493\n')
            f.write('LISTEN ::1 3493\n')
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
    def __init__(self, host: str = "localhost", port: int = 3493, ups_name: str = "ups"):
        """
        Initialize the UPS monitor using NUT (Network UPS Tools).
        
        :param host: Hostname or IP of the NUT server
        :param port: Port number of the NUT server (default: 3493)
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
            client = await asyncio.to_thread(nut.PyNUTClient, host=self.host, port=self.port)
            vars = await asyncio.to_thread(client.list_vars, self.ups_name)
            self._last_status = vars
            return vars
        except Exception as e:
            logger.error(f"Error getting UPS status: {e}")
            return {}

    def _parse_status(self, status: Dict[str, str]) -> Optional[bool]:
        """
        Parse the UPS status to determine if it's on battery.
        
        :param status: Dictionary of UPS variables
        :return: True if on battery, False if on AC, None if unknown
        """
        if not status:
            return None
            
        # Check ups.status variable
        ups_status = status.get('ups.status', '').lower()
        if 'onbatt' in ups_status:
            return True
        elif 'online' in ups_status:
            return False
        return None

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
                status = await self.get_status()
                is_on_battery = self._parse_status(status)
                
                if is_on_battery is None:
                    logger.warning("Could not determine UPS status")
                elif is_on_battery:
                    logger.warning("UPS is on battery power!")
                else:
                    logger.info("UPS is on utility power")
                
                summary = self.get_status_summary(status)
                if summary:
                    logger.info("\n" + summary)
                    
            except Exception as e:
                logger.error(f"Error polling UPS: {e}")
            
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
        host="localhost",  # Change this to your NUT server address
        port=3493,
        ups_name="ups"    # Change this to match your UPS name in NUT
    )
    await monitor.poll_status()

if __name__ == "__main__":
    asyncio.run(main()) 