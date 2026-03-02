"""
Abstract tracing utilities for observability.

This module provides a tracing abstraction that is not tied to any specific
backend (Langfuse, OpenTelemetry, etc.), allowing for easy switching of
implementations.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Generator, TypeVar

from loguru import logger

# Context variable to store current session ID
_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)

T = TypeVar("T")


def get_current_session_id() -> str | None:
    """Get the current session ID from context."""
    return _session_id.get()



@contextmanager
def set_session_id(session_id: str | None) -> Generator[None, None, None]:
    """
    Set the session ID for the current context.

    Args:
        session_id: The session ID to set, or None to clear.

    Example:
        with set_session_id("user-123"):
            # All nested operations will see this session_id
            result = await process_message(msg)
    """
    token = _session_id.set(session_id)
    try:
        yield
    finally:
        _session_id.reset(token)


def trace(
    name: str | None = None,
    *,
    extract_session_id: Callable[..., str] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator to trace a function execution with session context.

    This decorator is backend-agnostic. It manages session ID injection
    through context variables, without binding to any specific tracing
    implementation (Langfuse, OpenTelemetry, etc.).

    Args:
        name: Optional name for the trace span. Defaults to function name.
        extract_session_id: Optional callable to extract session_id from
            function arguments. The callable receives all positional (*args)
            and keyword (**kwargs) arguments of the decorated function.

    Returns:
        Decorated function with tracing context management.

    Example:
        @trace(name="process_message")
        async def process_message(msg: InboundMessage) -> Response:
            # session_id is automatically set in context
            return await handle(msg)

        # Or with custom session extraction
        @trace(extract_session_id=lambda msg, **_: msg.session_key.safe_name())
        async def process(msg: InboundMessage) -> Response:
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        span_name = name or func.__name__

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            # Extract session_id if extractor provided
            session_id: str | None = None
            if extract_session_id:
                try:
                    session_id = extract_session_id(*args, **kwargs)
                except Exception as e:
                    logger.debug(f"Failed to extract session_id: {e}")

            # Fall back to current context if no session_id extracted
            if session_id is None:
                session_id = get_current_session_id()

            # Use context manager to set session_id for nested operations
            if session_id:
                with set_session_id(session_id):
                    # Also propagate to langfuse if available
                    from vikingbot.integrations.langfuse import LangfuseClient

                    langfuse = LangfuseClient.get_instance()
                    if langfuse.enabled and hasattr(langfuse, "propagate_attributes"):
                        with langfuse.propagate_attributes(session_id=session_id):
                            return await func(*args, **kwargs)
                    return await func(*args, **kwargs)
            else:
                return await func(*args, **kwargs)

        return async_wrapper  # type: ignore[return-value]

    return decorator
