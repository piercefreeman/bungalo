import asyncio


async def _check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def check_ports(
    ports: dict[str, int],
    host: str = "127.0.0.1",
) -> dict[str, bool]:
    """Check reachability for a dict of name→port mappings concurrently."""
    results = await asyncio.gather(
        *(_check_port(host, port) for port in ports.values())
    )
    return dict(zip(ports.keys(), results))
