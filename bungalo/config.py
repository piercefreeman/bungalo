from pydantic_settings import BaseSettings

from bungalo.constants import DEFAULT_CONFIG_FILE


class ManagedHardware(BaseSettings):
    name: str
    local_ip: str
    username: str


class BungaloConfig(BaseSettings):
    managed_hardware: list[ManagedHardware]

    nut_shutdown_threshold: int = 20  # Shutdown when battery below 20%
    nut_startup_threshold: int = 50  # Start back up when battery above 50%

    class Config:
        toml_file = DEFAULT_CONFIG_FILE
