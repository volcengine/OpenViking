# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Circuit breaker and error classification for API call protection."""

from __future__ import annotations

import threading
import time

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# --- Error classification ---

_PERMANENT_PATTERNS = ("403", "401", "Forbidden", "Unauthorized", "AccountOverdue")
_TRANSIENT_PATTERNS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "TooManyRequests",
    "RateLimit",
    "timeout",
    "Timeout",
    "ConnectionError",
    "Connection refused",
    "Connection reset",
)


def classify_api_error(error: Exception) -> str:
    """Classify an API error as permanent, transient, or unknown.

    Checks both str(error) and str(error.__cause__) for known patterns.

    Returns:
        "permanent" — 403/401, never retry.
        "transient" — 429/5xx/timeout, safe to retry.
        "unknown"   — unrecognized, treated as transient by callers.
    """
    texts = [str(error)]
    if error.__cause__ is not None:
        texts.append(str(error.__cause__))

    for text in texts:
        for pattern in _PERMANENT_PATTERNS:
            if pattern in text:
                return "permanent"

    for text in texts:
        for pattern in _TRANSIENT_PATTERNS:
            if pattern in text:
                return "transient"

    return "unknown"


# --- Circuit breaker ---

_STATE_CLOSED = "CLOSED"
_STATE_OPEN = "OPEN"
_STATE_HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpen(Exception):
    """Raised when the circuit breaker is open and blocking requests."""


class CircuitBreaker:
    """Thread-safe circuit breaker for API call protection.

    Trips after ``failure_threshold`` consecutive failures (or immediately for
    permanent errors like 403/401). After ``reset_timeout`` seconds, allows one
    probe request (HALF_OPEN). If the probe succeeds, the breaker closes; if it
    fails, the breaker reopens.
    """

    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 300):
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._lock = threading.Lock()
        self._state = _STATE_CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0

    def check(self) -> None:
        """Allow the request through, or raise ``CircuitBreakerOpen``."""
        with self._lock:
            if self._state == _STATE_CLOSED:
                return
            if self._state == _STATE_HALF_OPEN:
                return  # allow probe request
            # OPEN — check if timeout elapsed
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._reset_timeout:
                self._state = _STATE_HALF_OPEN
                logger.info("Circuit breaker transitioning OPEN -> HALF_OPEN (timeout elapsed)")
                return
            raise CircuitBreakerOpen(
                f"Circuit breaker is OPEN, retry after {self._reset_timeout - elapsed:.0f}s"
            )

    @property
    def retry_after(self) -> float:
        """Seconds until the breaker may transition to HALF_OPEN, capped at 30s.

        Returns 0 if the breaker is CLOSED or HALF_OPEN.
        """
        with self._lock:
            if self._state != _STATE_OPEN:
                return 0
            remaining = self._reset_timeout - (time.monotonic() - self._last_failure_time)
            return min(max(remaining, 0), 30)

    def record_success(self) -> None:
        """Record a successful API call. Resets failure count."""
        with self._lock:
            if self._state == _STATE_HALF_OPEN:
                logger.info("Circuit breaker transitioning HALF_OPEN -> CLOSED (probe succeeded)")
            self._failure_count = 0
            self._state = _STATE_CLOSED

    def record_failure(self, error: Exception) -> None:
        """Record a failed API call. May trip the breaker."""
        error_class = classify_api_error(error)
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == _STATE_HALF_OPEN:
                self._state = _STATE_OPEN
                logger.info(
                    f"Circuit breaker transitioning HALF_OPEN -> OPEN (probe failed: {error})"
                )
                return

            if error_class == "permanent":
                self._state = _STATE_OPEN
                logger.info(f"Circuit breaker tripped immediately on permanent error: {error}")
                return

            if self._failure_count >= self._failure_threshold:
                self._state = _STATE_OPEN
                logger.info(
                    f"Circuit breaker tripped after {self._failure_count} consecutive "
                    f"failures: {error}"
                )
