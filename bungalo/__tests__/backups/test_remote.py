import os
import random
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from bungalo.backups.remote import (
    RCloneSync,
    SyncPair,
    validate_endpoints,
)
from bungalo.config.endpoints import B2Endpoint, EndpointBase, NASEndpoint


# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def nas_endpoint() -> NASEndpoint:
    return NASEndpoint(
        nickname="mynas1",
        ip_address="192.168.0.2",
        username="user",
        password=SecretStr("pass"),
        domain="",
    )


@pytest.fixture
def b2_endpoint() -> B2Endpoint:
    return B2Endpoint(
        nickname="myb2",
        key_id="akid",
        application_key=SecretStr("akey"),
    )


@pytest.fixture
def endpoints(
    nas_endpoint: NASEndpoint, b2_endpoint: B2Endpoint
) -> dict[str, EndpointBase]:
    return {
        nas_endpoint.nickname: nas_endpoint,
        b2_endpoint.nickname: b2_endpoint,
    }


@pytest.fixture
def sync_pairs() -> list[SyncPair]:
    return [
        SyncPair(
            src="nas:mynas1://drive/folder",  # type: ignore
            dst="b2:myb2://bucket/folder",  # type: ignore
        )
    ]


@pytest.fixture
def rclone(tmp_path, endpoints, sync_pairs) -> RCloneSync:
    return RCloneSync(
        config_path=tmp_path / "rclone.json",
        endpoints=endpoints,
        pairs=sync_pairs,
        slack_client=AsyncMock(),  # type: ignore
    )


# --------------------------------------------------------------------------- #
#  Tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_write_config_creates_expected_format(
    rclone: RCloneSync, tmp_path: Path, patched_run: tuple[MagicMock, MagicMock]
) -> None:
    encrypt_mock = AsyncMock()
    encrypt_mock.returncode = 0
    encrypt_mock.communicate.return_value = (b"ENCRYPTED_PASSWORD", b"")
    patched_run[1].return_value = encrypt_mock

    await rclone.write_config()
    config_path = rclone.config_path
    assert config_path.exists()

    config_text = config_path.read_text()
    lines = config_text.splitlines()

    # Validate NAS remote section
    assert "[mynas1]" in lines
    nas_config = _get_section_config(lines, "mynas1")
    assert nas_config["type"] == "smb"
    assert nas_config["host"] == "192.168.0.2"
    assert nas_config["user"] == "user"
    assert nas_config["pass"] == "ENCRYPTED_PASSWORD"

    # Validate B2 remote section
    assert "[myb2]" in lines
    b2_config = _get_section_config(lines, "myb2")
    assert b2_config["type"] == "b2"
    assert b2_config["account"] == "akid"
    assert b2_config["key"] == "akey"  # secret is not encrypted


def _get_section_config(lines: list[str], section_name: str) -> dict[str, str]:
    """Helper to extract config key-values for a specific section."""
    config = {}
    in_section = False
    for line in lines:
        if line.strip() == f"[{section_name}]":
            in_section = True
            continue
        if in_section:
            if not line.strip() or line.startswith("["):
                break
            key, value = [x.strip() for x in line.split("=", 1)]
            config[key] = value
    return config


def test_validate_endpoints_raises_on_duplicate_nicknames() -> None:
    duplicated_endpoints: list[EndpointBase] = [
        NASEndpoint(
            nickname="mynas1",
            ip_address="192.168.0.2",
            username="user",
            password=SecretStr("pass"),
            domain="",
        ),
        NASEndpoint(
            nickname="mynas1",
            ip_address="192.168.0.3",
            username="user2",
            password=SecretStr("pass2"),
            domain="",
        ),
    ]
    with pytest.raises(ValueError, match="Duplicate endpoint nickname"):
        validate_endpoints(duplicated_endpoints)


@pytest.mark.asyncio
async def test_sync_all_invokes_rclone_and_mount(
    tmp_path: Path, rclone: RCloneSync, patched_run: tuple[MagicMock, MagicMock]
) -> None:
    dummy_process = AsyncMock()
    dummy_process.returncode = 0
    dummy_process.communicate.return_value = (b"", b"")
    patched_run[1].return_value = dummy_process

    await rclone.sync_all()

    # One pair → one rclone call
    patched_run[1].assert_called_once()
    args, _ = patched_run[1].call_args
    cmd = args[:5]
    assert cmd[0:2] == ("rclone", "copy")


@pytest.mark.asyncio
async def test_sync_failure_bubbles_up(
    tmp_path: Path, rclone: RCloneSync, patched_run: tuple[MagicMock, MagicMock]
) -> None:
    failing_process = AsyncMock()
    failing_process.returncode = 1
    failing_process.communicate.return_value = (b"out", b"err")
    patched_run[1].return_value = failing_process

    with patch.object(rclone, "_alert") as alert_mock:
        await rclone.sync_all()
        alert_mock.assert_called()


# --------------------------------------------------------------------------- #
#  Integration Tests
# --------------------------------------------------------------------------- #


def _create_large_test_file(path: Path, size_mb: int = 5) -> None:
    """
    Create a test file of specified size in megabytes.

    :param path: Path where to create the file
    :param size_mb: Size of the file in megabytes

    """
    # Create a buffer of 1MB to write repeatedly
    chunk_size = 1024 * 1024  # 1MB
    chunk = bytes([random.randint(0, 255) for _ in range(chunk_size)])

    with open(path, "wb") as f:
        for _ in range(size_mb):
            f.write(chunk)


@pytest.mark.asyncio
async def test_local_file_sync(tmp_path: Path) -> None:
    """
    Test a real sync of a local file to a local file, to ensure our calls to rclone
    validate and we can parse the log output.

    """
    # Create source and destination directories
    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    source_dir.mkdir()
    dest_dir.mkdir()

    # Create a large test file (5MB)
    test_file = source_dir / "large_test.bin"
    _create_large_test_file(test_file)

    # Create some additional test files for structure testing
    test_subdir = source_dir / "subdir"
    test_subdir.mkdir()
    (test_subdir / "nested.txt").write_text("Nested content")
    (source_dir / "small_test.txt").write_text("Hello, World!")

    # Create sync pairs for local directories
    sync_pairs = [
        SyncPair(
            src=f"file:local://{source_dir}",  # type: ignore
            dst=f"file:local://{dest_dir}",  # type: ignore
        )
    ]

    # Mock the slack client
    mock_slack = AsyncMock()

    # Create RCloneSync instance
    rclone = RCloneSync(
        config_path=tmp_path / "rclone.json",
        endpoints={},  # No endpoints needed for local sync
        pairs=sync_pairs,
        slack_client=mock_slack,
        progress_interval=1,
    )

    # Run the sync
    await rclone.write_config()
    await rclone.sync_all()

    # Verify the files were actually copied
    assert (dest_dir / "large_test.bin").exists()
    assert os.path.getsize(dest_dir / "large_test.bin") == os.path.getsize(test_file)
    assert (dest_dir / "small_test.txt").exists()
    assert (dest_dir / "small_test.txt").read_text() == "Hello, World!"
    assert (dest_dir / "subdir" / "nested.txt").exists()
    assert (dest_dir / "subdir" / "nested.txt").read_text() == "Nested content"

    # Verify slack was updated with some log output
    mock_slack.create_status.assert_called()
    assert any(
        "5.000 MiB" in message[1] for message in mock_slack.update_status.call_args
    )
