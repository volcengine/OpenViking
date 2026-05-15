from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

# Error classification categories returned by classify_api_error()
ERROR_CLASS_PERMANENT = "permanent"
ERROR_CLASS_QUOTA_EXCEEDED = "quota_exceeded"
ERROR_CLASS_TRANSIENT = "transient"
ERROR_CLASS_UNKNOWN = "unknown"

PERMANENT_API_ERROR_PATTERNS = (
    "400",
    "401",
    "403",
    "Forbidden",
    "Unauthorized",
    "AccountOverdue",
)

QUOTA_EXCEEDED_PATTERNS = (
    "AccountQuotaExceeded",
    "quota limit",
    "quota exceed",
    "usage quota",
)

_PERMANENT_IO_ERRORS = (FileNotFoundError, PermissionError, IsADirectoryError, NotADirectoryError)

TRANSIENT_API_ERROR_PATTERNS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "TooManyRequests",
    "RateLimit",
    "RequestBurstTooFast",
    "timeout",
    "Timeout",
    "ConnectionError",
    "Connection refused",
    "Connection reset",
)


def classify_api_error(error: Exception) -> str:
    """Classify an API error as permanent, quota_exceeded, transient, or unknown.

    ``quota_exceeded`` is checked before ``transient`` because quota errors
    typically include "429" / "TooManyRequests" which would otherwise match
    the transient category.  Quota errors should not be retried; the caller
    should fail over to a backup model instead.
    """
    for exc in (error, getattr(error, "__cause__", None)):
        if exc is not None and isinstance(exc, _PERMANENT_IO_ERRORS):
            return ERROR_CLASS_PERMANENT

    texts = [str(error)]
    if error.__cause__ is not None:
        texts.append(str(error.__cause__))

    for text in texts:
        for pattern in PERMANENT_API_ERROR_PATTERNS:
            if pattern in text:
                return ERROR_CLASS_PERMANENT

    # Check quota_exceeded *before* transient so that "429 … AccountQuotaExceeded"
    # is classified as quota_exceeded, not transient.
    for text in texts:
        for pattern in QUOTA_EXCEEDED_PATTERNS:
            if pattern.lower() in text.lower():
                return ERROR_CLASS_QUOTA_EXCEEDED

    for text in texts:
        for pattern in TRANSIENT_API_ERROR_PATTERNS:
            if pattern in text:
                return ERROR_CLASS_TRANSIENT

    return ERROR_CLASS_UNKNOWN


def is_quota_exceeded_api_error(error: Exception) -> bool:
    """Return True if the error indicates an account quota has been exceeded."""
    return classify_api_error(error) == ERROR_CLASS_QUOTA_EXCEEDED


def is_retryable_api_error(error: Exception) -> bool:
    """Return True if the error should be retried."""
    return classify_api_error(error) == ERROR_CLASS_TRANSIENT


def _compute_delay(
    attempt: int,
    *,
    base_delay: float,
    max_delay: float,
    jitter: bool,
) -> float:
    delay = min(base_delay * (2**attempt), max_delay)
    if jitter:
        delay += random.uniform(0.0, min(base_delay, delay))
    return delay


def retry_sync(
    func: Callable[[], T],
    *,
    max_retries: int,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: bool = True,
    is_retryable: Callable[[Exception], bool] = is_retryable_api_error,
    logger=None,
    operation_name: str = "operation",
) -> T:
    """Retry a sync function on known transient errors."""
    attempt = 0

    while True:
        try:
            return func()
        except Exception as e:
            if max_retries <= 0 or attempt >= max_retries or not is_retryable(e):
                raise

            delay = _compute_delay(
                attempt,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
            )
            if logger:
                logger.warning(
                    "%s failed with retryable error (retry %d/%d): %s; retrying in %.2fs",
                    operation_name,
                    attempt + 1,
                    max_retries,
                    e,
                    delay,
                )
            time.sleep(delay)
            attempt += 1


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: bool = True,
    is_retryable: Callable[[Exception], bool] = is_retryable_api_error,
    logger=None,
    operation_name: str = "operation",
) -> T:
    """Retry an async function on known transient errors."""
    attempt = 0

    while True:
        try:
            return await func()
        except Exception as e:
            if max_retries <= 0 or attempt >= max_retries or not is_retryable(e):
                raise

            delay = _compute_delay(
                attempt,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
            )
            if logger:
                logger.warning(
                    "%s failed with retryable error (retry %d/%d): %s; retrying in %.2fs",
                    operation_name,
                    attempt + 1,
                    max_retries,
                    e,
                    delay,
                )
            await asyncio.sleep(delay)
            attempt += 1
