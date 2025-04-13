from dataclasses import dataclass

from wakeonlan import send_magic_packet

from bungalo.logger import CONSOLE
from bungalo.ssh import SSHManager


@dataclass
class ClientMachine:
    hostname: str
    username: str
    mac_address: str | None = None
    supports_wake_on_lan: bool = False


class ClientManager:
    def __init__(self, clients: list[ClientMachine]):
        """
        Initialize the client manager with a list of client machines to manage.

        :param clients: List of ClientMachine objects representing the machines to manage
        """
        self.clients = clients

    async def shutdown_clients(self) -> None:
        """
        SSH into each client machine and initiate a shutdown.
        Uses SSL certificates for authentication.
        """
        for client in self.clients:
            try:
                async with SSHManager().connect(
                    client.hostname,
                    client.username,
                ) as conn:
                    # Use shutdown command appropriate for the client OS
                    # This assumes Linux/Unix-like systems
                    await conn.run("sudo shutdown -h now")
            except Exception as e:
                CONSOLE.print(f"Failed to shutdown {client.hostname}: {str(e)}")

    async def wake_clients(self) -> None:
        """
        Send Wake-on-LAN magic packets to wake up all client machines.
        """
        for client in self.clients:
            if not client.supports_wake_on_lan:
                CONSOLE.print(
                    f"Skipping {client.hostname} because it doesn't support Wake-on-LAN"
                )
                continue
            if not client.mac_address:
                CONSOLE.print(
                    f"Skipping {client.hostname} because it doesn't have a MAC address"
                )
                continue

            try:
                # Send magic packet to wake the machine
                send_magic_packet(client.mac_address)
            except Exception as e:
                CONSOLE.print(f"Failed to wake {client.hostname}: {str(e)}")
