from abc import ABC, abstractmethod
from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, BeforeValidator, model_serializer, model_validator


class PathBase(BaseModel, ABC):
    """
    By convention, all paths are represented as URIs that are prefixed with the
    nickname of the endpoint/account that is being used to access them.

    For instance:

    Instead of a true r2 path being:
    r2://my‑bucket/nested/path/data.csv

    We use the nickname of the endpoint/account to prefix the path:
    r2://r2-account-1/my‑bucket/nested/path/data.csv

    Common helper: accepts a URI string & dumps back to the same string.

    """

    endpoint_nickname: str
    full_path: str

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, v):
        """
        If `v` is a raw string, turn it into a **dict of field‑values**
        that Pydantic can use to build the model. *Never* return an
        already‑constructed instance here.
        """
        return cls._from_uri(v) if isinstance(v, str) else v

    @model_serializer(mode="plain")
    def _dump(self):
        return str(self)

    @staticmethod
    @abstractmethod
    def _from_uri(v: str) -> dict: ...

    @abstractmethod
    def __str__(self) -> str: ...

    @classmethod
    def parse_endpoint_uri(cls, v: str) -> tuple[str, str, str, str]:
        p = urlparse(v)
        endpoint_nickname = p.netloc
        path_portions = p.path.lstrip("/").split("/")
        if len(path_portions) < 2:
            raise ValueError("URI must include both endpoint_nickname *and* path")
        return (
            p.scheme,
            endpoint_nickname,
            path_portions[0],
            "/".join(path_portions[1:]),
        )


class B2Path(PathBase):
    bucket: str
    key: str

    @staticmethod
    def _from_uri(v: str) -> dict[str, str]:
        scheme, endpoint_nickname, bucket, key = B2Path.parse_endpoint_uri(v)
        if scheme != "b2" or not key.strip():
            raise ValueError("b2 URI must be b2://<endpoint_nickname>/<bucket>/<key>")
        return {
            "endpoint_nickname": endpoint_nickname,
            "bucket": bucket,
            "key": key,
            "full_path": f"/{bucket}/{key}",
        }

    def __str__(self) -> str:
        return f"b2://{self.endpoint_nickname}/{self.bucket}/{self.key}"


class NASPath(PathBase):
    drive_name: str
    path: str

    @staticmethod
    def _from_uri(v: str) -> dict[str, str]:
        scheme, endpoint_nickname, drive, path = NASPath.parse_endpoint_uri(v)
        if scheme != "nas" or not path.strip():
            raise ValueError("nas URI must be nas://<endpoint_nickname>/<drive>/<path>")
        return {
            "endpoint_nickname": endpoint_nickname,
            "drive_name": drive,
            "path": path,
            "full_path": f"/{drive}/{path}",
        }

    def __str__(self) -> str:
        return f"nas://{self.endpoint_nickname}/{self.drive_name}/{self.path}"


def _parse_file_location(v):
    if isinstance(v, (B2Path, NASPath)):
        return v
    if not isinstance(v, str):
        raise TypeError("FileLocation expects a URI string")
    scheme = urlparse(v).scheme
    if scheme == "b2":
        return B2Path._from_uri(v)
    if scheme == "nas":
        return NASPath._from_uri(v)
    raise ValueError(f"Unsupported URI scheme '{scheme}'")


FileLocation = Annotated[B2Path | NASPath, BeforeValidator(_parse_file_location)]
