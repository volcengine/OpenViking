# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Process-local observability event bus.

The bus is intentionally small and best-effort. It lets one emitted event feed
multiple side-channel consumers, such as Prometheus collectors and product
usage/audit projections, without coupling business code to either consumer.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from openviking.observability.context import get_observability_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ObservabilityEvent:
    """One normalized in-process observability event."""

    event_name: str
    payload: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: str | None = None
    account_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None


class ObservabilitySubscriber(Protocol):
    """Callable protocol for event bus subscribers."""

    def __call__(self, event: ObservabilityEvent) -> None:
        """Consume one best-effort observability event."""


class ObservabilityEventBus:
    """Thread-safe fan-out dispatcher for process-local observability events."""

    def __init__(self) -> None:
        self._subscribers: dict[str, ObservabilitySubscriber] = {}
        self._lock = threading.RLock()

    def register(self, name: str, subscriber: ObservabilitySubscriber) -> None:
        """Register or replace one named subscriber."""
        key = str(name)
        with self._lock:
            self._subscribers[key] = subscriber

    def unregister(self, name: str) -> None:
        """Remove one named subscriber if present."""
        key = str(name)
        with self._lock:
            self._subscribers.pop(key, None)

    def publish(self, event: ObservabilityEvent) -> None:
        """Deliver an event to every subscriber without raising to callers."""
        with self._lock:
            subscribers = list(self._subscribers.items())

        for name, subscriber in subscribers:
            try:
                subscriber(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "observability subscriber %s failed for event %s: %s",
                    name,
                    event.event_name,
                    exc,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )

    def clear(self) -> None:
        """Remove all subscribers. Intended for tests and process shutdown."""
        with self._lock:
            self._subscribers.clear()


_GLOBAL_EVENT_BUS = ObservabilityEventBus()


def get_event_bus() -> ObservabilityEventBus:
    """Return the process-global observability event bus."""
    return _GLOBAL_EVENT_BUS


def register_event_subscriber(name: str, subscriber: ObservabilitySubscriber) -> None:
    """Register a process-global event subscriber."""
    _GLOBAL_EVENT_BUS.register(name, subscriber)


def unregister_event_subscriber(name: str) -> None:
    """Unregister a process-global event subscriber."""
    _GLOBAL_EVENT_BUS.unregister(name)


def reset_event_bus_for_tests() -> None:
    """Clear all subscribers from the process-global bus."""
    _GLOBAL_EVENT_BUS.clear()


def _metadata_from_context(payload: dict[str, Any]) -> dict[str, str | None]:
    """Build event metadata from payload first, then current root context."""
    root = get_observability_context().root

    def _first(*values: Any) -> str | None:
        for value in values:
            if value is not None:
                return str(value)
        return None

    return {
        "request_id": _first(payload.get("request_id"), getattr(root, "request_id", None)),
        "account_id": _first(payload.get("account_id"), getattr(root, "account_id", None)),
        "user_id": _first(payload.get("user_id"), getattr(root, "user_id", None)),
        "agent_id": _first(payload.get("agent_id"), getattr(root, "agent_id", None)),
    }


def build_event(event_name: str, payload: dict[str, Any] | None = None) -> ObservabilityEvent:
    """Create an enriched event envelope from a raw event name and payload."""
    normalized_payload = dict(payload or {})
    metadata = _metadata_from_context(normalized_payload)
    return ObservabilityEvent(
        event_name=str(event_name),
        payload=normalized_payload,
        request_id=metadata["request_id"],
        account_id=metadata["account_id"],
        user_id=metadata["user_id"],
        agent_id=metadata["agent_id"],
    )


def try_publish_event(event_name: str, payload: dict[str, Any] | None = None) -> None:
    """Best-effort publish API used by observability data sources."""
    try:
        _GLOBAL_EVENT_BUS.publish(build_event(event_name, payload))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "observability event publish failed for %s: %s",
            event_name,
            exc,
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )


def make_payload_subscriber(
    handler: Callable[[str, dict[str, Any]], None],
) -> ObservabilitySubscriber:
    """Adapt a legacy `(event_name, payload)` handler to an event subscriber."""

    def _subscriber(event: ObservabilityEvent) -> None:
        payload = dict(event.payload)
        for key in ("request_id", "account_id", "user_id", "agent_id"):
            if payload.get(key) is None:
                value = getattr(event, key)
                if value is not None:
                    payload[key] = value
        handler(event.event_name, payload)

    return _subscriber
