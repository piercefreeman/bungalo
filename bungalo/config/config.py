from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

from bungalo.config.endpoints import NASEndpoint, R2Endpoint
from bungalo.config.paths import FileLocation


class RootConfig(BaseSettings):
    slack_webhook_url: str


class NutConfig(BaseSettings):
    shutdown_threshold: int = 20  # Shutdown when battery below 20%
    startup_threshold: int = 50  # Start back up when battery above 50%


class iPhotoBackupConfig(BaseSettings):
    username: str
    password: str
    client_id: str | None = None
    album_name: str = "All Photos"
    photo_size: str = "original"
    output_directory: FileLocation


class RemoteSync(BaseSettings):
    src: FileLocation
    dst: FileLocation


class RemoteBackupConfig(BaseSettings):
    sync: list[RemoteSync]


class EndpointConfig(BaseSettings):
    r2: list[R2Endpoint] = []
    nas: list[NASEndpoint] = []


class BungaloConfig(BaseSettings):
    # General ungrouped configuration
    root: RootConfig

    # Power management
    nut: NutConfig = Field(default_factory=NutConfig)

    # Backups
    iphoto: iPhotoBackupConfig
    remote: RemoteBackupConfig

    # Storage locations
    endpoints: EndpointConfig = Field(default_factory=EndpointConfig)

    # Validate that all of the remote files that were validated to NAS files or
    # R2 accounts match the nicknames that we have specified
    @model_validator(mode="after")
    def validate_remote_files(self):
        for sync in self.remote.sync:
            sync.src.validate()
            sync.dst.validate()
