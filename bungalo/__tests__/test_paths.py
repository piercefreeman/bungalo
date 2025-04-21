import json
import typing as t

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from bungalo.paths import FileLocation, NASPath, R2Path

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

# `TypeAdapter` adds the validation layer that an Annotated alias lacks.
FileLocationAdapter = TypeAdapter(FileLocation)


def validate_file_location(v: str) -> R2Path | NASPath:
    return FileLocationAdapter.validate_python(v)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

R2_CASES: list[tuple[str, str, str]] = [
    ("r2://bucket/one.txt", "bucket", "one.txt"),
    ("r2://my‑bucket/nested/path/data.csv", "my‑bucket", "nested/path/data.csv"),
]

NAS_CASES: list[tuple[str, str, str]] = [
    ("nas://drive/doc.pdf", "drive", "doc.pdf"),
    (
        "nas://shared‑drive/reports/2025/report.parquet",
        "shared‑drive",
        "reports/2025/report.parquet",
    ),
]

INVALID_URIS: list[str] = [
    "s3://bucket/key",  # unsupported scheme
    "r2://missing‑key/",  # empty key
    "r2:///no‑bucket.txt",  # empty bucket
    "nas:///no‑drive/path",  # empty drive
    "/absolute/filesystem/path.txt",  # no scheme at all
]


# ─────────────────────────────────────────────────────────────────────────────
# R2Path  ↔︎  string
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("uri,bucket,key", R2_CASES, ids=lambda c: c[0])
def test_r2path_parse_roundtrip(uri: str, bucket: str, key: str) -> None:
    r2 = R2Path.model_validate(uri)
    assert (r2.bucket, r2.key) == (bucket, key)
    assert json.loads(r2.model_dump_json()) == uri
    assert str(r2) == uri


# ─────────────────────────────────────────────────────────────────────────────
# NASPath  ↔︎  string
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("uri,drive,path", NAS_CASES, ids=lambda c: c[0])
def test_naspath_parse_roundtrip(uri: str, drive: str, path: str) -> None:
    nas = NASPath.model_validate(uri)
    assert (nas.drive_name, nas.path) == (drive, path)
    assert json.loads(nas.model_dump_json()) == uri
    assert str(nas) == uri


# ─────────────────────────────────────────────────────────────────────────────
# FileLocation alias coercion
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "uri,expected_type",
    [(c[0], R2Path) for c in R2_CASES] + [(c[0], NASPath) for c in NAS_CASES],
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
        "source": "r2://data‑bucket/input.csv",
        "dest": "nas://reports/output.csv",
    }
    spec = JobSpec.model_validate(data)
    assert spec.source.bucket == "data‑bucket"
    assert spec.dest.drive_name == "reports"
    assert spec.model_dump() == data
