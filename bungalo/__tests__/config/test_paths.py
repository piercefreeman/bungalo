import json
import typing as t

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from bungalo.config.paths import B2Path, FileLocation, NASPath

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

# `TypeAdapter` adds the validation layer that an Annotated alias lacks.
FileLocationAdapter = TypeAdapter(FileLocation)


def validate_file_location(v: str) -> B2Path | NASPath:
    return FileLocationAdapter.validate_python(v)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

B2_CASES: list[tuple[str, str, str, str]] = [
    ("b2:r2-account-1://bucket/one.txt", "r2-account-1", "bucket", "one.txt"),
    (
        "b2:r2-account-1://my‑bucket/nested/path/data.csv",
        "r2-account-1",
        "my‑bucket",
        "nested/path/data.csv",
    ),
]

NAS_CASES: list[tuple[str, str, str, str]] = [
    ("nas:nas-account-1://drive/doc.pdf", "nas-account-1", "drive", "doc.pdf"),
    (
        "nas:nas-account-1://shared‑drive/reports/2025/report.parquet",
        "nas-account-1",
        "shared‑drive",
        "reports/2025/report.parquet",
    ),
]

INVALID_URIS: list[str] = [
    "s3:account://bucket/key",  # unsupported scheme
    "b2:account://missing‑key/",  # empty key
    "b2:account://no‑bucket.txt",  # empty bucket
    "/absolute/filesystem/path.txt",  # no scheme at all
    "b2://account/bucket/key",  # old format
    "nas://account/drive/path",  # old format
]


# ─────────────────────────────────────────────────────────────────────────────
# B2Path  ↔︎  string
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("uri,nickname,bucket,key", B2_CASES, ids=lambda c: c[0])
def test_b2path_parse_roundtrip(uri: str, nickname: str, bucket: str, key: str) -> None:
    b2 = B2Path.model_validate(uri)
    assert (b2.endpoint_nickname, b2.bucket, b2.key) == (nickname, bucket, key)
    assert json.loads(b2.model_dump_json()) == uri
    assert str(b2) == uri


# ─────────────────────────────────────────────────────────────────────────────
# NASPath  ↔︎  string
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("uri,nickname,drive,path", NAS_CASES, ids=lambda c: c[0])
def test_naspath_parse_roundtrip(
    uri: str, nickname: str, drive: str, path: str
) -> None:
    nas = NASPath.model_validate(uri)
    assert (nas.endpoint_nickname, nas.drive_name, nas.path) == (nickname, drive, path)
    assert json.loads(nas.model_dump_json()) == uri
    assert str(nas) == uri


# ─────────────────────────────────────────────────────────────────────────────
# FileLocation alias coercion
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "uri,expected_type",
    [(c[0], B2Path) for c in B2_CASES] + [(c[0], NASPath) for c in NAS_CASES],
    ids=lambda x: x,
)
def test_filelocation_coercion(uri: str, expected_type: t.Type[BaseModel]) -> None:
    obj = validate_file_location(uri)
    assert isinstance(obj, expected_type)
    # adapter dumps to the original string
    assert FileLocationAdapter.dump_python(obj) == uri


# ─────────────────────────────────────────────────────────────────────────────
# Invalid URIs are rejected
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad_uri", INVALID_URIS, ids=lambda x: x)
def test_invalid_uri_rejected(bad_uri: str) -> None:
    with pytest.raises(ValidationError):
        validate_file_location(bad_uri)


# ─────────────────────────────────────────────────────────────────────────────
# Integration: FileLocation inside a normal model
# ─────────────────────────────────────────────────────────────────────────────
class JobSpec(BaseModel):
    source: FileLocation
    dest: FileLocation


def test_job_spec_basic_roundtrip() -> None:
    data = {
        "source": "b2:account://data‑bucket/input.csv",
        "dest": "nas:account://reports/output.csv",
    }
    spec = JobSpec.model_validate(data)
    assert spec.source.bucket == "data‑bucket"
    assert spec.dest.drive_name == "reports"
    assert spec.model_dump() == data
