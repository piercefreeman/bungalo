import asyncio
import os
import shlex
from pathlib import Path

import uvicorn

from bungalo.logger import LOGGER

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"


def _get_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        LOGGER.warning("Environment variable %s=%s is not a valid integer", name, raw)
        return default


async def run_fastapi(
    host: str | None = None,
    port: int | None = None,
) -> None:
    bind_host = host or os.environ.get("BUNGALO_API_HOST", "127.0.0.1")
    bind_port = port or _get_int_env("BUNGALO_API_PORT", 8000)
    log_level = os.environ.get("BUNGALO_API_LOG_LEVEL", "info")
    LOGGER.info("Starting FastAPI server on http://%s:%s", bind_host, bind_port)
    config = uvicorn.Config(
        "bungalo.web_server:app",
        host=bind_host,
        port=bind_port,
        loop="asyncio",
        log_level=log_level,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_nextjs(
    *,
    port: int | None = None,
    api_base: str | None = None,
    frontend_path: Path | None = None,
) -> None:
    resolved_dir = frontend_path or FRONTEND_DIR
    if not resolved_dir.exists():
        LOGGER.warning("Next.js frontend directory not found at %s; skipping launch", resolved_dir)
        return

    next_port = port or _get_int_env("BUNGALO_NEXT_PORT", 3000)
    api_base_url = (
        api_base
        or os.environ.get("NEXT_PUBLIC_API_BASE")
        or f"http://{os.environ.get('BUNGALO_API_HOST', '127.0.0.1')}:{_get_int_env('BUNGALO_API_PORT', 8000)}"
    )

    env = os.environ.copy()
    env.setdefault("PORT", str(next_port))
    env.setdefault("NEXT_PUBLIC_API_BASE", api_base_url)
    env.setdefault("BUNGALO_DASHBOARD_URL", f"http://127.0.0.1:{next_port}")

    command = env.get("BUNGALO_NEXT_COMMAND")
    if command:
        argv = shlex.split(command)
    else:
        argv = ["npm", "run", "dev"]

    LOGGER.info(
        "Starting Next.js dashboard at http://127.0.0.1:%s (API: %s)",
        next_port,
        api_base_url,
    )
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(resolved_dir),
            env=env,
        )
    except FileNotFoundError as exc:
        LOGGER.error(
            "Unable to start Next.js dashboard (missing executable for %s): %s",
            argv[0],
            exc,
        )
        raise

    returncode = await process.wait()
    if returncode != 0:
        raise RuntimeError(f"Next.js process exited with status {returncode}")


async def start_dashboard_services() -> None:
    """
    Launch FastAPI and Next.js processes concurrently.
    """
    fastapi_task = asyncio.create_task(run_fastapi())
    next_task = asyncio.create_task(run_nextjs())

    done, pending = await asyncio.wait(
        {fastapi_task, next_task},
        return_when=asyncio.FIRST_EXCEPTION,
    )

    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    # Surface the first exception (if any)
    for task in done:
        exc = task.exception()
        if exc:
            raise exc


__all__ = ["run_fastapi", "run_nextjs", "start_dashboard_services"]
