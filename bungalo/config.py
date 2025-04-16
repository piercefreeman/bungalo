from pydantic import Field
from pydantic_settings import BaseSettings


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
    ip_address: str
    drive_name: str
    username: str
    password: str
    domain: str = "WORKGROUP"


class iPhotoBackupConfig(BaseSettings):
    username: str
    password: str
    client_id: str
    album_name: str = "All Photos"
    photo_size: str = "original"
    output_directory: str


class BungaloConfig(BaseSettings):
    root: RootConfig
    nut: NutConfig = Field(default_factory=NutConfig)
    nas: NASConfig
    iphoto: iPhotoBackupConfig
    managed_hardware: list[ManagedHardware] = []
