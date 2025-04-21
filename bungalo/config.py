from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

from bungalo.paths import FileLocation


class ManagedHardware(BaseSettings):
    name: str
    local_ip: str
    username: str


class RootConfig(BaseSettings):
    slack_webhook_url: str


class NutConfig(BaseSettings):
    shutdown_threshold: int = 20  # Shutdown when battery below 20%
    startup_threshold: int = 50  # Start back up when battery above 50%


class NASConfig(BaseSettings):
    nickname: str
    ip_address: str
    username: str
    password: str
    # drive_name: str
    domain: str = "WORKGROUP"


class iPhotoBackupConfig(BaseSettings):
    username: str
    password: str
    client_id: str | None = None
    album_name: str = "All Photos"
    photo_size: str = "original"
    output_directory: FileLocation


class R2Account(BaseSettings):
    nickname: str
    api_key: str


class RemoteSync(BaseSettings):
    src: FileLocation
    dst: FileLocation


class RemoteBackupConfig(BaseSettings):
    accounts: list[R2Account]
    sync: list[RemoteSync]


class BungaloConfig(BaseSettings):
    root: RootConfig
    nut: NutConfig = Field(default_factory=NutConfig)
    nas: NASConfig
    iphoto: iPhotoBackupConfig
    remote: RemoteBackupConfig
    managed_hardware: list[ManagedHardware] = []

    # Validate that all of the remote files that were validated to NAS files or
    # R2 accounts match the nicknames that we have specified
    @model_validator(mode="after")
    def validate_remote_files(self):
        for sync in self.remote.sync:
            sync.src.validate()
            sync.dst.validate()
