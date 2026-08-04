# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Circuit breaker and error classification for API call protection."""

from __future__ import annotations

import threading
import time

from openviking.utils.model_retry import (
    ERROR_CLASS_AUTH,
    ERROR_CLASS_INPUT_TOO_LARGE,
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_QUOTA_EXCEEDED,
    classify_api_error,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


# --- Circuit breaker ---

_STATE_CLOSED = "CLOSED"
_STATE_OPEN = "OPEN"
_STATE_HALF_OPEN = "HALF_OPEN"
_PROBE_UNSET = object()


class CircuitBreakerOpen(Exception):
    """Raised when the circuit breaker is open and blocking requests."""


class CircuitBreaker:
    """Thread-safe circuit breaker for API call protection.

    Trips after ``failure_threshold`` consecutive failures (or immediately for
    permanent errors like 403/401). After ``reset_timeout`` seconds, allows one
    probe request (HALF_OPEN). If the probe succeeds, the breaker closes; if it
    fails, the breaker reopens.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout: float = 300,
        max_reset_timeout: float | None = None,
    ):
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._base_reset_timeout = reset_timeout
        self._max_reset_timeout = reset_timeout if max_reset_timeout is None else max_reset_timeout
        self._current_reset_timeout = reset_timeout
        self._lock = threading.Lock()
        self._state = _STATE_CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0
        self._probe_started_at: float = 0
        self._probe_token = 0
        self._active_probe_token: int | None = None

    def _start_probe(self, now: float) -> int:
        self._probe_token += 1
        self._active_probe_token = self._probe_token
        self._probe_started_at = now
        return self._probe_token

    def check(self) -> int | None:
        """Allow the request through and return its HALF_OPEN probe token."""
        with self._lock:
            if self._state == _STATE_CLOSED:
                return None
            now = time.monotonic()
            if self._state == _STATE_HALF_OPEN:
                if self._active_probe_token is None:
                    return self._start_probe(now)
                elapsed = now - self._probe_started_at
                if elapsed >= self._current_reset_timeout:
                    logger.info("Circuit breaker replacing timed-out HALF_OPEN probe")
                    return self._start_probe(now)
                raise CircuitBreakerOpen(
                    "Circuit breaker is HALF_OPEN with a probe in progress, "
                    f"retry after {self._current_reset_timeout - elapsed:.0f}s"
                )
            # OPEN — check if timeout elapsed
            elapsed = now - self._last_failure_time
            if elapsed >= self._current_reset_timeout:
                self._state = _STATE_HALF_OPEN
                logger.info("Circuit breaker transitioning OPEN -> HALF_OPEN (timeout elapsed)")
                return self._start_probe(now)
            raise CircuitBreakerOpen(
                f"Circuit breaker is OPEN, retry after {self._current_reset_timeout - elapsed:.0f}s"
            )

    @property
    def retry_after(self) -> float:
        """Seconds until the breaker may transition to HALF_OPEN, capped at 30s.

        Returns 0 if the breaker is CLOSED or no HALF_OPEN probe is active.
        """
        with self._lock:
            if self._state == _STATE_CLOSED:
                return 0
            if self._state == _STATE_HALF_OPEN:
                if self._active_probe_token is None:
                    return 0
                elapsed = time.monotonic() - self._probe_started_at
            else:
                elapsed = time.monotonic() - self._last_failure_time
            remaining = self._current_reset_timeout - elapsed
            return min(max(remaining, 0), 30)

    def record_success(self, probe_token: int | None | object = _PROBE_UNSET) -> None:
        """Record a successful API call. Resets failure count."""
        with self._lock:
            explicit_token = probe_token is not _PROBE_UNSET
            if self._state == _STATE_HALF_OPEN:
                effective_token = (
                    self._active_probe_token if not explicit_token else probe_token
                )
                if effective_token != self._active_probe_token:
                    logger.debug("Ignoring completion from a stale HALF_OPEN probe")
                    return
                logger.info("Circuit breaker transitioning HALF_OPEN -> CLOSED (probe succeeded)")
            elif self._state == _STATE_OPEN or (
                explicit_token and probe_token is not None
            ):
                logger.debug("Ignoring success from a request admitted before the current state")
                return
            self._failure_count = 0
            self._state = _STATE_CLOSED
            self._current_reset_timeout = self._base_reset_timeout
            self._probe_started_at = 0
            self._active_probe_token = None

    def record_ignored(self, probe_token: int | None | object = _PROBE_UNSET) -> None:
        """Release a probe whose outcome does not describe provider health."""
        with self._lock:
            explicit_token = probe_token is not _PROBE_UNSET
            effective_token = (
                self._active_probe_token if not explicit_token else probe_token
            )
            if (
                self._state == _STATE_HALF_OPEN
                and effective_token is not None
                and effective_token == self._active_probe_token
            ):
                self._probe_started_at = 0
                self._active_probe_token = None

    def record_failure(
        self,
        error: Exception,
        probe_token: int | None | object = _PROBE_UNSET,
    ) -> None:
        """Record a failed API call. May trip the breaker."""
        error_class = classify_api_error(error)
        if error_class == ERROR_CLASS_INPUT_TOO_LARGE:
            logger.info(f"Circuit breaker ignoring row-specific input error: {error}")
            self.record_ignored(probe_token)
            return

        with self._lock:
            explicit_token = probe_token is not _PROBE_UNSET
            effective_token = (
                self._active_probe_token if not explicit_token else probe_token
            )
            if explicit_token:
                if probe_token is None:
                    if self._state != _STATE_CLOSED:
                        logger.debug("Ignoring failure from a request admitted before the current state")
                        return
                elif (
                    self._state != _STATE_HALF_OPEN
                    or effective_token != self._active_probe_token
                ):
                    logger.debug("Ignoring failure from a stale HALF_OPEN probe")
                    return
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == _STATE_HALF_OPEN:
                self._state = _STATE_OPEN
                self._probe_started_at = 0
                self._active_probe_token = None
                self._current_reset_timeout = min(
                    self._current_reset_timeout * 2,
                    self._max_reset_timeout,
                )
                logger.info(
                    f"Circuit breaker transitioning HALF_OPEN -> OPEN (probe failed: {error})"
                )
                return

            if error_class in (
                ERROR_CLASS_PERMANENT,
                ERROR_CLASS_AUTH,
                ERROR_CLASS_QUOTA_EXCEEDED,
            ):
                self._state = _STATE_OPEN
                self._current_reset_timeout = self._base_reset_timeout
                logger.info(f"Circuit breaker tripped immediately on {error_class} error: {error}")
                return

            if self._failure_count >= self._failure_threshold:
                self._state = _STATE_OPEN
                self._current_reset_timeout = self._base_reset_timeout
                logger.info(
                    f"Circuit breaker tripped after {self._failure_count} consecutive "
                    f"failures: {error}"
                )
