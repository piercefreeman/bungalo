from pydantic_settings import BaseSettings, Field


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


class BungaloConfig(BaseSettings):
    root: RootConfig = Field(default_factory=RootConfig)
    nut: NutConfig = Field(default_factory=NutConfig)
    nas: NASConfig = Field(default_factory=NASConfig)
    iphoto: iPhotoBackupConfig = Field(default_factory=iPhotoBackupConfig)
    managed_hardware: list[ManagedHardware] = []
