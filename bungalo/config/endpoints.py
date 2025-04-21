from pydantic_settings import BaseSettings


class NASEndpoint(BaseSettings):
    """
    An endpoint is a logical partition of a remote storage location, at the level
    of the auth keys that are required to access it.

    """

    nickname: str
    """
    The nickname of the NAS endpoint. This is used in all
    """


class NASEndpoint(NASEndpoint):
    ip_address: str
    username: str
    password: str
    domain: str = "WORKGROUP"


class R2Endpoint(NASEndpoint):
    api_key: str
