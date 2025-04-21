from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings

from bungalo.config.endpoints import NASEndpoint, R2Endpoint
from bungalo.config.paths import FileLocation, NASPath, R2Path


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
    def _validate_file_locations(self):
        """
        Walk the *entire* model tree, find every `R2Path`/`NASPath`, and
        ensure at least one matching endpoint accepts it.
        """

        def walk(obj):
            """Yield every FileLocation object (R2Path | NASPath) in `obj`."""
            if isinstance(obj, (R2Path, NASPath)):
                yield obj
            elif isinstance(obj, BaseModel):
                for field in obj.model_fields:
                    yield from walk(getattr(obj, field))
            elif isinstance(obj, (list, tuple, set)):
                for item in obj:
                    yield from walk(item)
            elif isinstance(obj, dict):
                for val in obj.values():
                    yield from walk(val)

        for loc in walk(self):
            endpoints: list[R2Endpoint | NASEndpoint] = (
                self.endpoints.r2 if isinstance(loc, R2Path) else self.endpoints.nas
            )

            if not any(ep.validate_path(loc) for ep in endpoints):
                raise ValueError(
                    f"File location {loc!s} does not match any configured "
                    f"{'R2' if isinstance(loc, R2Path) else 'NAS'} endpoint nickname"
                )

        return self
