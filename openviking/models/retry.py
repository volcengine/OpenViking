# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Unified retry logic for VLM backends and embedding providers.

Provides three public helpers:

- ``is_transient_error`` — classifies an exception as transient (retryable)
  or permanent (should propagate immediately).
- ``transient_retry`` — synchronous retry loop with exponential backoff.
- ``transient_retry_async`` — asynchronous counterpart using ``asyncio.sleep``.

Transient errors are those that may resolve on their own (rate-limits, temporary
server errors, network resets).  Permanent errors indicate a caller mistake
(bad auth, invalid input) and should never be retried.

Usage example::

    result = transient_retry(lambda: client.chat(...), max_retries=3)
    result = await transient_retry_async(lambda: client.chat_async(...), max_retries=3)
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from typing import Optional, TypeVar

logger = logging.getLogger("openviking.models.retry")

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Status code helpers
# ---------------------------------------------------------------------------

_TRANSIENT_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_PERMANENT_STATUS_CODES: frozenset[int] = frozenset({400, 401, 403, 404, 422})

# String patterns — permanent check runs first (more specific)
_PERMANENT_STR_PATTERNS: tuple[str, ...] = (
    "InvalidRequestError",
    "AuthenticationError",
)
_TRANSIENT_STR_PATTERNS: tuple[str, ...] = (
    "TooManyRequests",
    "RateLimit",
    "RequestBurstTooFast",
    "timed out",
    "timeout",
)


def _extract_status_code(exc: Exception) -> int | None:
    """Return numeric HTTP status from common status-bearing attributes.

    Checks ``.status_code``, ``.code``, and ``.http_status`` in that order.
    Returns ``None`` if none of the attributes exist or hold an integer.
    """
    for attr in ("status_code", "code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


# ---------------------------------------------------------------------------
# is_transient_error
# ---------------------------------------------------------------------------


def is_transient_error(exc: Exception) -> bool:
    """Classify an exception as transient (retryable) or permanent.

    Evaluation order:
    1. Extract numeric status code from the exception attributes; check
       permanent codes first, then transient codes.
    2. Check the exception type directly (built-in connection / timeout types).
    3. Scan ``str(exc)`` for known permanent string patterns, then transient
       ones.
    4. Attempt to import ``openai`` and check against its error hierarchy.
    5. Default to ``False`` (conservative — unknown errors are not retried).

    Args:
        exc: The exception to classify.

    Returns:
        ``True`` if the error is likely transient and worth retrying.
        ``False`` for permanent errors or any unrecognised exception.
    """
    # ── 1. Numeric status code ────────────────────────────────────────────
    status = _extract_status_code(exc)
    if status is not None:
        if status in _PERMANENT_STATUS_CODES:
            return False
        if status in _TRANSIENT_STATUS_CODES:
            return True

    # ── 2. Exception type ─────────────────────────────────────────────────
    # asyncio.TimeoutError is a subclass of TimeoutError on 3.11+, but treat
    # both explicitly for clarity on 3.10.
    if isinstance(exc, (ConnectionError, ConnectionResetError, ConnectionRefusedError)):
        return True
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True

    # ── 3. String patterns ────────────────────────────────────────────────
    message = str(exc)

    for pattern in _PERMANENT_STR_PATTERNS:
        if pattern in message:
            return False

    for pattern in _TRANSIENT_STR_PATTERNS:
        if pattern in message:
            return True

    # ── 4. openai error types (optional dependency) ───────────────────────
    try:
        import openai  # type: ignore[import-untyped]

        # Permanent openai errors — check before transient
        if isinstance(exc, openai.AuthenticationError):
            return False

        # Transient openai errors
        if isinstance(
            exc, (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError)
        ):
            return True
    except ImportError:
        pass

    # ── 5. Default: do not retry unknown errors ───────────────────────────
    return False


# ---------------------------------------------------------------------------
# transient_retry (sync)
# ---------------------------------------------------------------------------


def transient_retry(
    func: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: bool = True,
    is_retryable: Optional[Callable[[Exception], bool]] = None,
) -> T:
    """Call *func* and retry on transient failures with exponential backoff.

    The delay between attempts follows the formula::

        delay = min(base_delay * 2^attempt, max_delay)

    When ``jitter=True`` the delay is multiplied by a random factor in
    ``[0.5, 1.5)`` to spread concurrent retries.

    Args:
        func: Zero-argument callable to invoke.
        max_retries: Maximum number of *additional* attempts after the first
            failure.  ``0`` disables retrying entirely.
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Upper bound on the computed delay (seconds).
        jitter: Whether to apply random jitter to the delay.
        is_retryable: Optional predicate that decides whether an exception
            should be retried.  Defaults to ``is_transient_error``.

    Returns:
        The return value of *func* on success.

    Raises:
        Exception: The last exception raised by *func* after all retries are
            exhausted, or immediately if the error is not retryable.
    """
    _check = is_retryable if is_retryable is not None else is_transient_error

    last_exc: Exception
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as exc:
            last_exc = exc

            if not _check(exc):
                # Permanent — propagate immediately
                raise

            if attempt >= max_retries:
                # Retries exhausted
                logger.warning(
                    "transient_retry: all %d retries exhausted; last error: %s",
                    max_retries,
                    exc,
                )
                raise

            delay = min(base_delay * (2**attempt), max_delay)
            if jitter:
                delay *= 0.5 + random.random()  # [0.5, 1.5)

            logger.info(
                "transient_retry: attempt %d/%d failed (%s); retrying in %.2fs",
                attempt + 1,
                max_retries,
                exc,
                delay,
            )
            time.sleep(delay)

    # Unreachable, but satisfies the type checker
    raise last_exc  # type: ignore[possibly-undefined]


# ---------------------------------------------------------------------------
# transient_retry_async
# ---------------------------------------------------------------------------


async def transient_retry_async(
    coro_func: Callable[[], "asyncio.Coroutine[object, object, T]"],
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: bool = True,
    is_retryable: Optional[Callable[[Exception], bool]] = None,
) -> T:
    """Async version of :func:`transient_retry`.

    Identical semantics to the sync variant but uses ``asyncio.sleep``
    so it does not block the event loop during backoff.

    Args:
        coro_func: Zero-argument async callable (coroutine factory) to invoke.
        max_retries: Maximum number of *additional* attempts after the first
            failure.  ``0`` disables retrying entirely.
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Upper bound on the computed delay (seconds).
        jitter: Whether to apply random jitter to the delay.
        is_retryable: Optional predicate that decides whether an exception
            should be retried.  Defaults to ``is_transient_error``.

    Returns:
        The return value of *coro_func()* on success.

    Raises:
        Exception: The last exception raised by *coro_func* after all retries
            are exhausted, or immediately if the error is not retryable.
    """
    _check = is_retryable if is_retryable is not None else is_transient_error

    last_exc: Exception
    for attempt in range(max_retries + 1):
        try:
            return await coro_func()
        except Exception as exc:
            last_exc = exc

            if not _check(exc):
                raise

            if attempt >= max_retries:
                logger.warning(
                    "transient_retry_async: all %d retries exhausted; last error: %s",
                    max_retries,
                    exc,
                )
                raise

            delay = min(base_delay * (2**attempt), max_delay)
            if jitter:
                delay *= 0.5 + random.random()

            logger.info(
                "transient_retry_async: attempt %d/%d failed (%s); retrying in %.2fs",
                attempt + 1,
                max_retries,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    raise last_exc  # type: ignore[possibly-undefined]
