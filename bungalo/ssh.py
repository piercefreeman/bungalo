import asyncio
import os
import subprocess
from contextlib import asynccontextmanager
from typing import Optional

from asyncssh import connect as asyncssh_connect

from bungalo.constants import DEFAULT_SSH_KEY_PATH
from bungalo.logger import CONSOLE, LOGGER


class SSHManager:
    """
    Provides a single, Bungalo-managed SSH key. Expects any managed machines to have
    this key added to their authorized_keys file.

    """

    def __init__(self, key_path: str = DEFAULT_SSH_KEY_PATH):
        """
        Initialize the SSH key manager.

        :param key_path: Path to the SSH private key file
        """
        # Expand the path to handle ~
        self.key_path = os.path.expanduser(key_path)
        self.pub_key_path = f"{self.key_path}.pub"

    async def generate_key(self) -> bool:
        """
        Generate a new SSH key pair if it doesn't exist.

        :return: True if key was generated, False if it already exists
        """
        if os.path.exists(self.key_path):
            LOGGER.info(f"SSH key already exists at {self.key_path}")
            return False

        # Ensure .ssh directory exists
        ssh_dir = os.path.dirname(self.key_path)
        os.makedirs(ssh_dir, mode=0o700, exist_ok=True)

        try:
            LOGGER.info("Generating new SSH key pair...")
            # Run ssh-keygen non-interactively
            process = await asyncio.create_subprocess_exec(
                "ssh-keygen",
                "-t",
                "rsa",
                "-f",
                self.key_path,
                "-N",
                "",  # Empty passphrase
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                LOGGER.error(f"Failed to generate SSH key: {stderr.decode()}")
                return False

            LOGGER.info("SSH key pair generated successfully")
            return True

        except Exception as e:
            LOGGER.error(f"Error generating SSH key: {str(e)}")
            return False

    async def read_public_key(self) -> Optional[str]:
        """
        Read the contents of the public SSH key file.

        :return: The public key contents or None if not found
        """
        try:
            if not os.path.exists(self.pub_key_path):
                LOGGER.warning(f"Public key not found at {self.pub_key_path}")
                return None

            with open(self.pub_key_path, "r") as f:
                key_content = f.read().strip()
                LOGGER.debug(f"Read public key from {self.pub_key_path}")
                return key_content

        except Exception as e:
            LOGGER.error(f"Error reading public key: {str(e)}")
            return None

    @asynccontextmanager
    async def connect(self, hostname: str, username: str, timeout: float = 10):
        """
        Using our managed SSH key, connect to a remote host using SSH.
        Note: This connection skips host key verification for convenience.
        Use with caution as this reduces security.

        :param hostname: The hostname of the remote host
        :param username: The username to connect with
        :param timeout: Connection timeout in seconds (default: 10)
        :raises: asyncio.TimeoutError if connection times out
        :return: An AsyncSSHClient object
        """
        try:
            conn = await asyncio.wait_for(
                asyncssh_connect(
                    hostname,
                    username=username,
                    client_keys=[self.key_path],
                    known_hosts=None,  # Disable host key checking
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            LOGGER.error(
                f"SSH connection to {hostname} timed out after {timeout} seconds"
            )
            raise

        async with conn:
            yield conn


async def main() -> None:
    """
    Main entry point for the SSH manager.
    Generates a key if it doesn't exist and displays the public key.
    Also provides instructions for adding the key to UniFi Network.
    """
    manager = SSHManager()

    # Generate key if it doesn't exist
    await manager.generate_key()

    # Read and display the public key
    public_key = await manager.read_public_key()
    if public_key:
        CONSOLE.print("[green]Your SSH public key:[/green]")
        CONSOLE.print(public_key)

        # Display UniFi Network instructions
        CONSOLE.print("\n[yellow]To add this key to UniFi Network:[/yellow]")
        CONSOLE.print("1. Assign your device a known ssh password")
        CONSOLE.print(
            "2.a Navigate to Settings > Control Panel > Console > SSH Keys to set the root ssh password"
        )
        CONSOLE.print(
            "2.b Navigate to Settings > Control Panel > Controls > About This Console to get the local IP"
        )
        CONSOLE.print("3. Copy over the public key to the ~/.ssh/authorized_keys file")
        CONSOLE.print("3a. ssh-copy-id -i ~/.bungalo/id_rsa.pub root@<local-ip>")
    else:
        CONSOLE.print("[red]Failed to read SSH public key[/red]")


if __name__ == "__main__":
    asyncio.run(main())
