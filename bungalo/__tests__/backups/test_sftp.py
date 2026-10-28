import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock

import asyncssh
import pytest

from bungalo.backups.remote import RCloneSync
from bungalo.config.config import SyncPair
from bungalo.config.endpoints import SFTPEndpoint


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "host_key", "client_key"])
async def test_real_sftp_sync(tmp_path: Path, failure: str | None) -> None:
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    (source / "data" / "report.txt").write_text("remote backup")
    destination = tmp_path / "destination"
    destination.mkdir()
    stale_file = destination / "stale.txt"
    stale_file.write_text("old backup")

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    authorized_keys = tmp_path / "authorized_keys"
    authorized_keys.write_bytes(client_key.export_public_key())
    key_file = tmp_path / "private key"
    key_file.write_bytes(
        (
            asyncssh.generate_private_key("ssh-ed25519")
            if failure == "client_key"
            else client_key
        ).export_private_key()
    )
    key_file.chmod(0o600)

    async with await asyncssh.create_server(
        asyncssh.SSHServer,
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=str(authorized_keys),
        sftp_factory=lambda channel: asyncssh.SFTPServer(
            channel, chroot=os.fsencode(source)
        ),
    ) as server:
        known_hosts = tmp_path / "known_hosts"
        trusted_key = (
            asyncssh.generate_private_key("ssh-ed25519")
            if failure == "host_key"
            else host_key
        )
        known_hosts.write_text(
            f"[127.0.0.1]:{server.get_port()} "
            f"{trusted_key.export_public_key().decode()}"
        )
        endpoint = SFTPEndpoint(
            nickname="server",
            host="127.0.0.1",
            username="backup",
            port=server.get_port(),
            key_file=str(key_file),
            known_hosts_file=str(known_hosts),
        )
        sync = RCloneSync(
            config_path=tmp_path / "rclone.conf",
            endpoints={endpoint.nickname: endpoint},
            pairs=[
                SyncPair.model_validate(
                    {
                        "src": "sftp:server:///data",
                        "dst": f"file:local://{destination}",
                    }
                )
            ],
            slack_client=AsyncMock(),
            custom_rclone_args=[
                "--retries",
                "1",
                "--low-level-retries",
                "1",
                "--contimeout",
                "2s",
            ],
        )
        await sync.write_config()
        success = await asyncio.wait_for(sync.sync_all(), timeout=15)

        assert success is (failure is None)
        if failure:
            assert stale_file.read_text() == "old backup"
            assert not (destination / "report.txt").exists()
        else:
            assert (destination / "report.txt").read_text() == "remote backup"
            assert not stale_file.exists()
    assert (source / "data" / "report.txt").read_text() == "remote backup"
