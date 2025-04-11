from functools import wraps
from typing import TypeVar, Callable, Any
import asyncio

T = TypeVar('T', bound=Callable[..., Any])

def async_to_sync(func: T) -> Callable[..., Any]:
    """
    Decorator that converts an async function to a sync function by running it in
    the event loop. If there's an existing event loop, it will use that, otherwise
    it will create a new one.
    
    Args:
        func: The async function to convert to sync
        
    Returns:
        A synchronous version of the async function
    """
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))
    
    return wrapper
