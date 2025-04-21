import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from bungalo.backups.nas import mount_smb
from bungalo.config import BungaloConfig
from bungalo.logger import CONSOLE
from bungalo.slack import SlackClient


@dataclass
class RemoteConfig:
    """Configuration for a remote sync pair using rclone."""

    src: str
    destinations: List[str]
    platform: str


class RCloneSync:
    """
    Manages remote sync operations using rclone.

    Handles configuration generation and execution of sync operations
    between source and multiple destination paths on specified platforms.
    """

    def __init__(
        self,
        config_path: Path,
        remotes: List[RemoteConfig],
        slack_webhook_url: str | None = None,
    ) -> None:
        """
        Initialize the RClone sync manager.

        Args:
            config_path: Path where rclone config should be written
            remotes: List of remote configurations to process
            slack_webhook_url: Optional Slack webhook for notifications
        """
        self.config_path = config_path
        self.remotes = remotes
        self.slack_client = (
            SlackClient(slack_webhook_url) if slack_webhook_url else None
        )

    def write_config(self) -> None:
        """
        Write the rclone configuration file based on remote definitions.

        Generates and saves an rclone config file with the specified remotes
        in the format that rclone expects.
        """
        config: Dict[str, Any] = {}

        for remote in self.remotes:
            # Create a unique name for each destination
            for idx, dst in enumerate(remote.destinations):
                remote_name = f"{remote.platform}_{idx}"
                config[remote_name] = {
                    "type": remote.platform,
                    "path": dst,
                }

        # Ensure the config directory exists
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        # Write the config in rclone's JSON format
        with open(self.config_path, "w") as f:
            json.dump(config, f, indent=4)

        CONSOLE.print(f"Written rclone config to {self.config_path}")

    async def sync(self, src: str, dst: str) -> None:
        """
        Execute a single rclone sync operation.

        Args:
            src: Source path to sync from
            dst: Destination path to sync to
        """
        cmd = [
            "rclone",
            "sync",
            "--config",
            str(self.config_path),
            src,
            dst,
            "--progress",
        ]

        CONSOLE.print(f"Starting sync from {src} to {dst}")

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = f"Sync failed from {src} to {dst}: {stderr.decode()}"
            CONSOLE.print(error_msg)
            if self.slack_client:
                await self.slack_client.send_message(error_msg)
        else:
            success_msg = f"Successfully synced {src} to {dst}"
            CONSOLE.print(success_msg)
            if self.slack_client:
                await self.slack_client.send_message(success_msg)

    async def sync_all(self, mount_dir: Path) -> None:
        """
        Execute all configured sync operations sequentially.

        Args:
            mount_dir: Path to the mounted NAS directory to use as base path

        Processes each remote configuration and syncs the source
        to all specified destinations one at a time.
        """
        for remote in self.remotes:
            # Resolve source path relative to mount directory if it's a relative path
            src_path = (
                (mount_dir / remote.src)
                if not Path(remote.src).is_absolute()
                else Path(remote.src)
            )

            for dst in remote.destinations:
                try:
                    await self.sync(str(src_path), dst)
                except Exception as e:
                    error_msg = f"Error during sync operation: {str(e)}"
                    CONSOLE.print(error_msg)
                    if self.slack_client:
                        await self.slack_client.send_message(error_msg)


async def main(config: BungaloConfig) -> None:
    """
    Main entry point for remote backup process.

    Args:
        config: Bungalo configuration containing remote sync details

    Continuously runs sync operations every 6 hours, mounting the NAS
    before each sync operation.
    """
    # Convert config to RemoteConfig objects
    remotes = [
        RemoteConfig(
            src=remote.src, destinations=remote.destinations, platform=remote.platform
        )
        for remote in config.remote.remotes
    ]

    rclone = RCloneSync(
        config_path=Path(config.remote.config_path),
        remotes=remotes,
        slack_webhook_url=config.root.slack_webhook_url,
    )

    # Write initial config
    rclone.write_config()

    while True:
        try:
            with mount_smb(
                server=config.nas.ip_address,
                share=config.nas.drive_name,
                username=config.nas.username,
                password=config.nas.password,
                domain=config.nas.domain,
            ) as mount_dir:
                CONSOLE.print(f"SMB share mounted at: {mount_dir}")
                await rclone.sync_all(mount_dir)

        except Exception as e:
            if rclone.slack_client:
                await rclone.slack_client.send_message(
                    f"Error in remote sync: {str(e)}"
                )
            CONSOLE.print(f"Error in remote sync: {str(e)}")

        # Wait 6 hours before next sync
        await asyncio.sleep(6 * 60 * 60)
