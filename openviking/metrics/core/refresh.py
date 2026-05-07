# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Refresh control primitives for scrape-triggered metrics refresh.

RefreshGate implements TTL-based refresh control with SWR support:
- If a collector never succeeded, we must refresh synchronously (first data).
- If TTL is still valid, we skip refresh to avoid repeated expensive work.
- If TTL expired and we have a previous success, callers may decide to:
  - refresh synchronously (block scrape), or
  - refresh asynchronously in background and serve stale values (SWR).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RefreshDecision:
    """
    Decision returned by `RefreshGate.decide()`.

    Attributes:
        do_refresh: Whether the caller should start a collection attempt now.
        reason: Machine-readable explanation for the decision, such as `ttl_valid`,
            `ttl_expired`, `never_succeeded`, or `inflight`.
        last_success_at: Monotonic timestamp of the last successful collection, or `None`
            when the collector has not produced a successful result yet.
        inflight: Whether another collection attempt is currently running for the same gate.
    """

    do_refresh: bool
    reason: str
    last_success_at: float | None
    inflight: bool


class RefreshGate:
    """
    A small state machine that controls refresh frequency and tracks inflight refresh.

    The gate encapsulates the refresh bookkeeping needed by scrape-triggered collectors:
    whether a prior success exists, whether another refresh is currently running, and whether
    the TTL window has expired enough to justify a new collection attempt.
    """

    def __init__(self, *, ttl_seconds: float) -> None:
        """
        Initialize a refresh gate for a single refresh-managed collector.

        Args:
            ttl_seconds: Minimum interval between two successful refresh-triggered collection
                attempts before the gate considers the cached result expired again.
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = float(ttl_seconds)
        self._lock = threading.Lock()
        self._last_success_at: float | None = None
        self._inflight: bool = False

    def decide(self, *, now: float | None = None) -> RefreshDecision:
        """
        Decide whether a new collection attempt should start at the current time.

        Args:
            now: Optional monotonic timestamp override used mainly for tests. When omitted,
                the current `time.monotonic()` value is used.

        Returns:
            A `RefreshDecision` describing whether collection should proceed immediately and
            why that decision was made.
        """
        t = time.monotonic() if now is None else float(now)
        with self._lock:
            last = self._last_success_at
            inflight = self._inflight
            if inflight:
                return RefreshDecision(
                    do_refresh=False,
                    reason="inflight",
                    last_success_at=last,
                    inflight=True,
                )
            if last is None:
                return RefreshDecision(
                    do_refresh=True,
                    reason="never_succeeded",
                    last_success_at=None,
                    inflight=False,
                )
            if t - last >= self._ttl_seconds:
                return RefreshDecision(
                    do_refresh=True,
                    reason="ttl_expired",
                    last_success_at=last,
                    inflight=False,
                )
            return RefreshDecision(
                do_refresh=False,
                reason="ttl_valid",
                last_success_at=last,
                inflight=False,
            )

    def mark_inflight(self) -> bool:
        """
        Mark the gate as currently running a collection attempt.

        Returns:
            `True` if the caller successfully acquired the inflight slot, or `False` if another
            collection attempt was already running.
        """
        with self._lock:
            if self._inflight:
                return False
            self._inflight = True
            return True

    def mark_complete(self, *, success: bool, now: float | None = None) -> None:
        """
        Finish the inflight state and optionally record a successful completion timestamp.

        Args:
            success: Whether the just-finished collection attempt completed successfully.
                Only successful attempts advance `_last_success_at`.
            now: Optional monotonic timestamp override used mainly for tests. When omitted,
                the current `time.monotonic()` value is used.
        """
        t = time.monotonic() if now is None else float(now)
        with self._lock:
            self._inflight = False
            if success:
                self._last_success_at = t
