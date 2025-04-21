import asyncio
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Coroutine, ParamSpec, TypeVar

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

T = TypeVar("T")
P = ParamSpec("P")


def async_to_sync(async_fn: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, T]:
    @wraps(async_fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(async_fn(*args, **kwargs))
        return result

    return wrapper


@contextmanager
def progress_bar(
    description: str = "Processing",
    total: int | None = None,
):
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task(description, total=total)
        yield (progress, task)
