import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any

import psutil

PROCESS_SAMPLE_LIMIT = 5
PROCESS_SAMPLE_DELAY = 0.1


def _collect_top_processes() -> list[dict[str, Any]]:
    processes = []
    primed_processes: list[psutil.Process] = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            proc.cpu_percent(interval=None)
            primed_processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    time.sleep(PROCESS_SAMPLE_DELAY)

    for proc in primed_processes:
        try:
            cpu_percent = proc.cpu_percent(interval=None)
            memory_percent = proc.memory_percent()
            cmdline = proc.info.get("cmdline") or []
            command = " ".join(cmdline) if cmdline else proc.info.get("name") or ""
            processes.append(
                {
                    "pid": proc.pid,
                    "name": proc.info.get("name") or command or f"pid {proc.pid}",
                    "command": command,
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory_percent,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    processes.sort(key=lambda entry: entry["cpu_percent"], reverse=True)
    return processes[:PROCESS_SAMPLE_LIMIT]


def _collect_metrics_sync() -> dict[str, Any]:
    # Snapshot network counters before the CPU sampling delay so we can
    # compute throughput rates over the same interval.
    net_before = psutil.net_io_counters()
    net_time_before = time.monotonic()

    cpu_per_core = psutil.cpu_percent(interval=0.1, percpu=True)

    net_after = psutil.net_io_counters()
    net_elapsed = time.monotonic() - net_time_before

    load_average = None
    try:
        load_values = getattr(psutil, "getloadavg", os.getloadavg)()
        load_average = {
            "1m": load_values[0],
            "5m": load_values[1],
            "15m": load_values[2],
        }
    except (AttributeError, OSError):
        load_average = None

    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    processes = _collect_top_processes()

    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "cpu": {
            "cores": [
                {
                    "index": idx,
                    "percent": value,
                }
                for idx, value in enumerate(cpu_per_core)
            ],
            "average_percent": sum(cpu_per_core) / len(cpu_per_core)
            if cpu_per_core
            else 0.0,
            "load_average": load_average,
        },
        "memory": {
            "total": memory.total,
            "available": memory.available,
            "used": memory.used,
            "free": memory.free,
            "percent": memory.percent,
        },
        "swap": {
            "total": swap.total,
            "used": swap.used,
            "free": swap.free,
            "percent": swap.percent,
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        },
        "network": {
            "bytes_sent_per_sec": (net_after.bytes_sent - net_before.bytes_sent)
            / net_elapsed,
            "bytes_recv_per_sec": (net_after.bytes_recv - net_before.bytes_recv)
            / net_elapsed,
            "bytes_sent_total": net_after.bytes_sent,
            "bytes_recv_total": net_after.bytes_recv,
        },
        "processes": processes,
    }


async def collect_system_metrics() -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _collect_metrics_sync)


__all__ = ["collect_system_metrics"]
