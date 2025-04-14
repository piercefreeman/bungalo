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

    async def healthcheck(self) -> dict[str, bool]:
        """
        Perform a health check by attempting to connect to each registered client.

        :return: Dictionary mapping 'hostname:username' to connection success status
        """
        results: dict[str, bool] = {}
        ssh_manager = SSHManager()

        for client in self.clients:
            key = f"{client.hostname}:{client.username}"
            try:
                async with ssh_manager.connect(
                    client.hostname, client.username
                ) as conn:
                    # Try to execute a simple command to verify connection
                    await conn.run('echo "Connection test"', timeout=10)
                    results[key] = True
                    CONSOLE.print(
                        f"[green]SSH health check succeeded for {key}[/green]"
                    )
            except Exception as e:
                results[key] = False
                CONSOLE.print(f"[red]SSH health check failed for {key}: {str(e)}[/red]")

        return results
