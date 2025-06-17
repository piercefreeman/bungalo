from pathlib import Path
from unittest.mock import ANY, Mock, patch

import pytest

from bungalo.backups.nas import mount_smb


def _assert_cmd_sequence(
    run_mock: Mock, expected_first: list[str], expected_last: list[str]
) -> None:
    calls = [c.args[0] for c in run_mock.call_args_list]
    assert calls[0] == expected_first  # mount -t cifs ...
    assert calls[-1] == expected_last  # ['umount', <mount_dir>]


def test_guest_mount_and_unmount(
    tmp_path: Path, patched_run: tuple[Mock, Mock]
) -> None:
    """Empty username → 'guest' in options and umount issued on exit."""
    with patch.object(Path, "is_mount", return_value=True):
        with mount_smb("nas.local", "public", mount_point=tmp_path):
            pass
    _assert_cmd_sequence(
        patched_run[0],
        ["mount", "-t", "cifs", "//nas.local/public", ANY, "-o", "vers=3.0,guest"],
        ["umount", str(tmp_path)],
    )
    # Check 'guest' present in options string (last token of mount cmd)
    assert "guest" in patched_run[0].call_args_list[0].args[0][-1]


def test_credential_mount_with_domain_and_extra_options(
    tmp_path: Path, patched_run: tuple[Mock, Mock]
) -> None:
    extra = {"rw": "", "uid": "1000"}
    with patch.object(Path, "is_mount", return_value=True):
        with mount_smb(
            "10.0.0.5",
            "documents",
            username="alice",
            password="secret",
            domain="CORP",
            mount_options=extra,
            mount_point=tmp_path,
        ):
            pass

    cmd = patched_run[0].call_args_list[0].args[0]
    opts = cmd[-1]  # the -o string
    assert "username=alice" in opts
    assert "password=secret" in opts
    assert "domain=CORP" in opts
    for k, v in extra.items():
        needle = f"{k}={v}" if v else k
        assert needle in opts


def test_unmount_skipped_when_not_mounted(
    tmp_path: Path, patched_run: tuple[Mock, Mock]
) -> None:
    """If Path.is_mount() is False we should *not* call umount."""
    with patch.object(Path, "is_mount", return_value=False):
        with mount_smb("srv", "s", mount_point=tmp_path):
            pass
    calls = [c.args[0][0] for c in patched_run[0].call_args_list]
    assert "umount" not in calls


def test_exception_inside_block_still_unmounts(
    tmp_path: Path, patched_run: tuple[Mock, Mock]
) -> None:
    with patch.object(Path, "is_mount", return_value=True):
        with pytest.raises(RuntimeError):
            with mount_smb("srv", "s", mount_point=tmp_path):
                raise RuntimeError("boom")
    # umount should still be present
    assert patched_run[0].call_args_list[-1].args[0][0] == "umount"
