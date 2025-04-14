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


class BungaloConfig(BaseSettings):
    root: RootConfig
    nut: NutConfig
    managed_hardware: list[ManagedHardware] = []
