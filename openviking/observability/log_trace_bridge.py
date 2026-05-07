# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Log to Trace Bridge Module.

Provides convenience functions for developers to log messages and span events/exceptions
simultaneously, reducing redundant code and simplifying development.

Key features:
- log_and_add_event: Log message and add span event simultaneously
- log_exception: Log error and record span exception simultaneously
- Preserves original types for OTel attributes (str, int, float, bool, arrays)
- Safe fallback: only logs when OTel is not available
"""

from __future__ import annotations

import logging
from typing import Any, Sequence, Union

# Try to import opentelemetry - will be None if not installed
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import Status, StatusCode
except ImportError:
    otel_trace = None
    Status = None
    StatusCode = None


# Define OTel-compatible attribute value types
OTelAttributeValue = Union[
    str,
    int,
    float,
    bool,
    Sequence[str],
    Sequence[int],
    Sequence[float],
    Sequence[bool],
]


def _to_otel_attributes(kwargs: dict[str, Any]) -> dict[str, OTelAttributeValue]:
    """
    Convert kwargs to OTel-compatible span attributes.

    Preserves original types for supported types (str, int, float, bool, and homogeneous arrays).
    Unsupported types are converted to strings.

    Args:
        kwargs: Dictionary of key-value pairs to convert.

    Returns:
        Dictionary of OTel-compatible attributes.
    """
    attributes: dict[str, OTelAttributeValue] = {}

    for key, value in kwargs.items():
        if value is None:
            # Skip None values
            continue
        elif isinstance(value, (str, int, float, bool)):
            # Primitive types - use as-is
            attributes[key] = value
        elif isinstance(value, (list, tuple)):
            # Array types - check if homogeneous
            if not value:
                # Empty array - skip
                continue

            # Check if all elements are of the same primitive type
            first_type = type(value[0])
            if first_type not in (str, int, float, bool):
                # Mixed or unsupported types - convert to string array
                attributes[key] = [str(v) for v in value]
                continue

            # Verify all elements are of the same type
            all_same_type = all(isinstance(v, first_type) for v in value)
            if all_same_type:
                # Homogeneous array - use as-is
                attributes[key] = list(value)
            else:
                # Mixed types - convert to string array
                attributes[key] = [str(v) for v in value]
        else:
            # Other types - convert to string
            attributes[key] = str(value)

    return attributes


def log_and_add_event(logger: logging.Logger, level: int, message: str, **kwargs: Any) -> None:
    """
    Log a message and add it as a span event simultaneously.

    This function allows developers to log messages and record span events
    in a single call, reducing redundant code.

    Args:
        logger: The logger instance to use.
        level: The logging level (logging.INFO, logging.DEBUG, etc.).
        message: The log message.
        **kwargs: Additional key-value pairs to be included as log extra
            and span attributes. Supported types: str, int, float, bool,
            and homogeneous arrays of these types. Unsupported types are
            converted to strings.
    """
    # Log the message
    logger.log(level, message, extra=kwargs)

    # If OTel is available, also record as a span event
    if otel_trace is not None:
        try:
            current_span = otel_trace.get_current_span()
            if current_span is not None and current_span.is_recording():
                # Convert kwargs to OTel-compatible attributes, preserving types
                attributes = _to_otel_attributes(kwargs)
                current_span.add_event(message, attributes=attributes)
        except Exception:
            # Best-effort only - don't break logging if span event fails
            pass


def log_exception(logger: logging.Logger, message: str, exc: Exception, **kwargs: Any) -> None:
    """
    Log an error and record it as a span exception simultaneously.

    This function logs the exception with full stack trace and also records
    it in the current OTel span if available.

    Args:
        logger: The logger instance to use.
        message: The error message.
        exc: The exception object.
        **kwargs: Additional key-value pairs to be included as log extra
            and span attributes.
    """
    # Log the error with exception stack trace
    logger.error(message, exc_info=exc, extra=kwargs)

    # If OTel is available, also record the exception in the span
    if otel_trace is not None and Status is not None and StatusCode is not None:
        try:
            current_span = otel_trace.get_current_span()
            if current_span is not None and current_span.is_recording():
                # Convert kwargs to OTel-compatible attributes
                attributes = _to_otel_attributes(kwargs)
                attributes["message"] = message

                # Record the exception in the span
                current_span.record_exception(exc, attributes=attributes)
                current_span.set_status(Status(StatusCode.ERROR, description=str(exc)))
        except Exception:
            # Best-effort only - don't break logging if span recording fails
            pass


def log_debug(logger: logging.Logger, message: str, **kwargs: Any) -> None:
    """
    Log a DEBUG level message and add it as a span event.

    Convenience wrapper around log_and_add_event with DEBUG level.

    Args:
        logger: The logger instance to use.
        message: The log message.
        **kwargs: Additional key-value pairs.
    """
    log_and_add_event(logger, logging.DEBUG, message, **kwargs)


def log_info(logger: logging.Logger, message: str, **kwargs: Any) -> None:
    """
    Log an INFO level message and add it as a span event.

    Convenience wrapper around log_and_add_event with INFO level.

    Args:
        logger: The logger instance to use.
        message: The log message.
        **kwargs: Additional key-value pairs.
    """
    log_and_add_event(logger, logging.INFO, message, **kwargs)


def log_warning(logger: logging.Logger, message: str, **kwargs: Any) -> None:
    """
    Log a WARNING level message and add it as a span event.

    Convenience wrapper around log_and_add_event with WARNING level.

    Args:
        logger: The logger instance to use.
        message: The log message.
        **kwargs: Additional key-value pairs.
    """
    log_and_add_event(logger, logging.WARNING, message, **kwargs)


def log_error(logger: logging.Logger, message: str, **kwargs: Any) -> None:
    """
    Log an ERROR level message and add it as a span event.

    Convenience wrapper around log_and_add_event with ERROR level.
    Note: This does not record an exception. Use log_exception for that.

    Args:
        logger: The logger instance to use.
        message: The log message.
        **kwargs: Additional key-value pairs.
    """
    log_and_add_event(logger, logging.ERROR, message, **kwargs)
