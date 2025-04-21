from typing import Any

import pytest
from pydantic import ValidationError

from bungalo.config import BungaloConfig
from bungalo.config.config import (
    EndpointConfig,
    NutConfig,
    RemoteBackupConfig,
    RemoteSync,
    RootConfig,
    iPhotoBackupConfig,
)
from bungalo.config.endpoints import NASEndpoint, R2Endpoint
from bungalo.config.paths import NASPath, R2Path

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def config_dict() -> dict[str, Any]:
    return {
        "root": {
            "slack_webhook_url": "https://hooks.slack.com/test",
        },
        "nut": {
            "shutdown_threshold": 15,
            "startup_threshold": 45,
        },
        "iphoto": {
            "username": "test_user",
            "password": "test_pass",
            "client_id": "test_client_id",
            "album_name": "Test Album",
            "photo_size": "large",
            "output_directory": "r2://backup/test-bucket/photos",
        },
        "remote": {
            "sync": [
                {
                    "src": "nas://nas1/drive1/documents",
                    "dst": "r2://backup/backup-bucket/documents",
                },
                {
                    "src": "nas://nas2/drive2/media",
                    "dst": "r2://backup/backup-bucket/media",
                },
            ]
        },
        "endpoints": {
            "r2": [
                {
                    "nickname": "backup",
                    "api_key": "test_api_key_1",
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
# Successful parsing
# ─────────────────────────────────────────────────────────────────────────────


def test_fully_parameterized_config(config_dict: dict[str, Any]) -> None:
    """Test that we can create and read a fully parameterized config."""
    config = BungaloConfig.model_validate(config_dict)

    # Verify all sections are properly loaded
    assert isinstance(config.root, RootConfig)
    assert config.root.slack_webhook_url == "https://hooks.slack.com/test"

    assert isinstance(config.nut, NutConfig)
    assert config.nut.shutdown_threshold == 15
    assert config.nut.startup_threshold == 45

    assert isinstance(config.iphoto, iPhotoBackupConfig)
    assert config.iphoto.username == "test_user"
    assert config.iphoto.password == "test_pass"
    assert config.iphoto.client_id == "test_client_id"
    assert config.iphoto.album_name == "Test Album"
    assert config.iphoto.photo_size == "large"
    assert isinstance(config.iphoto.output_directory, R2Path)
    assert config.iphoto.output_directory.bucket == "test-bucket"
    assert config.iphoto.output_directory.key == "photos"

    assert isinstance(config.remote, RemoteBackupConfig)
    assert len(config.remote.sync) == 2
    assert isinstance(config.remote.sync[0], RemoteSync)
    assert isinstance(config.remote.sync[0].src, NASPath)
    assert isinstance(config.remote.sync[0].dst, R2Path)

    assert isinstance(config.endpoints, EndpointConfig)
    assert len(config.endpoints.r2) == 1
    assert isinstance(config.endpoints.r2[0], R2Endpoint)
    assert config.endpoints.r2[0].nickname == "backup"
    assert config.endpoints.r2[0].api_key == "test_api_key_1"

    assert len(config.endpoints.nas) == 2
    assert isinstance(config.endpoints.nas[0], NASEndpoint)
    assert isinstance(config.endpoints.nas[1], NASEndpoint)
    assert config.endpoints.nas[0].nickname == "nas1"
    assert config.endpoints.nas[0].ip_address == "192.168.1.100"
    assert config.endpoints.nas[1].nickname == "nas2"
    assert config.endpoints.nas[1].domain == "HOME"


# ─────────────────────────────────────────────────────────────────────────────
# Invalid config
# ─────────────────────────────────────────────────────────────────────────────


def test_invalid_endpoint_nickname(config_dict: dict[str, Any]) -> None:
    """Test that we reject config with invalid endpoint nicknames."""
    config_dict["remote"]["sync"][0]["src"] = "r2://invalid-nickname/bucket/key"
    with pytest.raises(ValidationError):
        BungaloConfig.model_validate(config_dict)
