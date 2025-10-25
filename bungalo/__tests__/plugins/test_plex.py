from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from bungalo.config import BungaloConfig
from bungalo.plugins import plex


class DummyProcess:
    def __init__(self, returncode: int = 0):
        self._returncode = returncode

    async def wait(self) -> int:
        return self._returncode


@pytest.mark.asyncio
async def test_plex_plugin_mounts_and_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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

    monkeypatch.setenv("BUNGALO_PLEX_ROOT", str(tmp_path / "plex"))
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(plex.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(plex, "mount_smb", fake_mount_smb)
    monkeypatch.setattr(plex, "SlackClient", DummySlackClient)

    config_dict = {
        "root": {"self_ip": "tailscale.plex"},
        "slack": {
            "app_token": "app",
            "bot_token": "bot",
            "channel": "#alerts",
        },
        "backups": {"sync": []},
        "endpoints": {
            "nas": [
                {
                    "nickname": "plex-nas",
                    "ip_address": "192.168.1.50",
                    "username": "plex",
                    "password": "secret",
                    "domain": "WORKGROUP",
                }
            ]
        },
        "media_server": {
            "plugin": "plex",
            "transcode": "nas:plex-nas://share_transcode/Transcode",
            "mounts": [
                {
                    "name": "movies",
                    "path": "nas:plex-nas://share_movies/Movies",
                    "container_path": "/data/movies",
                },
                {
                    "name": "tv",
                    "path": "nas:plex-nas://share_tv/Shows",
                },
            ],
        },
    }

    config = BungaloConfig.model_validate(config_dict)

    await plex.main(config)

    # Two docker commands should be executed: rm -f and run
    assert len(commands) == 2
    run_cmd = commands[-1]
    assert run_cmd[0:2] == ("docker", "run")
    assert plex.PLEX_IMAGE in run_cmd
    assert "-e" in run_cmd and "TZ=UTC" in run_cmd

    # Validate expected volume mounts are present
    # run_cmd structure: "-v", "<path>", "-v", "<path>", ...
    # Gather paired values
    volume_targets = [
        run_cmd[idx + 1] for idx, val in enumerate(run_cmd) if val == "-v"
    ]

    config_dir = Path(tmp_path / "plex" / "config")
    transcode_dir = Path(tmp_path / "plex" / "mounts" / "transcode" / "Transcode")
    movies_dir = Path(tmp_path / "plex" / "mounts" / "movies" / "Movies")
    tv_dir = Path(tmp_path / "plex" / "mounts" / "tv" / "Shows")

    assert f"{config_dir}:/config" in volume_targets
    assert f"{transcode_dir}:/transcode" in volume_targets
    assert f"{movies_dir}:/data/movies:ro" in volume_targets
    assert f"{tv_dir}:/data/tv:ro" in volume_targets

    assert any("http://tailscale.plex:32400/web" in message for message in slack_messages)
