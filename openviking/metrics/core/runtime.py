# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Runtime utilities for the metrics subsystem.

Currently this module defines a small in-process event router:
- DataSources emit events by calling `openviking.metrics.global_api.try_dispatch_event(...)`.
- The global API routes events to the matching Collector handler.

This avoids wiring MetricRegistry into business code and keeps the write-path
restricted to Collector implementations.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


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
