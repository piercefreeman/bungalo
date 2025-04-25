from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def patched_run() -> tuple[MagicMock, MagicMock]:
    """
    Intercept *all* subprocess.run calls and pretend they succeed.
    """
    with patch("subprocess.run") as sync_run:
        with patch("asyncio.create_subprocess_exec") as async_run:
            sync_run.return_value = SimpleNamespace(
                returncode=0, stdout=b"", stderr=b""
            )
            async_run.return_value = SimpleNamespace(
                returncode=0, stdout=b"", stderr=b""
            )
            yield sync_run, async_run
