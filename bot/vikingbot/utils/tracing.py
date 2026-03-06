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

# Try to import langfuse observe decorator
try:
    from langfuse.decorators import observe as langfuse_observe
except ImportError:
    langfuse_observe = None


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
    extract_user_id: Callable[..., str] | None = None,
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
        extract_user_id: Optional callable to extract user_id from
            function arguments (e.g., lambda msg: msg.sender_id).

    Returns:
        Decorated function with tracing context management.

    Example:
        @trace(
            name="process_message",
            extract_session_id=lambda msg: msg.session_key.safe_name(),
            extract_user_id=lambda msg: msg.sender_id,
        )
        async def process_message(msg: InboundMessage) -> Response:
            # session_id and user_id are automatically propagated
            return await handle(msg)
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        span_name = name or func.__name__

        # Apply @observe decorator if available for Langfuse tracing
        wrapped_func = func
        if langfuse_observe is not None:
            wrapped_func = langfuse_observe(name=span_name)(func)

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            # Extract session_id if extractor provided
            session_id: str | None = None
            if extract_session_id:
                try:
                    # Inspect the extractor's signature to determine how to call it
                    import inspect

                    sig = inspect.signature(extract_session_id)
                    param_count = len(
                        [
                            p
                            for p in sig.parameters.values()
                            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                        ]
                    )

                    if param_count == 1 and len(args) >= 1:
                        # Extractor expects single arg (e.g., lambda msg: ...)
                        # Use the last arg which is typically the message/object
                        session_id = extract_session_id(args[-1])
                    else:
                        # Extractor expects multiple args or specific signature
                        session_id = extract_session_id(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"Failed to extract session_id: {e}")

            # Extract user_id if extractor provided
            user_id: str | None = None
            if extract_user_id:
                try:
                    import inspect

                    sig = inspect.signature(extract_user_id)
                    param_count = len(
                        [
                            p
                            for p in sig.parameters.values()
                            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                        ]
                    )

                    if param_count == 1 and len(args) >= 1:
                        user_id = extract_user_id(args[-1])
                    else:
                        user_id = extract_user_id(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"Failed to extract user_id: {e}")

            # Fall back to current context if no session_id extracted
            if session_id is None:
                session_id = get_current_session_id()
                logger.debug(f"[TRACE] No session_id extracted, using context: {session_id}")
            else:
                # logger.info(f"[TRACE] Extracted session_id: {session_id}")
                pass

            if user_id:
                # logger.info(f"[TRACE] Extracted user_id: {user_id}")
                pass

            # Use context manager to set session_id for nested operations
            if session_id:
                with set_session_id(session_id):
                    # Also propagate to langfuse if available
                    from vikingbot.integrations.langfuse import LangfuseClient

                    langfuse = LangfuseClient.get_instance()
                    has_propagate = hasattr(langfuse, "propagate_attributes")
                    # logger.info(f"[LANGFUSE] Client status: enabled={langfuse.enabled}, has_propagate_attributes={has_propagate}")
                    if langfuse.enabled and has_propagate:
                        # logger.info(f"[LANGFUSE] Starting trace with attributes: session_id={session_id}, user_id={user_id}")
                        with langfuse.propagate_attributes(session_id=session_id, user_id=user_id):
                            return await wrapped_func(*args, **kwargs)
                    else:
                        if not has_propagate:
                            logger.warning(f"[LANGFUSE] propagate_attributes not available")
                    return await wrapped_func(*args, **kwargs)
            else:
                return await wrapped_func(*args, **kwargs)

        return async_wrapper  # type: ignore[return-value]

    return decorator
