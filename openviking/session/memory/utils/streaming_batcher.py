# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared async count/time window batcher for streaming session updates."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

T = TypeVar("T")
R = TypeVar("R")


@dataclass(slots=True)
class StreamingBatcherConfig:
    """Count/time window configuration shared by streaming updaters."""

    max_items_per_batch: int = 8
    max_wait_seconds: float = 10.0
    timer_check_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_items_per_batch <= 0:
            raise ValueError("max_items_per_batch must be > 0")
        if self.max_wait_seconds <= 0:
            raise ValueError("max_wait_seconds must be > 0")
        if self.timer_check_interval_seconds <= 0:
            raise ValueError("timer_check_interval_seconds must be > 0")


@dataclass(slots=True)
class StreamingBatcher(Generic[T, R]):
    """A reusable async batcher whose submit waits for its batch result.

    Items are buffered until either the buffered size reaches
    ``max_items_per_batch`` or the oldest item waits for ``max_wait_seconds``.
    Flush is performed by background tasks/timer; each ``submit`` awaits the
    Future attached to its own batch item, so callers only return after the
    batch containing their item has been processed.
    """

    name: str
    process_batch: Callable[[list[T], str], Awaitable[R]]
    config: StreamingBatcherConfig = field(default_factory=StreamingBatcherConfig)
    item_size: Callable[[T], int] | None = None
    result_metadata: Callable[[R], dict[str, Any] | None] | None = None
    _buffer: list[_PendingBatchItem[T, R]] = field(init=False, repr=False)
    _buffer_lock: asyncio.Lock = field(init=False, repr=False)
    _flush_lock: asyncio.Lock = field(init=False, repr=False)
    _timer_task: asyncio.Task[None] | None = field(init=False, default=None, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)
    _last_result: R | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self._buffer = []
        self._buffer_lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()
        self._timer_task = None
        self._closed = False
        self._last_result = None

    @property
    def closed(self) -> bool:
        return self._closed

    async def get_buffered_size(self) -> int:
        async with self._buffer_lock:
            return sum(self._item_size(item.payload) for item in self._buffer)

    async def get_buffered_item_count(self) -> int:
        async with self._buffer_lock:
            return len(self._buffer)

    async def submit(self, payload: T) -> R:
        if self._closed:
            raise RuntimeError(f"{self.name} is closed")

        self._ensure_timer_task()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[R] = loop.create_future()
        should_flush = False
        async with self._buffer_lock:
            self._buffer.append(
                _PendingBatchItem(
                    payload=payload,
                    submitted_at=time.monotonic(),
                    future=future,
                )
            )
            should_flush = self._buffered_size_unlocked() >= self.config.max_items_per_batch

        if should_flush:
            self._trigger_background_flush("count")

        return await future

    async def close(self) -> R | None:
        if self._closed:
            return None
        self._closed = True
        await self._stop_timer_task()
        return await self.flush("close")

    async def flush(self, reason: str) -> R | None:
        async with self._flush_lock:
            async with self._buffer_lock:
                if not self._buffer:
                    return None
                items = self._buffer
                self._buffer = []

            batch_id = uuid.uuid4().hex
            batch_trace_id = uuid.uuid4().hex
            try:
                with tracer.start_as_current_span(
                    name=f"{self.name}.flush",
                    trace_id=batch_trace_id,
                ):
                    tracer.set("batch_id", batch_id)
                    tracer.set("flush_reason", reason)
                    tracer.set("request_count", len(items))
                    tracer.set("input_size", sum(self._item_size(item.payload) for item in items))
                    result = await self.process_batch([item.payload for item in items], reason)
                    metadata = self._get_result_metadata(result)
                    if metadata is not None:
                        metadata.setdefault("batch_id", batch_id)
                        metadata.setdefault("batch_trace_id", batch_trace_id)
            except Exception as exc:
                for item in items:
                    if not item.future.done():
                        item.future.set_exception(exc)
                raise

            self._last_result = result
            for item in items:
                if not item.future.done():
                    item.future.set_result(result)
            return result

    def _ensure_timer_task(self) -> None:
        if self._timer_task is not None and not self._timer_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[%s] timer loop not started: no running event loop", self.name)
            self._timer_task = None
            return
        self._timer_task = loop.create_task(
            self._run_timer_loop(),
            name=f"{self.name}-flush-loop",
        )

    async def _stop_timer_task(self) -> None:
        task = self._timer_task
        if task is None:
            return
        self._timer_task = None
        if task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run_timer_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.config.timer_check_interval_seconds)
                if await self._should_flush_by_time_or_count():
                    await self.flush("time")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[%s] timer flush iteration failed: %s", self.name, exc)

    async def _should_flush_by_time_or_count(self) -> bool:
        async with self._buffer_lock:
            if not self._buffer:
                return False
            if self._buffered_size_unlocked() >= self.config.max_items_per_batch:
                return True
            oldest = min(item.submitted_at for item in self._buffer)
            return (time.monotonic() - oldest) >= self.config.max_wait_seconds

    def _trigger_background_flush(self, reason: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _runner() -> None:
            try:
                await self.flush(reason)
            except Exception as exc:
                logger.warning("[%s] background flush failed: %s", self.name, exc)

        loop.create_task(_runner(), name=f"{self.name}-flush-{reason}")

    def _buffered_size_unlocked(self) -> int:
        return sum(self._item_size(item.payload) for item in self._buffer)

    def _item_size(self, payload: T) -> int:
        if self.item_size is None:
            return 1
        return max(0, int(self.item_size(payload)))

    def _get_result_metadata(self, result: R) -> dict[str, Any] | None:
        if self.result_metadata is not None:
            return self.result_metadata(result)
        metadata = getattr(result, "metadata", None)
        return metadata if isinstance(metadata, dict) else None


@dataclass(slots=True)
class _PendingBatchItem(Generic[T, R]):
    payload: T
    submitted_at: float
    future: asyncio.Future[R]
