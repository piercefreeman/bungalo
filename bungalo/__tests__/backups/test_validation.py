from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from bungalo.backups.validation import _validate_pair
from bungalo.config.config import SyncPair


@pytest.mark.asyncio
async def test_sftp_to_nas_validation(tmp_path: Path) -> None:
    pair = SyncPair.model_validate(
        {"src": "sftp:server:///srv/data", "dst": "nas:nas1://share/server"}
    )
    entry = {
        "Path": "nested/data.txt",
        "Name": "data.txt",
        "Size": 4,
        "ModTime": (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat(),
    }
    config_path = tmp_path / "rclone.conf"
    with (
        patch(
            "bungalo.backups.validation._list_remote_files",
            new_callable=AsyncMock,
            return_value=[entry],
        ) as list_files,
        patch(
            "bungalo.backups.validation._validate_remote_file",
            new_callable=AsyncMock,
            return_value=1,
        ) as read_file,
    ):
        assert await _validate_pair(pair, config_path=config_path) == (1, [])

    assert list_files.call_args.args == ("server:/srv/data", config_path)
    read_file.assert_awaited_once_with("nas1:share/server/nested/data.txt", config_path)
