import asyncio
import subprocess
from datetime import datetime
from typing import Dict, Optional, Tuple
import logging
import nut2 as nut
import sys
import os

logging.basicConfig(
    level=logging.INFO,
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
        # Check if we have sudo access
        result = subprocess.run(['sudo', '-n', 'true'], capture_output=True)
        if result.returncode != 0:
            return False, "Sudo access is required for installation"
        
        # Install NUT
        logger.info("Installing NUT packages...")
        subprocess.run(['sudo', 'apt-get', 'update'], check=True)
        subprocess.run(['sudo', 'apt-get', 'install', '-y', 'nut'], check=True)
        
        # Basic NUT configuration
        nut_conf_path = "/etc/nut/nut.conf"
        ups_conf_path = "/etc/nut/ups.conf"
        
        # Configure NUT to run in standalone mode
        logger.info("Configuring NUT...")
        with open(nut_conf_path, 'w') as f:
            f.write('MODE=standalone\n')
        
        # Create basic ups.conf if it doesn't exist
        if not os.path.exists(ups_conf_path):
            with open(ups_conf_path, 'w') as f:
                f.write('[ups]\n')
                f.write('driver = usbhid-ups\n')
                f.write('port = auto\n')
                f.write('desc = "Local UPS"\n')
        
        # Set correct permissions
        subprocess.run(['sudo', 'chown', 'root:nut', nut_conf_path], check=True)
        subprocess.run(['sudo', 'chmod', '640', nut_conf_path], check=True)
        subprocess.run(['sudo', 'chown', 'root:nut', ups_conf_path], check=True)
        subprocess.run(['sudo', 'chmod', '640', ups_conf_path], check=True)
        
        # Restart NUT services
        logger.info("Restarting NUT services...")
        subprocess.run(['sudo', 'systemctl', 'restart', 'nut-server'], check=True)
        subprocess.run(['sudo', 'systemctl', 'restart', 'nut-client'], check=True)
        
        return True, "NUT installation and configuration completed successfully"
        
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
            await asyncio.to_thread(client.logout)
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
        logger.info(message)
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