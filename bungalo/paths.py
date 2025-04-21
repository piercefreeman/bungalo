from abc import ABC, abstractmethod
from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, BeforeValidator, model_serializer, model_validator


class PathBase(BaseModel, ABC):
    """Common helper: accepts a URI string & dumps back to the same string."""

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


class R2Path(PathBase):
    bucket: str
    key: str

    @staticmethod
    def _from_uri(v: str) -> dict[str, str]:
        p = urlparse(v)
        if p.scheme != "r2":
            raise ValueError("r2 URI must be  r2://<bucket>/<key>")
        bucket, key = p.netloc, p.path.lstrip("/")
        if not bucket or not key:
            raise ValueError("r2 URI must include both bucket *and* key")
        return {"bucket": bucket, "key": key}

    def __str__(self) -> str:
        return f"r2://{self.bucket}/{self.key}"


class NASPath(PathBase):
    drive_name: str
    path: str

    @staticmethod
    def _from_uri(v: str) -> dict[str, str]:
        p = urlparse(v)
        if p.scheme != "nas":
            raise ValueError("nas URI must be  nas://<drive>/<path>")
        drive, path = p.netloc, p.path.lstrip("/")
        if not drive or not path:
            raise ValueError("nas URI must include both drive *and* path")
        return {"drive_name": drive, "path": path}

    def __str__(self) -> str:
        return f"nas://{self.drive_name}/{self.path}"


def _parse_file_location(v):
    if isinstance(v, (R2Path, NASPath)):
        return v
    if not isinstance(v, str):
        raise TypeError("FileLocation expects a URI string")
    scheme = urlparse(v).scheme
    if scheme == "r2":
        return R2Path._from_uri(v)
    if scheme == "nas":
        return NASPath._from_uri(v)
    raise ValueError(f"Unsupported URI scheme '{scheme}'")


FileLocation = Annotated[R2Path | NASPath, BeforeValidator(_parse_file_location)]
