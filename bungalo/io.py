import asyncio
from functools import wraps
from typing import Any, Callable, Coroutine, ParamSpec, TypeVar

T = TypeVar("T")
P = ParamSpec("P")


def async_to_sync(async_fn: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, T]:
    @wraps(async_fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(async_fn(*args, **kwargs))
        return result

    return wrapper
