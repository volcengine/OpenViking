from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

PERMANENT_API_ERROR_PATTERNS = (
    "400",
    "401",
    "403",
    "Forbidden",
    "Unauthorized",
    "AccountOverdue",
)

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
    """Classify an API error as permanent, transient, or unknown."""
    texts = [str(error)]
    if error.__cause__ is not None:
        texts.append(str(error.__cause__))

    for text in texts:
        for pattern in PERMANENT_API_ERROR_PATTERNS:
            if pattern in text:
                return "permanent"

    for text in texts:
        for pattern in TRANSIENT_API_ERROR_PATTERNS:
            if pattern in text:
                return "transient"

    return "unknown"


def is_retryable_api_error(error: Exception) -> bool:
    """Return True if the error should be retried."""
    return classify_api_error(error) == "transient"


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
