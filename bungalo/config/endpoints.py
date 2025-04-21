from abc import ABC, abstractmethod

from pydantic_settings import BaseSettings

from bungalo.config.paths import FileLocation, NASPath, R2Path


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

    @abstractmethod
    def validate_path(self, path: FileLocation) -> bool: ...


class NASEndpoint(EndpointBase):
    ip_address: str
    username: str
    password: str
    domain: str = "WORKGROUP"

    def validate_path(self, path: FileLocation) -> bool:
        return isinstance(path, NASPath) and path.endpoint_nickname == self.nickname


class R2Endpoint(EndpointBase):
    api_key: str

    def validate_path(self, path: FileLocation) -> bool:
        return isinstance(path, R2Path) and path.endpoint_nickname == self.nickname
