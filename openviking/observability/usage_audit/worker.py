# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Background worker for Usage/Audit event persistence."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from openviking.observability.events import ObservabilityEvent

from .store import UsageAuditStore

logger = logging.getLogger(__name__)


class UsageAuditWorker:
    """Best-effort async batch writer for usage/audit events."""

    def __init__(
        self,
        store: UsageAuditStore,
        *,
        queue_size: int = 10_000,
        batch_size: int = 500,
        flush_interval_seconds: float = 1.0,
    ) -> None:
        self._store = store
        self._queue_size = max(int(queue_size), 1)
        self._batch_size = max(int(batch_size), 1)
        self._flush_interval_seconds = max(float(flush_interval_seconds), 0.1)
        self._queue: asyncio.Queue[ObservabilityEvent] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._current_batch: list[ObservabilityEvent] | None = None
        self._closed = False
        self.dropped_count = 0

    async def start(self) -> None:
        """Start the background flush loop."""
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self._queue_size)
        self._closed = False
        self._task = asyncio.create_task(self._run(), name="openviking-usage-audit-worker")

    def enqueue(self, event: ObservabilityEvent) -> None:
        """Try to enqueue one event without blocking the caller."""
        if self._closed or self._queue is None or self._loop is None:
            self.dropped_count += 1
            return

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is self._loop:
            self._enqueue_nowait(event)
            return

        self._loop.call_soon_threadsafe(self._enqueue_nowait, event)

    def _enqueue_nowait(self, event: ObservabilityEvent) -> None:
        queue = self._queue
        if self._closed or queue is None:
            self.dropped_count += 1
            return
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped_count += 1

    async def _run(self) -> None:
        while not self._closed:
            batch: list[ObservabilityEvent] = []
            try:
                assert self._queue is not None
                event = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=self._flush_interval_seconds,
                )
                batch.append(event)
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Usage/Audit worker loop error: %s", exc)
                continue

            self._current_batch = batch
            try:
                await self._flush(batch)
            except asyncio.CancelledError:
                logger.warning(
                    "Usage/Audit worker cancelled with %d in-flight events",
                    len(batch),
                )
                raise
            else:
                self._current_batch = None

    async def _flush(self, batch: list[ObservabilityEvent]) -> None:
        if not batch:
            return
        try:
            await self._store.record_batch(batch)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Usage/Audit batch flush failed: %s",
                exc,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )

    async def close(self, *, timeout_seconds: float = 3.0) -> None:
        """Stop the worker and flush remaining queued events."""
        self._closed = True
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                if self._current_batch:
                    logger.warning(
                        "Usage/Audit shutdown waiting for %d in-flight events",
                        len(self._current_batch),
                    )
                    await task
                else:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            self._task = None

        queue = self._queue
        if queue is None:
            return

        remaining: list[ObservabilityEvent] = []
        while True:
            try:
                remaining.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if remaining:
            try:
                await asyncio.wait_for(self._flush(remaining), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                logger.warning("Usage/Audit shutdown flush timed out")
