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


class ObservabilitySubscriber(Protocol):
    """Callable protocol for event bus subscribers."""

    def __call__(self, event: ObservabilityEvent) -> None:
        """Consume one best-effort observability event."""


class ObservabilityEventBus:
    """Thread-safe fan-out dispatcher for process-local observability events."""

    def __init__(self) -> None:
        self._subscribers: dict[str, ObservabilitySubscriber] = {}
        self._lock = threading.RLock()
        self._delivery_failure_reported = False

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

    def has_subscriber(self, name: str) -> bool:
        with self._lock:
            return name in self._subscribers

    def publish(self, event: ObservabilityEvent) -> tuple[str, ...]:
        """Deliver an event to every subscriber without raising to callers."""
        with self._lock:
            subscribers = list(self._subscribers.items())

        failed = []
        for name, subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:  # noqa: BLE001
                failed.append(name)
        if failed and not event.event_name.startswith("audit."):
            with self._lock:
                level = logging.INFO if self._delivery_failure_reported else logging.ERROR
                self._delivery_failure_reported = True
            logger.log(
                level,
                "observability delivery incomplete total=%d failed=%d degraded=true",
                len(subscribers),
                len(failed),
                extra={"total": len(subscribers), "failed": len(failed), "degraded": True},
            )
        return tuple(failed)

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
    }


def build_event(event_name: str, payload: dict[str, Any] | None = None) -> ObservabilityEvent:
    """Create an enriched event envelope from a raw event name and payload."""
    normalized_payload = dict(payload or {})
    from openviking.observability.reporting.runtime import is_reporting_enabled

    if is_reporting_enabled() and not normalized_payload.get("event_id"):
        from uuid import uuid4

        normalized_payload["event_id"] = "evt_" + uuid4().hex
    from openviking.observability.reporting.facts import event_context

    for key, value in event_context(str(event_name)).items():
        if normalized_payload.get(key) is None:
            normalized_payload[key] = value
    if event_name == "http.request":
        tasks = get_observability_context().extra.get("metric_tasks", {})
        if tasks:
            normalized_payload["metric_tasks"] = list(tasks.values())
    metadata = _metadata_from_context(normalized_payload)
    return ObservabilityEvent(
        event_name=str(event_name),
        payload=normalized_payload,
        request_id=metadata["request_id"],
        account_id=metadata["account_id"],
        user_id=metadata["user_id"],
    )


def try_publish_event(event_name: str, payload: dict[str, Any] | None = None) -> None:
    """Best-effort publish API used by observability data sources."""
    try:
        _GLOBAL_EVENT_BUS.publish(build_event(event_name, payload))
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "observability publish unavailable error_type=%s degraded=true",
            type(exc).__name__,
            extra={"degraded": True},
        )


def make_payload_subscriber(
    handler: Callable[[str, dict[str, Any]], None],
) -> ObservabilitySubscriber:
    """Adapt a legacy `(event_name, payload)` handler to an event subscriber."""

    def _subscriber(event: ObservabilityEvent) -> None:
        payload = dict(event.payload)
        for key in ("request_id", "account_id", "user_id"):
            if payload.get(key) is None:
                value = getattr(event, key)
                if value is not None:
                    payload[key] = value
        handler(event.event_name, payload)

    return _subscriber


def log_audit(
    *,
    event: str,
    result: str,
    actor_id: str | None,
    actor_role: str | None,
    actor_type: str | None,
    account_id: str | None,
    target_type: str,
    target_id: str | None,
    error_code: str | None,
    **http_fields: Any,
) -> None:
    """Emit a common WARNING copy and an event whose persistence failures are reported.

    This does not acknowledge durable storage: the usage/audit worker reports
    asynchronous enqueue and storage failures at their actual boundary.
    """
    from openviking.observability.log_safety import validate_audit_record
    from openviking_cli.utils.logger import _get_log_context, get_logger

    audit_logger = get_logger("openviking.audit")
    timestamp = datetime.now(timezone.utc)
    context = _get_log_context()
    try:
        payload = validate_audit_record(
            {
                **http_fields,
                "ts": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "event": event,
                "action": event.rsplit(".", 1)[-1],
                "result": result,
                "actor_id": actor_id,
                "actor_role": actor_role,
                "actor_type": actor_type,
                "account_id": account_id,
                "target_type": target_type,
                "target_id": target_id,
                "request_id": context.get("request_id") or None,
                "trace_id": context.get("trace_id") or None,
                "source_ip": http_fields.get("source_ip"),
                "user_agent": http_fields.get("user_agent"),
                "error_code": error_code,
            }
        )
    except (ValueError, TypeError) as exc:
        audit_logger.error(
            "audit record rejected error_type=%s error_code=AUDIT_INVALID_RECORD",
            type(exc).__name__,
            extra={"error_code": "AUDIT_INVALID_RECORD"},
        )
        return
    record = audit_logger.makeRecord(
        audit_logger.name, logging.WARNING, __file__, 0, "audit event", (), None, extra=payload
    )
    record.created = timestamp.timestamp()
    audit_logger.handle(record)
    envelope = ObservabilityEvent(
        event_name=event,
        payload=payload,
        timestamp=timestamp,
        request_id=payload["request_id"],
        account_id=account_id,
        user_id=actor_id,
    )
    if not _GLOBAL_EVENT_BUS.has_subscriber("usage_audit"):
        audit_logger.error(
            "audit persistence unavailable error_code=AUDIT_PERSISTENCE_UNAVAILABLE",
            extra={"error_code": "AUDIT_PERSISTENCE_UNAVAILABLE"},
        )
        return
    if _GLOBAL_EVENT_BUS.publish(envelope):
        audit_logger.error(
            "audit publish failed error_code=AUDIT_PUBLISH_FAILED",
            extra={"error_code": "AUDIT_PUBLISH_FAILED"},
        )
