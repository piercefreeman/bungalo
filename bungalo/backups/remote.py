import asyncio
import contextlib
from contextlib import contextmanager
from pathlib import Path
from traceback import format_exc
from typing import Generator, Sequence, assert_never

from pydantic import BaseModel, Field, IPvAnyAddress, model_validator

from bungalo.config.config import BungaloConfig, SyncPair
from bungalo.config.endpoints import B2Endpoint, EndpointBase, NASEndpoint
from bungalo.config.paths import FileLocation
from bungalo.constants import DEFAULT_RCLONE_CONFIG_FILE
from bungalo.logger import CONSOLE, LOGGER
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
            lines.append(f"{key} = {value}")

        lines.append("")  # Empty line after section
        return lines


class SMBRemote(RemoteBase):
    type: str = "smb"
    host: IPvAnyAddress | str
    user: str
    password: str = Field(serialization_alias="pass")
    domain: str | None = None

    @model_validator(mode="after")
    def validate_host(cls, value):  # noqa: N805
        if not value.host:
            raise ValueError("SMB remote must include 'host'")
        return value


class B2Remote(RemoteBase):
    type: str = "b2"
    account: str
    key: str


class EncryptedRemote(RemoteBase):
    type: str = "crypt"
    remote: str
    password: str
    directory_name_encryption: str = "off"


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

    async def write_config(self) -> None:
        config_lines = []
        for nickname, endpoint in self.endpoints.items():
            remote: RemoteBase
            match endpoint:
                case NASEndpoint():
                    remote = SMBRemote(
                        name=nickname,
                        host=endpoint.ip_address,
                        user=endpoint.username,
                        password=await self._encrypt_key(
                            endpoint.password.get_secret_value()
                        ),
                        domain=endpoint.domain,
                    )
                case B2Endpoint():
                    remote = B2Remote(
                        name=nickname,
                        account=endpoint.key_id,
                        key=endpoint.application_key.get_secret_value(),
                    )
                case EndpointBase():
                    raise ValueError(
                        f"Unsupported base endpoint type: {type(endpoint)}"
                    )
                case _:
                    assert_never(endpoint)

            if endpoint.encrypt_key:
                raw_remote = remote.name
                remote.name = f"{raw_remote}-raw"
                encrypted_remote = EncryptedRemote(
                    name=raw_remote,
                    remote=f"{remote.name}:",
                    password=await self._encrypt_key(
                        endpoint.encrypt_key.get_secret_value()
                    ),
                    # When enabled, we can't pass through our raw bucket name with the crypt->b2
                    # chained backends. It will try to create a new bucket with the encrypted
                    # name instead. We could address by specifying an encrypted remote for each
                    # separate bucket we want to target, but there's not much security risk to
                    # keeping our directory names in plaintext.
                    directory_name_encryption="false",
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
                    CONSOLE.print(f"Syncing {resolved_src} → {resolved_dst}")
                    await self._run_sync(resolved_src, resolved_dst)
                    await self._alert(f"Synced {resolved_src} → {resolved_dst}")
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
            "--stats",
            "30s",  # emits one stats line every 30 s
            "--use-json-log",
            "--log-format",
            "time,level,msg",
            "--stats-log-level",
            "NOTICE",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def _pipe_reader(name: str, stream: asyncio.StreamReader) -> None:
            async for line in stream:
                # TODO: Parse the JSON log output and write status updates within
                # a slack thread (use a single contextmanager to wrap the slack
                # thread context)
                LOGGER.info("%s: %s", name, line.decode().rstrip())

        if not process.stdout or not process.stderr:
            raise RuntimeError("rclone failed to start")

        # Start readers immediately
        readers = [
            asyncio.create_task(_pipe_reader("STDOUT", process.stdout)),
            asyncio.create_task(_pipe_reader("STDERR", process.stderr)),
        ]

        # Wait for rclone to exit
        returncode = await process.wait()

        # Make sure all remaining lines are consumed
        await asyncio.gather(*readers, return_exceptions=True)

        if returncode:
            raise RuntimeError(f"rclone exited with code {returncode}")

    async def _alert(self, message: str) -> None:
        CONSOLE.print(message)
        if self.slack_client:
            await self.slack_client.create_status(message)

    async def _encrypt_key(self, key: str) -> str:
        """
        Use rclone to encrypt a key. This will use an rclone-internal reversable
        encryption cypher to encrypt the key, then base64 encode the result.

        This prevents "shoulder surfing" of the raw password, not actually leaking
        it to an outside observer. This central rclone cypher is baked into the executable
        so generated passwords will still be readable across docker container executions.

        All "pass", "password", "client_secret" fields need to be encrypted with this function.

        """
        process = await asyncio.create_subprocess_exec(
            "rclone",
            "obscure",
            key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode:
            raise RuntimeError(stderr.decode() or stdout.decode())
        return stdout.decode()


def validate_endpoints(endpoints: Sequence[EndpointBase]) -> dict[str, EndpointBase]:
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
        slack_client=SlackClient(
            app_token=config.slack.app_token,
            bot_token=config.slack.bot_token,
            channel_id=config.slack.channel,
        ),
    )
    await rclone_sync.write_config()

    while True:
        try:
            await rclone_sync.sync_all()
        except Exception as exc:
            CONSOLE.print(f"Sync failed: {exc}")
            if rclone_sync.slack_client:
                await rclone_sync.slack_client.create_status(
                    f"Sync round failed: {exc}"
                )
        await asyncio.sleep(config.backups.interval.total_seconds())
