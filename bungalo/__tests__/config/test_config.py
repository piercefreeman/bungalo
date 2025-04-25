from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from bungalo.config import BungaloConfig
from bungalo.config.config import (
    EndpointConfig,
    ManagedHardware,
    NutConfig,
    RemoteBackupConfig,
    RootConfig,
    SyncPair,
    iPhotoBackupConfig,
)
from bungalo.config.endpoints import B2Endpoint, NASEndpoint
from bungalo.config.paths import B2Path, FileLocation, NASPath

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def managed_hardware_dict() -> dict[str, str]:
    return {
        "name": "test-nas",
        "local_ip": "192.168.1.100",
        "username": "admin",
    }


@pytest.fixture
def config_dict() -> dict[str, Any]:
    return {
        "root": {
            "slack_webhook_url": "https://hooks.slack.com/test",
        },
        "nut": {
            "shutdown_threshold": 15,
            "startup_threshold": 45,
            "managed_hardware": [
                {
                    "name": "test-nas",
                    "local_ip": "192.168.1.100",
                    "username": "admin",
                },
                {
                    "name": "test-nas2",
                    "local_ip": "192.168.1.101",
                    "username": "admin2",
                },
            ],
        },
        "iphoto": {
            "username": "test_user",
            "password": "test_pass",
            "client_id": "test_client_id",
            "album_name": "Test Album",
            "photo_size": "large",
            "output_directory": "b2://backup/test-bucket/photos",
        },
        "remote": {
            "sync": [
                {
                    "src": "nas://nas1/drive1/documents",
                    "dst": "b2://backup/backup-bucket/documents",
                    "encrypt": True,
                },
                {
                    "src": "nas://nas2/drive2/media",
                    "dst": "b2://backup/backup-bucket/media",
                    "encrypt": False,
                },
            ]
        },
        "endpoints": {
            "b2": [
                {
                    "nickname": "backup",
                    "key_id": "test_key_id",
                    "application_key": "test_app_key",
                },
            ],
            "nas": [
                {
                    "nickname": "nas1",
                    "ip_address": "192.168.1.100",
                    "username": "admin",
                    "password": "pass123",
                    "domain": "WORKGROUP",
                },
                {
                    "nickname": "nas2",
                    "ip_address": "192.168.1.101",
                    "username": "admin2",
                    "password": "pass456",
                    "domain": "HOME",
                },
            ],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Individual Component Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_managed_hardware(managed_hardware_dict: dict[str, str]) -> None:
    """Test ManagedHardware configuration parsing."""
    hw = ManagedHardware.model_validate(managed_hardware_dict)
    assert hw.name == "test-nas"
    assert hw.local_ip == "192.168.1.100"
    assert hw.username == "admin"


def test_nut_config_defaults() -> None:
    """Test NutConfig with default values."""
    config = NutConfig()
    assert config.shutdown_threshold == 20
    assert config.startup_threshold == 50
    assert config.managed_hardware == []


def test_sync_pair_validation() -> None:
    """Test SyncPair validation with different path types."""
    pair = SyncPair(
        src="nas://nas1/drive1/test", dst="b2://backup/bucket/test", encrypt=True
    )
    assert isinstance(pair.src, NASPath)
    assert isinstance(pair.dst, B2Path)
    assert pair.encrypt is True


def test_b2_endpoint() -> None:
    """Test B2Endpoint configuration and path validation."""
    endpoint = B2Endpoint(
        nickname="backup", key_id="test_key", application_key="test_secret"
    )
    assert endpoint.nickname == "backup"
    assert isinstance(endpoint.application_key, SecretStr)

    # Test path validation
    valid_path = B2Path._from_uri("b2://backup/bucket/key")
    invalid_path = B2Path._from_uri("b2://other/bucket/key")

    assert endpoint.validate_path(B2Path.model_validate(valid_path))
    assert not endpoint.validate_path(B2Path.model_validate(invalid_path))


# ─────────────────────────────────────────────────────────────────────────────
# Full Config Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_fully_parameterized_config(config_dict: dict[str, Any]) -> None:
    """Test that we can create and read a fully parameterized config."""
    config = BungaloConfig.model_validate(config_dict)

    # Test root config
    assert isinstance(config.root, RootConfig)
    assert config.root.slack_webhook_url == "https://hooks.slack.com/test"

    # Test nut config
    assert isinstance(config.nut, NutConfig)
    assert config.nut.shutdown_threshold == 15
    assert config.nut.startup_threshold == 45
    assert len(config.nut.managed_hardware) == 2
    assert isinstance(config.nut.managed_hardware[0], ManagedHardware)
    assert config.nut.managed_hardware[0].name == "test-nas"

    # Test iPhoto config
    assert isinstance(config.iphoto, iPhotoBackupConfig)
    assert config.iphoto.username == "test_user"
    assert config.iphoto.password == "test_pass"
    assert config.iphoto.client_id == "test_client_id"
    assert config.iphoto.album_name == "Test Album"
    assert config.iphoto.photo_size == "large"
    assert isinstance(config.iphoto.output_directory, B2Path)

    # Test remote backup config
    assert isinstance(config.remote, RemoteBackupConfig)
    assert len(config.remote.sync) == 2
    assert isinstance(config.remote.sync[0], SyncPair)
    assert isinstance(config.remote.sync[0].src, NASPath)
    assert isinstance(config.remote.sync[0].dst, B2Path)
    assert config.remote.sync[0].encrypt is True
    assert config.remote.sync[1].encrypt is False

    # Test endpoints config
    assert isinstance(config.endpoints, EndpointConfig)
    assert len(config.endpoints.b2) == 1
    assert isinstance(config.endpoints.b2[0], B2Endpoint)
    assert config.endpoints.b2[0].nickname == "backup"
    assert isinstance(config.endpoints.b2[0].application_key, SecretStr)

    assert len(config.endpoints.nas) == 2
    assert isinstance(config.endpoints.nas[0], NASEndpoint)
    assert isinstance(config.endpoints.nas[1], NASEndpoint)
    assert config.endpoints.nas[0].nickname == "nas1"
    assert config.endpoints.nas[0].ip_address == "192.168.1.100"
    assert config.endpoints.nas[1].nickname == "nas2"
    assert config.endpoints.nas[1].domain == "HOME"

    # Test that get_all() works correctly
    all_endpoints = config.endpoints.get_all()
    assert len(all_endpoints) == 3


# ─────────────────────────────────────────────────────────────────────────────
# Validation Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_invalid_endpoint_nickname(config_dict: dict[str, Any]) -> None:
    """Test that we reject config with invalid endpoint nicknames."""
    config_dict["remote"]["sync"][0]["src"] = "nas://invalid-nas/drive1/test"
    with pytest.raises(ValidationError):
        BungaloConfig.model_validate(config_dict)


def test_invalid_path_format() -> None:
    """Test that invalid path formats are rejected."""
    with pytest.raises(ValueError):
        FileLocation("invalid://path")

    with pytest.raises(ValueError):
        FileLocation("b2://backup")  # Missing path components


def test_secret_values() -> None:
    """Test that sensitive values are properly handled as SecretStr."""
    config = BungaloConfig.model_validate(config_dict)

    # Check B2 credentials
    assert isinstance(config.endpoints.b2[0].application_key, SecretStr)

    # Check NAS credentials
    assert isinstance(config.endpoints.nas[0].password, SecretStr)
    assert isinstance(config.endpoints.nas[1].password, SecretStr)
