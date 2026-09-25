# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Circuit breaker and error classification for API call protection."""

from __future__ import annotations

import asyncio
import threading
import time
from contextvars import ContextVar

from openviking.utils.model_retry import (
    ERROR_CLASS_AUTH,
    ERROR_CLASS_CONTENT_SAFETY,
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


class CircuitBreakerOpen(Exception):
    """Raised when the circuit breaker is open and blocking requests."""


class CircuitBreaker:
    """Thread-safe circuit breaker for API call protection.

    Trips after ``failure_threshold`` consecutive failures (or immediately for
    credential/quota errors). After ``reset_timeout`` seconds, allows requests
    again (HALF_OPEN). If a request succeeds, the breaker closes; if it
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
        self._generation = 0
        self._probe_in_flight = False
        self._admission: ContextVar[tuple[int, tuple[int, object]] | None] = ContextVar(
            f"circuit_breaker_admission_{id(self)}", default=None
        )

    @staticmethod
    def _execution_identity() -> tuple[int, object]:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        return threading.get_ident(), task

    def _admit_current_execution(self) -> None:
        self._admission.set((self._generation, self._execution_identity()))

    def _take_admission(self) -> int | None:
        admission = self._admission.get()
        self._admission.set(None)
        if admission is None or admission[1] != self._execution_identity():
            return None
        return admission[0]

    def _close_from_probe(self) -> None:
        self._failure_count = 0
        self._state = _STATE_CLOSED
        self._probe_in_flight = False
        self._current_reset_timeout = self._base_reset_timeout
        self._generation += 1

    def abandon(self) -> None:
        """Release this execution's unused HALF_OPEN probe admission.

        Callers invoke this from ``finally`` so cancellation and early-return
        paths cannot leave the breaker permanently stuck in HALF_OPEN.
        """
        admission_generation = self._take_admission()
        with self._lock:
            if (
                self._state == _STATE_HALF_OPEN
                and self._probe_in_flight
                and admission_generation == self._generation
            ):
                self._state = _STATE_OPEN
                self._probe_in_flight = False
                self._generation += 1

    def check(self) -> None:
        """Allow the request through, or raise ``CircuitBreakerOpen``."""
        with self._lock:
            admission = self._admission.get()
            if (
                admission is not None
                and admission[0] == self._generation
                and admission[1] == self._execution_identity()
            ):
                # Admission is scoped to this execution. This makes a bounded
                # wait composable with a downstream provider check without
                # admitting a second HALF_OPEN probe.
                return
            if self._state == _STATE_CLOSED:
                self._admit_current_execution()
                return
            if self._state == _STATE_HALF_OPEN:
                self._admission.set(None)
                raise CircuitBreakerOpen("Circuit breaker is HALF_OPEN; probe in progress")
            # OPEN — check if timeout elapsed
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._current_reset_timeout:
                self._state = _STATE_HALF_OPEN
                self._generation += 1
                self._probe_in_flight = True
                self._admit_current_execution()
                logger.info("Circuit breaker transitioning OPEN -> HALF_OPEN (timeout elapsed)")
                return
            self._admission.set(None)
            raise CircuitBreakerOpen(
                f"Circuit breaker is OPEN, retry after {self._current_reset_timeout - elapsed:.0f}s"
            )

    async def wait_until_ready(self, *, deadline_at: float | None = None) -> None:
        """Wait for admission within one existing cooldown, without retrying work.

        The bound is captured once: failures from other deliveries cannot keep
        extending this delivery's wait. A caller's absolute deadline may shorten
        it. Cancellation propagates so the queue can retain its unacknowledged
        delivery. No model call has started or consumed an attempt here.
        """
        deadline = (
            time.monotonic() + max(0.0, deadline_at - time.time())
            if deadline_at is not None
            else None
        )
        wait_until: float | None = None
        while True:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                raise CircuitBreakerOpen("Model admission deadline exceeded")
            try:
                self.check()
                return
            except CircuitBreakerOpen:
                if wait_until is None:
                    with self._lock:
                        wait_until = self._last_failure_time + self._current_reset_timeout
                    if deadline is not None:
                        wait_until = min(wait_until, deadline)
                remaining = wait_until - time.monotonic()
                if remaining <= 0:
                    raise CircuitBreakerOpen("Model circuit breaker open after admission wait")
                await asyncio.sleep(min(remaining, 30.0))

    @property
    def retry_after(self) -> float:
        """Seconds until the breaker may transition to HALF_OPEN, capped at 30s.

        Returns 0 if the breaker is CLOSED or HALF_OPEN.
        """
        with self._lock:
            if self._state != _STATE_OPEN:
                return 0
            remaining = self._current_reset_timeout - (time.monotonic() - self._last_failure_time)
            return min(max(remaining, 0), 30)

    def record_success(self) -> None:
        """Record a successful API call. Resets failure count."""
        admission_generation = self._take_admission()
        with self._lock:
            if self._state == _STATE_HALF_OPEN:
                if admission_generation != self._generation or not self._probe_in_flight:
                    return
                logger.info("Circuit breaker transitioning HALF_OPEN -> CLOSED (probe succeeded)")
                self._close_from_probe()
                return
            if self._state == _STATE_OPEN:
                # A request admitted by an older generation completed after the
                # circuit reopened. It cannot certify the current generation.
                return
            if admission_generation is not None and admission_generation != self._generation:
                return
            self._failure_count = 0
            self._current_reset_timeout = self._base_reset_timeout

    def record_failure(self, error: Exception) -> None:
        """Record a failed API call. May trip the breaker."""
        error_class = classify_api_error(error)
        admission_generation = self._take_admission()
        if error_class in (
            ERROR_CLASS_INPUT_TOO_LARGE,
            ERROR_CLASS_CONTENT_SAFETY,
            ERROR_CLASS_PERMANENT,
        ):
            logger.info(f"Circuit breaker ignoring request-specific error: {error}")
            with self._lock:
                if (
                    self._state == _STATE_HALF_OPEN
                    and self._probe_in_flight
                    and admission_generation == self._generation
                ):
                    # A provider response proves availability even when that
                    # particular request is invalid or rejected.
                    self._close_from_probe()
            return

        with self._lock:
            if self._state == _STATE_HALF_OPEN:
                if admission_generation != self._generation or not self._probe_in_flight:
                    return
                self._failure_count += 1
                self._last_failure_time = time.monotonic()
                self._state = _STATE_OPEN
                self._probe_in_flight = False
                self._current_reset_timeout = min(
                    self._current_reset_timeout * 2,
                    self._max_reset_timeout,
                )
                self._generation += 1
                logger.info(
                    f"Circuit breaker transitioning HALF_OPEN -> OPEN (probe failed: {error})"
                )
                return
            if self._state == _STATE_OPEN and admission_generation is not None:
                # A result from a request admitted before this OPEN generation
                # is stale. Unscoped record_failure calls are still accepted
                # for compatibility with external health signals.
                return
            if admission_generation is not None and admission_generation != self._generation:
                return

            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if error_class in (
                ERROR_CLASS_AUTH,
                ERROR_CLASS_QUOTA_EXCEEDED,
            ):
                self._state = _STATE_OPEN
                self._probe_in_flight = False
                self._current_reset_timeout = self._base_reset_timeout
                self._generation += 1
                logger.info(f"Circuit breaker tripped immediately on {error_class} error: {error}")
                return

            if self._failure_count >= self._failure_threshold:
                self._state = _STATE_OPEN
                self._probe_in_flight = False
                self._current_reset_timeout = self._base_reset_timeout
                self._generation += 1
                logger.info(
                    f"Circuit breaker tripped after {self._failure_count} consecutive "
                    f"failures: {error}"
                )
