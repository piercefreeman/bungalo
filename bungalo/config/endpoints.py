from abc import ABC, abstractmethod

from pydantic import SecretStr
from pydantic_settings import BaseSettings

from bungalo.config.paths import B2Path, FileLocation, NASPath


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
