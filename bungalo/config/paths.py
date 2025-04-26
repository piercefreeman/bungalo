from abc import ABC, abstractmethod
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, model_serializer, model_validator


class PathBase(BaseModel, ABC):
    """
    By convention, all paths are represented as URIs where the endpoint nickname
    is part of the scheme, followed by the actual path.

    For instance:
    b2:r2-account-1://my‑bucket/nested/path/data.csv
    nas:nas-account-1://shared‑drive/reports/data.csv

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
        """Parse a URI in the format type:endpoint://path into components"""
        try:
            base_scheme, endpoint = v.split(":", 1)
            endpoint, path = endpoint.split("://", 1)
        except ValueError:
            raise ValueError("URI must be in format type:endpoint://path")

        path_portions = path.split("/", 1)
        first_path_item = path_portions[0] if len(path_portions) > 0 else ""
        second_path_item = path_portions[1] if len(path_portions) > 1 else ""

        return (base_scheme, endpoint, first_path_item, second_path_item)


class B2Path(PathBase):
    bucket: str
    key: str

    @staticmethod
    def _from_uri(v: str) -> dict[str, str]:
        scheme, endpoint_nickname, bucket, key = B2Path.parse_endpoint_uri(v)
        if scheme != "b2" or not bucket.strip():
            raise ValueError("b2 URI must be b2:endpoint://bucket/key")
        return {
            "endpoint_nickname": endpoint_nickname,
            "bucket": bucket,
            "key": key,
            "full_path": f"{bucket}/{key}",
        }

    def __str__(self) -> str:
        return f"b2:{self.endpoint_nickname}://{self.bucket}/{self.key}"


class NASPath(PathBase):
    drive_name: str
    path: str

    @staticmethod
    def _from_uri(v: str) -> dict[str, str]:
        scheme, endpoint_nickname, drive, path = NASPath.parse_endpoint_uri(v)
        if scheme != "nas" or not drive.strip():
            raise ValueError("nas URI must be nas:endpoint://drive/path")
        return {
            "endpoint_nickname": endpoint_nickname,
            "drive_name": drive,
            "path": path,
            "full_path": f"{drive}/{path}",
        }

    def __str__(self) -> str:
        return f"nas:{self.endpoint_nickname}://{self.drive_name}/{self.path}"


class FilePath(PathBase):
    path: str

    @staticmethod
    def _from_uri(v: str) -> dict[str, str]:
        scheme, endpoint_nickname, first_path, second_path = (
            FilePath.parse_endpoint_uri(v)
        )
        if scheme != "file":
            raise ValueError("file URI must be file:local://path")
        if endpoint_nickname != "local":
            raise ValueError("file URI must use 'local' as endpoint")

        full_path = first_path
        if second_path:
            full_path = f"{first_path}/{second_path}"

        return {
            "endpoint_nickname": endpoint_nickname,
            "path": full_path,
            "full_path": full_path,
        }

    def __str__(self) -> str:
        return f"file:{self.endpoint_nickname}://{self.path}"


def _parse_file_location(v):
    if isinstance(v, (B2Path, NASPath, FilePath)):
        return v
    if not isinstance(v, str):
        raise TypeError("FileLocation expects a URI string")
    try:
        scheme = v.split(":", 1)[0]
    except IndexError:
        raise ValueError("Invalid URI format")

    if scheme == "b2":
        return B2Path._from_uri(v)
    if scheme == "nas":
        return NASPath._from_uri(v)
    if scheme == "file":
        return FilePath._from_uri(v)
    raise ValueError(f"Unsupported URI scheme '{scheme}'")


FileLocation = Annotated[
    B2Path | NASPath | FilePath, BeforeValidator(_parse_file_location)
]
