# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Collector orchestration for Prometheus exposition.

This module implements the "scrape-triggered collection" workflow:
- `/metrics` export calls `CollectorManager.refresh_all(...)` before rendering.
- Each collector may have TTL-based refresh control (RefreshGate).
- When TTL is expired but we have a previous successful refresh, we apply SWR:
  return immediately (do not block scrape) and refresh in background.

The goal is to keep `/metrics` fast and reliable even when some collectors
depend on slow/unstable subsystems.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import cast

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.core.refresh import RefreshGate

from .base import Refreshable


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """
    Execution outcome for a single collector refresh attempt.

    Attributes:
        collector: Collector name associated with this refresh result.
        attempted: Whether the manager actually started a collection attempt.
        success: Whether the attempt or scheduling decision is considered successful.
        reason: Machine-readable outcome such as `ok`, `timeout`, `ttl_valid`, or
            `swr_triggered`.
    """

    collector: str
    attempted: bool
    success: bool
    reason: str


class CollectorManager:
    """
    Manages a set of collectors and executes collection with timeouts and refresh control.

    - Collectors with `config.ttl_seconds is None` always attempt refresh on each scrape.
    - Collectors with TTL use RefreshGate to avoid repeated refresh within TTL.
    - When TTL is expired and we have a previous successful refresh, `refresh_all`
      triggers a background refresh and returns a `swr_triggered` result immediately.
    """

    def __init__(self) -> None:
        """Initialize an empty collector set, its refresh gates, and tracked background tasks."""
        self._collectors: list[MetricCollector] = []
        self._gates: dict[str, RefreshGate] = {}
        self._background_tasks: set[asyncio.Task[RefreshResult]] = set()

    def register(self, collector: MetricCollector) -> None:
        """
        Register a collector into the refresh pipeline.

        If the collector has `config.ttl_seconds`, a gate is created to enforce TTL + SWR.
        """
        if not isinstance(collector, Refreshable):
            raise TypeError("CollectorManager only accepts Refreshable collectors")
        self._collectors.append(collector)
        collector_name = self._collector_name(collector)
        ttl_seconds = getattr(cast(Refreshable, collector).config, "ttl_seconds", None)
        if ttl_seconds is not None:
            self._gates[collector_name] = RefreshGate(ttl_seconds=float(ttl_seconds))

    async def refresh_all(self, registry, *, deadline_seconds: float = 1.0) -> list[RefreshResult]:
        """
        Refresh all registered collectors under a global deadline.

        Args:
            registry: MetricRegistry instance to be written by collectors.
            deadline_seconds: Total time budget for the entire refresh stage.

        Returns:
            A list of RefreshResult items, including "ttl_valid" / "swr_triggered"
            decisions made by the RefreshGate.
        """
        if not self._collectors:
            return []

        start = time.monotonic()
        results: list[RefreshResult] = []
        tasks: list[asyncio.Task[RefreshResult]] = []

        for c in self._collectors:
            collector_name = self._collector_name(c)
            gate = self._gates.get(collector_name)
            timeout_seconds = float(getattr(cast(Refreshable, c).config, "timeout_seconds", 0.5))
            if gate is None:
                tasks.append(
                    asyncio.create_task(
                        self._run_collector(c, registry, timeout=timeout_seconds),
                        name=collector_name,
                    )
                )
                continue

            decision = gate.decide(now=time.monotonic())
            if not decision.do_refresh:
                results.append(
                    RefreshResult(
                        collector=collector_name,
                        attempted=False,
                        success=True,
                        reason=decision.reason,
                    )
                )
                continue
            if not gate.mark_inflight():
                results.append(
                    RefreshResult(
                        collector=collector_name,
                        attempted=False,
                        success=True,
                        reason="inflight",
                    )
                )
                continue
            if decision.reason == "ttl_expired" and decision.last_success_at is not None:
                task = asyncio.create_task(
                    self._run_collector_with_gate(c, registry, gate=gate, timeout=timeout_seconds),
                    name=collector_name,
                )
                self._track_background(task)
                results.append(
                    RefreshResult(
                        collector=collector_name,
                        attempted=False,
                        success=True,
                        reason="swr_triggered",
                    )
                )
                continue

            tasks.append(
                asyncio.create_task(
                    self._run_collector_with_gate(c, registry, gate=gate, timeout=timeout_seconds),
                    name=collector_name,
                )
            )

        if not tasks:
            return results

        remaining = max(0.0, deadline_seconds - (time.monotonic() - start))
        done, pending = await asyncio.wait(tasks, timeout=remaining)
        for t in done:
            results.append(t.result())
        for t in pending:
            # Deadline means we stop waiting for refresh; we do NOT cancel because collectors run
            # in a thread and cancellation would not stop that work anyway. Keeping the task alive
            # avoids leaving gates stuck in inflight state while also preventing overlapping
            # refresh attempts across scrapes.
            self._track_background(t)
            results.append(
                RefreshResult(
                    collector=t.get_name() or "unknown",
                    attempted=True,
                    success=False,
                    reason="deadline_exceeded",
                )
            )
        return results

    def _track_background(self, task: asyncio.Task[RefreshResult]) -> None:
        """
        Track a background refresh task until it completes.

        The task set exists only to keep background SWR refreshes strongly referenced and to
        allow their lifecycle to be cleaned up automatically on completion.
        """
        self._background_tasks.add(task)

        def _done(t: asyncio.Task[RefreshResult]) -> None:
            """Remove a completed background task from the tracked task set."""
            self._background_tasks.discard(t)

        task.add_done_callback(_done)

    async def _run_collector_with_gate(
        self, collector: MetricCollector, registry, *, gate: RefreshGate, timeout: float
    ) -> RefreshResult:
        """
        Run one collector while ensuring the associated refresh gate is completed correctly.

        The gate is marked complete on both success and failure so future refresh decisions do
        not remain stuck in the inflight state.
        """
        success = False
        try:
            res = await self._run_collector(collector, registry, timeout=timeout)
            success = bool(res.success)
            return res
        finally:
            gate.mark_complete(success=success)

    async def _run_collector(
        self, collector: MetricCollector, registry, *, timeout: float
    ) -> RefreshResult:
        """
        Execute one collector's `collect(...)` call under a timeout in a worker thread.

        Returns:
            A `RefreshResult` describing the collector outcome without propagating exceptions to
            the caller.
        """
        try:
            await asyncio.wait_for(asyncio.to_thread(collector.collect, registry), timeout=timeout)
            return RefreshResult(
                collector=self._collector_name(collector),
                attempted=True,
                success=True,
                reason="ok",
            )
        except asyncio.TimeoutError:
            return RefreshResult(
                collector=self._collector_name(collector),
                attempted=True,
                success=False,
                reason="timeout",
            )
        except Exception:
            return RefreshResult(
                collector=self._collector_name(collector),
                attempted=True,
                success=False,
                reason="exception",
            )

    def _collector_name(self, collector: MetricCollector) -> str:
        """
        Resolve the display name used for refresh results and refresh-gate bookkeeping.

        The manager prefers the collector's explicit `collector_name()` method and falls back to
        a `name` attribute or the concrete class name for compatibility.
        """
        try:
            return str(collector.collector_name())
        except AttributeError:
            name = getattr(collector, "name", None)
            if name is not None:
                return str(name)
            return collector.__class__.__name__
