import asyncio
import contextlib
from base64 import b64encode
from contextlib import contextmanager
from inspect import isclass
from pathlib import Path
from traceback import format_exc
from typing import Generator, assert_never

from pydantic import BaseModel, Field, IPvAnyAddress, SecretStr, model_validator

from bungalo.config.config import BungaloConfig, SyncPair
from bungalo.config.endpoints import B2Endpoint, EndpointBase, NASEndpoint
from bungalo.config.paths import FileLocation
from bungalo.constants import DEFAULT_RCLONE_CONFIG_FILE
from bungalo.logger import CONSOLE
from bungalo.slack import SlackClient


class RemoteBase(BaseModel):
    """
    Definition that mirrors the Rclone remote definition. Used for validating
    the remote configuration before syncing.

    """

    name: str = Field(..., pattern=r"^[A-Za-z0-9_\-]+$")
    type: str

    def to_rclone_config(self) -> list[str]:
        """Convert the remote configuration to rclone's native format."""
        lines = [f"[{self.name}]"]
        config_dict = self.model_dump(
            exclude={"name"}, mode="json", by_alias=True, exclude_none=True
        )

        for key, value in config_dict.items():
            field_definition = self.__class__.model_fields.get(key)
            if (
                field_definition
                and isclass(field_definition.annotation)
                and issubclass(field_definition.annotation, SecretStr)
            ):
                raw_value = getattr(self, key)
                value = raw_value.get_secret_value()
            lines.append(f"{key} = {value}")

        lines.append("")  # Empty line after section
        return lines


class SMBRemote(RemoteBase):
    type: str = "smb"
    host: IPvAnyAddress | str
    user: str
    password: SecretStr
    domain: str | None = None

    @model_validator(mode="after")
    def validate_host(cls, value):  # noqa: N805
        if not value.host:
            raise ValueError("SMB remote must include 'host'")
        return value


class B2Remote(RemoteBase):
    type: str = "s3"
    provider: str = "Backblaze"
    access_key_id: str
    secret_access_key: SecretStr


class EncryptedRemote(RemoteBase):
    type: str = "crypt"
    remote: str
    password: SecretStr


class RCloneSync:
    def __init__(
        self,
        config_path: Path,
        endpoints: dict[str, EndpointBase],
        pairs: list[SyncPair],
        slack_client: SlackClient | None = None,
    ) -> None:
        self.config_path = config_path
        self.endpoints = endpoints
        self.pairs = pairs
        self.slack_client = slack_client

    def write_config(self) -> None:
        config_lines = []
        for nickname, endpoint in self.endpoints.items():
            remote: RemoteBase
            if isinstance(endpoint, NASEndpoint):
                remote = SMBRemote(
                    name=nickname,
                    host=endpoint.ip_address,
                    user=endpoint.username,
                    password=endpoint.password,
                    domain=endpoint.domain,
                )

            elif isinstance(endpoint, B2Endpoint):
                remote = B2Remote(
                    name=nickname,
                    access_key_id=endpoint.key_id,
                    secret_access_key=endpoint.application_key,
                )
            else:
                assert_never(endpoint)

            if endpoint.encrypt_key:
                raw_remote = remote.name
                remote.name = f"{raw_remote}-raw"
                encrypted_remote = EncryptedRemote(
                    name=raw_remote,
                    remote=remote.name,
                    password=b64encode(endpoint.encrypt_key.encode()).decode(),
                )
                config_lines.extend(encrypted_remote.to_rclone_config())

            config_lines.extend(remote.to_rclone_config())

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text("".join(f"{line}\n" for line in config_lines))
        CONSOLE.print(f"rclone config written → {self.config_path}")

    async def sync_all(self) -> None:
        for pair in self.pairs:
            try:
                with self._pair_context(pair) as (resolved_src, resolved_dst):
                    await self._run_sync(resolved_src, resolved_dst)
            except Exception as exc:
                CONSOLE.print(f"Traceback: {format_exc()}")
                await self._alert(f"Sync {pair.src} → {pair.dst} failed: {exc}")

    @contextmanager
    def _pair_context(self, pair: SyncPair) -> Generator[tuple[str, str], None, None]:
        with contextlib.ExitStack() as stack:
            resolved_src = stack.enter_context(self._location_context(pair.src))
            resolved_dst = stack.enter_context(self._location_context(pair.dst))
            yield resolved_src, resolved_dst

    @contextmanager
    def _location_context(self, location: FileLocation) -> Generator[str, None, None]:
        endpoint = self.endpoints[location.endpoint_nickname]

        # Handle additional processing necessary to prepare the FileLocation for transfer
        # We previously used this for SMB mounts via mount_smb, but now we just configure
        # directly in rclone.

        yield f"{endpoint.nickname}:{location.full_path}"

    async def _run_sync(self, src: str, dst: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "rclone",
            "sync",
            "--config",
            str(self.config_path),
            src,
            dst,
            "--progress",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode:
            raise RuntimeError(stderr.decode() or stdout.decode())
        await self._alert(f"Synced {src} → {dst}")

    async def _alert(self, message: str) -> None:
        CONSOLE.print(message)
        if self.slack_client:
            await self.slack_client.send_message(message)


def validate_endpoints(endpoints: list[EndpointBase]) -> dict[str, EndpointBase]:
    """
    Since the client config allows clients to specify their own nicknames for
    endpoints, we need to validate that no two endpoints have the same nickname.

    """
    endpoints_by_nickname: dict[str, EndpointBase] = {}
    for endpoint in endpoints:
        if endpoint.nickname in endpoints_by_nickname:
            raise ValueError(
                f"Duplicate endpoint nickname detected: '{endpoint.nickname}'"
            )
        endpoints_by_nickname[endpoint.nickname] = endpoint
    return endpoints_by_nickname


async def main(config: BungaloConfig) -> None:
    endpoints_by_nickname = validate_endpoints(config.endpoints.get_all())
    sync_pairs = [SyncPair.model_validate(raw_pair) for raw_pair in config.backups.sync]

    rclone_sync = RCloneSync(
        config_path=Path(DEFAULT_RCLONE_CONFIG_FILE).expanduser(),
        endpoints=endpoints_by_nickname,
        pairs=sync_pairs,
        slack_client=(
            SlackClient(config.root.slack_webhook_url)
            if config.root.slack_webhook_url
            else None
        ),
    )
    rclone_sync.write_config()

    while True:
        try:
            await rclone_sync.sync_all()
        except Exception as exc:
            CONSOLE.print(f"Sync failed: {exc}")
            if rclone_sync.slack_client:
                await rclone_sync.slack_client.send_message(f"Sync round failed: {exc}")
        await asyncio.sleep(6 * 60 * 60)
