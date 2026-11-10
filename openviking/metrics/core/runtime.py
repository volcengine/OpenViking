# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Runtime utilities for the metrics subsystem.

Currently this module defines shared in-process metrics runtime helpers:
- Default executor instrumentation used by async-system metrics.
- DataSources publish events to the shared observability event bus.
- Metrics subscribes with this router and routes each event to the matching Collector handler.

This avoids wiring MetricRegistry into business code and keeps the write-path
restricted to Collector implementations.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import threading
from collections.abc import Callable
from typing import Any

DEFAULT_EXECUTOR_POOL = "asyncio_default"
DEFAULT_PROCESS_ROLE = "legacy_server"


class DefaultExecutorMonitor:
    """Monitor tasks submitted to one event loop's default executor."""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        process_role: str = DEFAULT_PROCESS_ROLE,
    ) -> None:
        """Create a monitor for a single event loop without installing it."""
        self._loop = loop
        self._process_role = str(process_role)
        self._lock = threading.Lock()
        self._original_run_in_executor: Callable[..., Any] | None = None
        self._active_tasks = 0
        self._pending_tasks = 0
        self._submitted_total = 0
        self._completed_total = 0
        self._failed_total = 0

    def install(self) -> "DefaultExecutorMonitor":
        """Patch the event loop's default executor entrypoint and return this monitor."""
        if self._original_run_in_executor is not None:
            return self
        original_run_in_executor = self._loop.run_in_executor
        self._original_run_in_executor = original_run_in_executor

        def _run_in_executor(executor, func, *args):
            """Wrap default executor submissions and leave custom executors untouched."""
            if executor is not None:
                return original_run_in_executor(executor, func, *args)
            return self._submit_default_executor(func, *args)

        self._loop.run_in_executor = _run_in_executor  # type: ignore[method-assign]
        return self

    def uninstall(self) -> None:
        """Restore the original event loop entrypoint when this monitor installed it."""
        global _current_executor_monitor
        if self._original_run_in_executor is not None:
            self._loop.run_in_executor = self._original_run_in_executor  # type: ignore[method-assign]
        self._original_run_in_executor = None
        with _executor_monitor_lock:
            if _current_executor_monitor is self:
                _current_executor_monitor = None

    def read_metrics(self) -> dict[str, int | str]:
        """Return the latest default executor counters and thread state."""
        executor = getattr(self._loop, "_default_executor", None)
        max_workers = int(getattr(executor, "_max_workers", 0) or 0)
        threads = len(getattr(executor, "_threads", ()) or ())
        with self._lock:
            return {
                "process_role": self._process_role,
                "worker": multiprocessing.current_process().name,
                "pool": DEFAULT_EXECUTOR_POOL,
                "max_workers": max_workers,
                "threads": threads,
                "active_tasks": self._active_tasks,
                "pending_tasks": self._pending_tasks,
                "submitted_total": self._submitted_total,
                "completed_total": self._completed_total,
                "failed_total": self._failed_total,
            }

    def _submit_default_executor(self, func, *args):
        """Submit one wrapped call to the original default executor and update counters."""
        run_in_executor = self._original_run_in_executor
        if run_in_executor is None:
            raise RuntimeError("executor monitor is not installed")
        started = False
        finished = False
        with self._lock:
            self._submitted_total += 1
            self._pending_tasks += 1

        def _wrapped():
            """Run the original callable while tracking pending, active, and final states."""
            nonlocal started, finished
            with self._lock:
                started = True
                self._pending_tasks = max(0, self._pending_tasks - 1)
                self._active_tasks += 1
            try:
                return func(*args)
            except Exception:
                with self._lock:
                    self._failed_total += 1
                raise
            finally:
                with self._lock:
                    finished = True
                    self._active_tasks = max(0, self._active_tasks - 1)
                    self._completed_total += 1

        try:
            future = run_in_executor(None, _wrapped)
        except Exception:
            with self._lock:
                self._pending_tasks = max(0, self._pending_tasks - 1)
                self._failed_total += 1
            raise

        def _on_done(done_future) -> None:
            """Drop pending state when cancellation prevents a queued call from starting."""
            nonlocal finished
            if not done_future.cancelled():
                return
            with self._lock:
                if started or finished:
                    return
                finished = True
                self._pending_tasks = max(0, self._pending_tasks - 1)

        future.add_done_callback(_on_done)
        return future


_executor_monitor_lock = threading.RLock()
_current_executor_monitor: DefaultExecutorMonitor | None = None


def install_executor_monitor(
    *,
    loop: asyncio.AbstractEventLoop | None = None,
    process_role: str = DEFAULT_PROCESS_ROLE,
) -> DefaultExecutorMonitor:
    """Install the process-wide default executor monitor on the selected event loop."""
    global _current_executor_monitor
    target_loop = loop or asyncio.get_running_loop()
    with _executor_monitor_lock:
        if _current_executor_monitor is not None:
            _current_executor_monitor.uninstall()
        monitor = DefaultExecutorMonitor(
            loop=target_loop,
            process_role=process_role,
        ).install()
        _current_executor_monitor = monitor
    return monitor


def uninstall_executor_monitor() -> None:
    """Uninstall the current process-wide default executor monitor if one exists."""
    with _executor_monitor_lock:
        if _current_executor_monitor is not None:
            _current_executor_monitor.uninstall()


def get_executor_monitor() -> DefaultExecutorMonitor | None:
    """Return the current process-wide default executor monitor, if installed."""
    with _executor_monitor_lock:
        return _current_executor_monitor


def empty_executor_metrics() -> dict[str, int | str]:
    """Return a zero-valued default executor metric payload."""
    return {
        "process_role": DEFAULT_PROCESS_ROLE,
        "worker": multiprocessing.current_process().name,
        "pool": DEFAULT_EXECUTOR_POOL,
        "max_workers": 0,
        "threads": 0,
        "active_tasks": 0,
        "pending_tasks": 0,
        "submitted_total": 0,
        "completed_total": 0,
        "failed_total": 0,
    }


class EventCollectorRouter:
    """
    A minimal in-process dispatcher for event-driven metrics collection.

    The router is intentionally small: it only maps one event name to one handler and invokes
    that handler when the event is dispatched. This keeps event-driven metrics lightweight and
    preserves the architectural rule that collectors, not business code, translate events into
    registry writes.
    """

    def __init__(self) -> None:
        """
        Initialize an empty event-to-handler mapping.

        Handlers are keyed by the normalized string form of the event name and are expected to
        accept a single dictionary payload.
        """
        self._handlers: dict[str, Callable[[dict[str, Any]], None]] = {}

    def register(self, event_name: str, handler: Callable[[dict[str, Any]], None]) -> None:
        """
        Register or replace the handler for a specific metrics event.

        Args:
            event_name: Logical event name emitted by a DataSource or bridge layer.
            handler: Callable that receives the event payload and performs the corresponding
                collector-side metric translation.
        """
        self._handlers[str(event_name)] = handler

    def dispatch(self, event_name: str, payload: dict[str, Any]) -> None:
        """
        Deliver an event payload to the registered handler, if one exists.

        Args:
            event_name: Logical event name to dispatch.
            payload: Event payload already normalized into a dictionary.

        If no handler is registered, the event is ignored silently. This is intentional because
        metrics are a side-channel and must not affect the correctness of the business flow.
        """
        handler = self._handlers.get(str(event_name))
        if handler is None:
            return
        handler(payload)
