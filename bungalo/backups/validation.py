import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bungalo.app_manager import AppManager
from bungalo.backups.remote import RCloneSync, validate_endpoints
from bungalo.config.config import BungaloConfig, SyncPair
from bungalo.config.paths import B2Path, FileLocation, NASPath
from bungalo.constants import DEFAULT_RCLONE_CONFIG_FILE
from bungalo.logger import CONSOLE, LOGGER
from bungalo.slack import SlackClient

SERVICE_NAME = "remote_validation"
SAMPLE_COUNT = 25
VALIDATION_INTERVAL = timedelta(hours=5)


def _resolve_rclone_path(location: FileLocation) -> str | None:
    match location:
        case NASPath():
            return f"{location.endpoint_nickname}:{location.full_path}"
        case B2Path():
            return f"{location.endpoint_nickname}:{location.full_path}"
        case _:
            return None


def _build_object_path(base: str, entry: dict[str, Any]) -> str:
    base_clean = base.rstrip("/")
    relative = (entry.get("Path") or "").lstrip("/")
    name = entry.get("Name")

    if relative and name and relative == name and base_clean.endswith(name):
        return base_clean
    if not relative:
        return base_clean
    return f"{base_clean}/{relative}"


async def _list_remote_files(
    remote_path: str, config_path: Path
) -> list[dict[str, Any]]:
    process = await asyncio.create_subprocess_exec(
        "rclone",
        "lsjson",
        "--recursive",
        "--files-only",
        "--fast-list",
        "--config",
        str(config_path),
        remote_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        error_output = stderr.decode() or stdout.decode()
        raise RuntimeError(
            f"rclone lsjson failed for {remote_path}: {error_output.strip()}"
        )

    try:
        entries = json.loads(stdout.decode() or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse rclone lsjson output: {exc}") from exc

    if not isinstance(entries, list):
        raise RuntimeError("Unexpected lsjson output format")
    return entries


async def _validate_remote_file(object_path: str, config_path: Path) -> int:
    process = await asyncio.create_subprocess_exec(
        "rclone",
        "cat",
        "--count",
        "1",
        "--config",
        str(config_path),
        object_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        error_output = stderr.decode() or stdout.decode()
        raise RuntimeError(
            f"rclone cat failed for {object_path}: {error_output.strip()}"
        )
    return len(stdout)


async def _validate_pair(
    pair: SyncPair,
    *,
    config_path: Path,
) -> tuple[int, list[str]]:
    label = str(pair.dst)
    remote_path = _resolve_rclone_path(pair.dst)
    if remote_path is None:
        LOGGER.debug("Skipping validation for unsupported path type: %s", label)
        return 0, []

    files = await _list_remote_files(remote_path, config_path)
    if not files:
        return 0, [f"{label}: remote path contains no files"]

    sample_size = min(SAMPLE_COUNT, len(files))
    selected = random.sample(files, sample_size)

    errors: list[str] = []
    for entry in selected:
        object_path = _build_object_path(remote_path, entry)
        try:
            bytes_read = await _validate_remote_file(object_path, config_path)
        except Exception as exc:
            errors.append(
                f"{label}: failed to decrypt {entry.get('Path') or entry.get('Name')}: {exc}"
            )
            continue

        if bytes_read <= 0:
            errors.append(
                f"{label}: decrypted zero bytes from {entry.get('Path') or entry.get('Name')}"
            )

    return sample_size, errors


async def main(config: BungaloConfig) -> None:
    endpoints_by_nickname = validate_endpoints(config.endpoints.get_all())
    sync_pairs = [SyncPair.model_validate(raw_pair) for raw_pair in config.backups.sync]

    slack_client = SlackClient(
        app_token=config.slack.app_token,
        bot_token=config.slack.bot_token,
        channel_id=config.slack.channel,
    )

    rclone_sync = RCloneSync(
        config_path=Path(DEFAULT_RCLONE_CONFIG_FILE).expanduser(),
        endpoints=endpoints_by_nickname,
        pairs=sync_pairs,
        slack_client=slack_client,
    )
    await rclone_sync.write_config()

    manager = AppManager.get()
    interval_seconds = VALIDATION_INTERVAL.total_seconds()

    await manager.update_service(
        SERVICE_NAME,
        state="idle",
        detail="Waiting to start remote validation",
        next_run_at=datetime.now(timezone.utc),
    )

    while True:
        if not sync_pairs:
            await manager.update_service(
                SERVICE_NAME,
                state="idle",
                detail="No remote backup pairs configured",
                next_run_at=datetime.now(timezone.utc) + VALIDATION_INTERVAL,
                last_run_at=datetime.now(timezone.utc),
            )
            await asyncio.sleep(interval_seconds)
            continue

        start_time = datetime.now(timezone.utc)
        await manager.update_service(
            SERVICE_NAME,
            state="running",
            detail="Starting remote backup validation",
            last_run_at=start_time,
        )

        total_checked = 0
        all_errors: list[str] = []

        for pair in sync_pairs:
            await manager.update_service(
                SERVICE_NAME,
                state="running",
                detail=f"Validating {pair.dst}",
            )
            try:
                checked, errors = await _validate_pair(
                    pair,
                    config_path=rclone_sync.config_path,
                )
                total_checked += checked
                all_errors.extend(errors)
            except Exception as exc:
                error_message = f"{pair.dst}: validation failed: {exc}"
                LOGGER.error(error_message)
                all_errors.append(error_message)

        next_run_at = datetime.now(timezone.utc) + VALIDATION_INTERVAL

        if all_errors:
            detail = f"Validation failed: {len(all_errors)} issues detected"
            await manager.update_service(
                SERVICE_NAME,
                state="error",
                detail=detail,
                next_run_at=next_run_at,
            )

            summary_lines = [
                "🚨 Remote backup validation failed.",
                f"Checked {total_checked} files across {len(sync_pairs)} backups.",
                "Issues:",
            ]
            for line in all_errors[:10]:
                summary_lines.append(f"• {line}")
            if len(all_errors) > 10:
                summary_lines.append(f"...and {len(all_errors) - 10} more.")

            message = "\n".join(summary_lines)
            await slack_client.create_status(message)
            CONSOLE.print(message)
        else:
            detail = f"Validated {total_checked} files across {len(sync_pairs)} backups"
            await manager.update_service(
                SERVICE_NAME,
                state="idle",
                detail=detail,
                next_run_at=next_run_at,
            )
            LOGGER.info(detail)

        await asyncio.sleep(interval_seconds)


__all__ = ["main"]
