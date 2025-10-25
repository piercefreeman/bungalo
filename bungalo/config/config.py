from datetime import timedelta
from typing import Literal, Sequence

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings

from bungalo.config.endpoints import B2Endpoint, EndpointBase, NASEndpoint
from bungalo.config.paths import B2Path, FileLocation, NASPath


class RootConfig(BaseSettings):
    pass


class ManagedHardware(BaseSettings):
    name: str
    local_ip: str
    username: str


class NutConfig(BaseSettings):
    shutdown_threshold: int = 20  # Shutdown when battery below 20%
    startup_threshold: int = 50  # Start back up when battery above 50%
    managed_hardware: list[ManagedHardware] = []
    bootstrap_retry_interval: timedelta = timedelta(
        minutes=5
    )  # Retry bootstrap every 5 minutes on failure


class iPhotoBackupConfig(BaseSettings):
    username: str
    password: str
    client_id: str | None = None
    photo_size: str = "original"

    # For the time being we only support NAS output. From there we use
    # rclone to sync to B2 or another remote location.
    output: NASPath

    interval: timedelta = timedelta(hours=24)


class SyncPair(BaseSettings):
    src: FileLocation
    dst: FileLocation


class RemoteBackupConfig(BaseSettings):
    sync: list[SyncPair]
    interval: timedelta = timedelta(hours=6)


class MediaServerMount(BaseModel):
    name: str
    path: NASPath
    container_path: str | None = None

    @field_validator("container_path")
    @classmethod
    def validate_container_path(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("/"):
            raise ValueError("container_path must be an absolute path starting with '/'")
        return value


class MediaServerConfig(BaseSettings):
    plugin: Literal["plex"]
    mounts: list[MediaServerMount] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_mounts(self):
        names = [mount.name for mount in self.mounts]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate media server mount names are not allowed")

        container_paths: set[str] = set()
        for mount in self.mounts:
            container_path = mount.container_path or f"/data/{mount.name}"
            if container_path in container_paths:
                raise ValueError(
                    f"Duplicate container mount path detected: {container_path}"
                )
            container_paths.add(container_path)
        return self


class EndpointConfig(BaseSettings):
    b2: list[B2Endpoint] = []
    nas: list[NASEndpoint] = []

    def get_all(self) -> Sequence[EndpointBase]:
        return self.b2 + self.nas


class SlackConfig(BaseSettings):
    app_token: str
    bot_token: str
    channel: str

    @field_validator("channel")
    @classmethod
    def validate_channel_format(cls, channel: str) -> str:
        # Check for hashtag prefix
        if channel.startswith("#"):
            return channel

        # Check for Slack conversation ID format
        if (
            len(channel) >= 9
            and channel[0] in ["C", "G", "D"]
            and channel[1:].isalnum()
        ):
            return channel

        raise ValueError(
            "Channel must either start with '#' or be a valid Slack conversation ID "
            "(starting with C, G, or D followed by alphanumeric characters)"
        )


class BungaloConfig(BaseSettings):
    # General ungrouped configuration
    root: RootConfig = RootConfig()

    # Notifications
    slack: SlackConfig

    # Power management
    nut: NutConfig = Field(default_factory=NutConfig)

    # Backups
    iphoto: iPhotoBackupConfig | None = None
    backups: RemoteBackupConfig

    # Storage locations
    endpoints: EndpointConfig = Field(default_factory=EndpointConfig)

    # Media servers (e.g., Plex)
    media_server: MediaServerConfig | None = None

    # Validate that all of the remote files that were validated to NAS files or
    # B2 accounts match the nicknames that we have specified
    @model_validator(mode="after")
    def _validate_file_locations(self):
        """
        Walk the *entire* model tree, find every `B2Path`/`NASPath`, and
        ensure at least one matching endpoint accepts it.
        """

        def walk(obj):
            """Yield every FileLocation object (B2Path | NASPath) in `obj`."""
            if isinstance(obj, (B2Path, NASPath)):
                yield obj
            elif isinstance(obj, BaseModel):
                for field in obj.__class__.model_fields:
                    yield from walk(getattr(obj, field))
            elif isinstance(obj, (list, tuple, set)):
                for item in obj:
                    yield from walk(item)
            elif isinstance(obj, dict):
                for val in obj.values():
                    yield from walk(val)

        for loc in walk(self):
            endpoints: Sequence[B2Endpoint | NASEndpoint] = (
                self.endpoints.b2 if isinstance(loc, B2Path) else self.endpoints.nas
            )

            if not any(ep.validate_path(loc) for ep in endpoints):
                raise ValueError(
                    f"File location {loc!s} does not match any configured "
                    f"{'B2' if isinstance(loc, B2Path) else 'NAS'} endpoint nickname"
                )

        return self
