from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from bungalo.config import BungaloConfig
from bungalo.plugins import jellyfin


class DummyProcess:
    def __init__(self, returncode: int = 0):
        self._returncode = returncode

    async def wait(self) -> int:
        return self._returncode


@pytest.mark.asyncio
async def test_jellyfin_plugin_mounts_and_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    commands: list[tuple[Any, ...]] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        commands.append(args)
        return DummyProcess()

    @contextmanager
    def fake_mount_smb(**kwargs):
        mount_point: Path = kwargs["mount_point"]
        share: str = kwargs["share"]
        mount_point.mkdir(parents=True, exist_ok=True)
        if share == "share_movies":
            (mount_point / "Movies").mkdir(parents=True, exist_ok=True)
        elif share == "share_tv":
            (mount_point / "Shows").mkdir(parents=True, exist_ok=True)
        elif share == "share_transcode":
            (mount_point / "Transcode").mkdir(parents=True, exist_ok=True)
        yield mount_point

    slack_messages: list[str] = []

    class DummySlackClient:
        def __init__(self, *args, **kwargs):
            pass

        async def create_status(self, text: str, parent_ts=None):
            slack_messages.append(text)
            return None

    monkeypatch.setenv("BUNGALO_JELLYFIN_ROOT", str(tmp_path / "jellyfin"))
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(jellyfin.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(jellyfin, "mount_smb", fake_mount_smb)
    monkeypatch.setattr(jellyfin, "SlackClient", DummySlackClient)

    config_dict = {
        "root": {"self_ip": "tailscale.jellyfin"},
        "slack": {
            "app_token": "app",
            "bot_token": "bot",
            "channel": "#alerts",
        },
        "backups": {"sync": []},
        "endpoints": {
            "nas": [
                {
                    "nickname": "jellyfin-nas",
                    "ip_address": "192.168.1.50",
                    "username": "jellyfin",
                    "password": "secret",
                    "domain": "WORKGROUP",
                }
            ]
        },
        "media_server": {
            "plugin": "jellyfin",
            "transcode": "nas:jellyfin-nas://share_transcode/Transcode",
            "mounts": [
                {
                    "name": "movies",
                    "path": "nas:jellyfin-nas://share_movies/Movies",
                    "container_path": "/media/movies",
                },
                {
                    "name": "tv",
                    "path": "nas:jellyfin-nas://share_tv/Shows",
                },
            ],
        },
    }

    config = BungaloConfig.model_validate(config_dict)

    await jellyfin.main(config)

    # Two docker commands should be executed: rm -f and run
    assert len(commands) == 2
    run_cmd = commands[-1]
    assert run_cmd[0:2] == ("docker", "run")
    assert jellyfin.JELLYFIN_IMAGE in run_cmd
    assert "-e" in run_cmd and "TZ=UTC" in run_cmd

    # Validate expected volume mounts are present
    volume_targets = [
        run_cmd[idx + 1] for idx, val in enumerate(run_cmd) if val == "-v"
    ]

    config_dir = Path(tmp_path / "jellyfin" / "config")
    transcode_dir = Path(tmp_path / "jellyfin" / "mounts" / "transcode" / "Transcode")
    movies_dir = Path(tmp_path / "jellyfin" / "mounts" / "movies" / "Movies")
    tv_dir = Path(tmp_path / "jellyfin" / "mounts" / "tv" / "Shows")

    assert f"{config_dir}:/config" in volume_targets
    assert f"{transcode_dir}:/cache" in volume_targets
    assert f"{movies_dir}:/media/movies:ro" in volume_targets
    assert f"{tv_dir}:/data/tv:ro" in volume_targets

    assert any("http://tailscale.jellyfin:8096" in message for message in slack_messages)
