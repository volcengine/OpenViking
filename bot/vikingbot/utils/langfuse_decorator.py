"""
Generic Langfuse tracing decorator.

Compatible with both sync and async functions.
Gracefully degrades if langfuse is not installed or not enabled.
Uses the global LangfuseClient singleton by default.
"""

import asyncio
import time
from functools import wraps
from typing import Any, Callable, TypeVar, cast

from loguru import logger

T = TypeVar("T")


# Try to import langfuse client, but don't fail if not available
LangfuseClient = None
try:
    from vikingbot.integrations.langfuse import LangfuseClient
except ImportError:
    pass


def _get_langfuse_client() -> Any | None:
    """Get the global LangfuseClient singleton if available."""
    if LangfuseClient is None:
        return None
    try:
        client = LangfuseClient.get_instance()
        if (
            client
            and hasattr(client, "enabled")
            and client.enabled
            and hasattr(client, "_client")
            and client._client is not None
        ):
            return client
    except Exception:
        pass
    return None


def trace_tool(
    name: str | None = None,
    *,
    report_input: bool = True,
    report_output: bool = True,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Generic decorator to trace a function call to Langfuse.

    Works with both sync and async functions.
    Gracefully degrades if langfuse is not available or not enabled.
    Uses the global LangfuseClient singleton.

    Args:
        name: Optional name for the span (defaults to function name)
        report_input: Whether to report input arguments
        report_output: Whether to report output

    Returns:
        Decorated function

    Example:
        ```python
        @trace_tool(name="my_function", report_input=True, report_output=True)
        def my_sync_func(x: int, y: int) -> int:
            return x + y

        @trace_tool()
        async def my_async_func(x: int) -> int:
            return x * 2
        ```
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        span_name = name or func.__name__

        # Determine if function is async
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                client = _get_langfuse_client()
                if client is None:
                    return await func(*args, **kwargs)

                span = None
                start_time = time.time()
                try:
                    # Prepare input
                    span_input = None
                    if report_input:
                        if kwargs:
                            span_input = kwargs
                        elif args:
                            span_input = {"args": args}

                    # Create span
                    with client.tool_call(
                        name=span_name,
                        input=span_input,
                    ) as span:
                        result = await func(*args, **kwargs)

                        # Update span with output before exiting context
                        if span is not None:
                            update_kwargs: dict[str, Any] = {}
                            if report_output:
                                update_kwargs["output"] = result
                            update_kwargs["metadata"] = {
                                "success": True,
                                "duration_ms": (time.time() - start_time) * 1000,
                            }
                            if hasattr(span, "update"):
                                span.update(**update_kwargs)

                        return result
                except Exception as e:
                    # Update span with error
                    if span is not None:
                        try:
                            update_kwargs: dict[str, Any] = {
                                "output": f"Error: {str(e)}",
                                "metadata": {
                                    "success": False,
                                    "error": str(e),
                                    "duration_ms": (time.time() - start_time) * 1000,
                                },
                            }
                            if hasattr(span, "update"):
                                span.update(**update_kwargs)
                        except Exception:
                            pass
                    raise

            return cast(Callable[..., T], async_wrapper)
        else:
            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> T:
                client = _get_langfuse_client()
                if client is None:
                    return func(*args, **kwargs)

                span = None
                start_time = time.time()
                try:
                    # Prepare input
                    span_input = None
                    if report_input:
                        if kwargs:
                            span_input = kwargs
                        elif args:
                            span_input = {"args": args}

                    # Create span
                    with client.tool_call(
                        name=span_name,
                        input=span_input,
                    ) as span:
                        result = func(*args, **kwargs)

                        # Update span with output before exiting context
                        if span is not None:
                            update_kwargs: dict[str, Any] = {}
                            if report_output:
                                update_kwargs["output"] = result
                            update_kwargs["metadata"] = {
                                "success": True,
                                "duration_ms": (time.time() - start_time) * 1000,
                            }
                            if hasattr(span, "update"):
                                span.update(**update_kwargs)

                        return result
                except Exception as e:
                    # Update span with error
                    if span is not None:
                        try:
                            update_kwargs: dict[str, Any] = {
                                "output": f"Error: {str(e)}",
                                "metadata": {
                                    "success": False,
                                    "error": str(e),
                                    "duration_ms": (time.time() - start_time) * 1000,
                                },
                            }
                            if hasattr(span, "update"):
                                span.update(**update_kwargs)
                        except Exception:
                            pass
                    raise

            return cast(Callable[..., T], sync_wrapper)

    return decorator
