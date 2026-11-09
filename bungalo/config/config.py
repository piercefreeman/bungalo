import re
from datetime import timedelta
from typing import Literal, Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings

from bungalo.config.endpoints import B2Endpoint, EndpointBase, NASEndpoint, SFTPEndpoint
from bungalo.config.paths import B2Path, FileLocation, NASPath, SFTPPath


class RootConfig(BaseSettings):
    """
    General-purpose configuration that applies to the whole Bungalo runtime.
    """

    self_ip: str | None = None


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
            raise ValueError(
                "container_path must be an absolute path starting with '/'"
            )
        return value


class MediaServerConfig(BaseSettings):
    plugin: Literal["jellyfin"]
    transcode: NASPath
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


class HomeAssistantConfig(BaseSettings):
    enabled: bool = False
    port: int = 8123


class MediaIngestTranscodeConfig(BaseModel):
    template: str
    """Path on the Bungalo server to a shell script using "$INPUT" and "$OUTPUT"."""

    output_suffix: str
    """Replaces the source's extension to name the output, like "_HQ.mp4"."""

    work_dir: str = "/tmp/bungalo-media-ingest"
    """Folder on the box for output while it waits to upload."""

    volumes: list[str] | None = Field(default=None, min_length=1)
    """Names from media_ingest.volumes to convert; left out, every one of them is."""

    @field_validator("output_suffix")
    @classmethod
    def validate_output_suffix(cls, value: str) -> str:
        if not re.fullmatch(r"[^/\r\n]*\.[A-Za-z0-9]+", value):
            raise ValueError("output_suffix must end in an extension, like _HQ.mp4")
        return value

    @field_validator("work_dir")
    @classmethod
    def validate_work_dir(cls, value: str) -> str:
        if not value.startswith("/") or value.rstrip("/") == "":
            raise ValueError("work_dir must be an absolute folder below /")
        return value.rstrip("/")


class MediaIngestConfig(BaseModel):
    ssh_endpoint: str
    """Nickname of the [[endpoints.sftp]] entry for the Mac holding the footage."""

    studio_url: str
    """Origin of the Studio that upload links in Slack comments must belong to."""

    volumes: list[str] = Field(min_length=1)
    """Volume names to scan, like "CARD", however macOS suffixes their mount points."""

    interval: timedelta = Field(default=timedelta(minutes=5), gt=timedelta(0))
    """How often to SSH in and look through /Volumes for new videos."""

    reply_interval: timedelta = Field(default=timedelta(seconds=30), gt=timedelta(0))
    """How often to check open contact sheet threads for comments."""

    transcode: MediaIngestTranscodeConfig | None = None
    """Convert each selected video on the box before uploading it."""

    @model_validator(mode="after")
    def validate_transcode_volumes(self):
        listed = self.transcode.volumes if self.transcode else None
        unknown = sorted(set(listed or []) - set(self.volumes))
        if unknown:
            raise ValueError(
                f"media_ingest.transcode.volumes has {unknown}, "
                "which media_ingest.volumes doesn't scan"
            )
        return self

    @field_validator("volumes")
    @classmethod
    def validate_volume_names(cls, value: list[str]) -> list[str]:
        for name in value:
            if (
                not name.strip()
                or name in (".", "..")
                or any(character in name for character in "/\t\r\n")
            ):
                raise ValueError(f"'{name}' is not a volume name under /Volumes")
        return value

    @field_validator("studio_url")
    @classmethod
    def validate_studio_url(cls, value: str) -> str:
        value = value.rstrip("/")
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.path:
            raise ValueError(
                "studio_url must be an origin, like https://studio.example.com"
            )
        return value

    def transcode_for(self, volume_name: str) -> MediaIngestTranscodeConfig | None:
        """How to convert footage from this volume, if it should be converted."""
        transcode = self.transcode
        if transcode and (
            transcode.volumes is None or volume_name in transcode.volumes
        ):
            return transcode
        return None


class EndpointConfig(BaseSettings):
    b2: list[B2Endpoint] = []
    nas: list[NASEndpoint] = []
    sftp: list[SFTPEndpoint] = []

    def get_all(self) -> Sequence[EndpointBase]:
        return self.b2 + self.nas + self.sftp


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

    # Media servers (e.g., Jellyfin)
    media_server: MediaServerConfig | None = None

    # Home automation
    home_assistant: HomeAssistantConfig = Field(default_factory=HomeAssistantConfig)

    # Footage on a remote Mac, offered for upload through Slack
    media_ingest: MediaIngestConfig | None = None

    @model_validator(mode="after")
    def _validate_media_ingest_endpoint(self):
        if self.media_ingest and not any(
            endpoint.nickname == self.media_ingest.ssh_endpoint
            for endpoint in self.endpoints.sftp
        ):
            raise ValueError(
                f"media_ingest.ssh_endpoint '{self.media_ingest.ssh_endpoint}' "
                "does not match any configured SFTP endpoint nickname"
            )
        return self

    @model_validator(mode="after")
    def _validate_file_locations(self):
        """
        Walk the *entire* model tree, find every remote path, and
        ensure at least one matching endpoint accepts it.
        """

        def walk(obj):
            """Yield every remote FileLocation object in `obj`."""
            if isinstance(obj, (B2Path, NASPath, SFTPPath)):
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

        endpoints = self.endpoints.get_all()
        for loc in walk(self):
            if not any(ep.validate_path(loc) for ep in endpoints):
                raise ValueError(
                    f"File location {loc!s} does not match any configured "
                    f"{str(loc).split(':', 1)[0].upper()} endpoint nickname"
                )

        return self
