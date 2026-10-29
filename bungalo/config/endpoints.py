from abc import ABC, abstractmethod

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings

from bungalo.config.paths import B2Path, FileLocation, NASPath, SFTPPath


class EndpointBase(BaseSettings, ABC):
    """
    An endpoint is a logical partition of a remote storage location, at the level
    of the auth keys that are required to access it.

    """

    nickname: str
    """
    The nickname of the NAS endpoint. This must be specified in all paths to provide the
    proper routing from a string to the right endpoint.

    """

    encrypt_key: SecretStr | None = None
    """
    The key to use for encrypting the data at rest.
    """

    @abstractmethod
    def validate_path(self, path: FileLocation) -> bool: ...


class NASEndpoint(EndpointBase):
    ip_address: str
    username: str
    password: SecretStr
    domain: str = "WORKGROUP"

    def validate_path(self, path: FileLocation) -> bool:
        return isinstance(path, NASPath) and path.endpoint_nickname == self.nickname


class B2Endpoint(EndpointBase):
    key_id: str
    application_key: SecretStr

    def validate_path(self, path: FileLocation) -> bool:
        return isinstance(path, B2Path) and path.endpoint_nickname == self.nickname


class SFTPEndpoint(EndpointBase):
    host: str
    username: str
    port: int = Field(default=22, ge=1, le=65535)
    key_file: str
    known_hosts_file: str

    @field_validator("host", "username", "key_file", "known_hosts_file")
    @classmethod
    def validate_connection_value(cls, value: str) -> str:
        if not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("SFTP connection values must be nonempty single lines")
        return value

    @field_validator("known_hosts_file")
    @classmethod
    def require_host_verification(cls, value: str) -> str:
        if value.strip().lower() == "none":
            raise ValueError("SFTP requires a known_hosts file for host verification")
        return value

    def validate_path(self, path: FileLocation) -> bool:
        return isinstance(path, SFTPPath) and path.endpoint_nickname == self.nickname
