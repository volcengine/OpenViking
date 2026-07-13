# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Event collector: SessionCollector.

Tracks session lifecycle and usage signals emitted from session-related code paths:
- create/get/delete/commit/extract lifecycle outcomes
- contexts and skills usage counts
- archive outcome (ok/skip)

Labels are bounded:
- action/status are small enums controlled by the codebase.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.session import FencedBacklogDataSource

from .base import CollectorConfig, EventMetricCollector, StateMetricCollector


@dataclass
class SessionCollector(EventMetricCollector):
    """
    Translate session lifecycle and usage events into bounded session counters.

    The collector receives coarse-grained events from session management code paths and records
    only stable labels such as action and status so the exported series remain suitable for
    long-term dashboarding.
    """

    DOMAIN: ClassVar[str] = "session"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_lifecycle_total
    # e.g.: openviking_session_lifecycle_total
    LIFECYCLE_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "lifecycle", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_contexts_used_total
    # e.g.: openviking_session_contexts_used_total
    CONTEXTS_USED_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "contexts_used", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_archive_total
    # e.g.: openviking_session_archive_total
    ARCHIVE_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "archive", unit="total")
    FENCING_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "fencing_writes", unit="total"
    )
    FENCING_LATENCY_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "fencing_write_latency", unit="seconds"
    )
    FENCING_LAST_EVENT_TIMESTAMP_SECONDS: ClassVar[str] = (
        MetricCollector.metric_name(
            DOMAIN, "fencing_last_event_timestamp", unit="seconds"
        )
    )
    FENCED_EFFECTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        "fenced", "effects", unit="total"
    )
    FENCED_EFFECT_LAST_EVENT_TIMESTAMP_SECONDS: ClassVar[str] = (
        MetricCollector.metric_name(
            "fenced", "effect_last_event_timestamp", unit="seconds"
        )
    )

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset(
        {
            "session.lifecycle",
            "session.contexts_used",
            "session.archive",
            "session.fencing",
            "session.fenced_effect",
        }
    )

    def collect(self, registry=None) -> None:
        """Implement the collector interface as a no-op because session metrics are push-driven."""
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """
        Dispatch one normalized session event to the matching counter update helper.

        Each branch handles a bounded event family emitted from session services and adapters.
        """
        if event_name == "session.lifecycle":
            action = payload.get("action")
            status = payload.get("status")
            if action is None or status is None:
                return
            self.record_lifecycle(
                registry,
                action=str(action),
                status=str(status),
            )
            return
        if event_name == "session.contexts_used":
            action = payload.get("action")
            delta = payload.get("delta")
            if action is None or delta is None:
                return
            self.record_contexts_used(
                registry,
                action=str(action),
                delta=int(delta),
            )
            return
        if event_name == "session.archive":
            status = payload.get("status")
            if status is None:
                return
            self.record_archive(
                registry,
                status=str(status),
            )
            return
        if event_name == "session.fencing":
            operation = payload.get("operation")
            outcome = payload.get("outcome")
            latency_seconds = payload.get("latency_seconds")
            if operation is None or outcome is None or latency_seconds is None:
                return
            self.record_fencing(
                registry,
                operation=str(operation),
                outcome=str(outcome),
                latency_seconds=float(latency_seconds),
            )
            return
        if event_name == "session.fenced_effect":
            operation = payload.get("operation")
            outcome = payload.get("outcome")
            if operation is None or outcome is None:
                return
            registry.inc_counter(
                self.FENCED_EFFECTS_TOTAL,
                labels={
                    "operation": str(operation),
                    "outcome": str(outcome),
                },
                label_names=("operation", "outcome"),
            )
            registry.set_gauge(
                self.FENCED_EFFECT_LAST_EVENT_TIMESTAMP_SECONDS,
                time.time(),
                labels={
                    "operation": str(operation),
                    "outcome": str(outcome),
                },
                label_names=("operation", "outcome"),
            )

    def record_lifecycle(self, registry, *, action: str, status: str) -> None:
        """Increment the lifecycle counter for one session action/status pair."""
        registry.inc_counter(
            self.LIFECYCLE_TOTAL,
            labels={"action": str(action), "status": str(status)},
            label_names=("action", "status"),
        )

    def record_contexts_used(self, registry, *, action: str, delta: int) -> None:
        """Increase the contexts-used counter by the positive number of contexts consumed."""
        if delta <= 0:
            return
        registry.inc_counter(
            self.CONTEXTS_USED_TOTAL,
            labels={"action": str(action)},
            label_names=("action",),
            amount=int(delta),
        )

    def record_archive(self, registry, *, status: str) -> None:
        """Increment the archive outcome counter for one session-archive attempt."""
        registry.inc_counter(
            self.ARCHIVE_TOTAL,
            labels={"status": str(status)},
            label_names=("status",),
        )

    def record_fencing(
        self,
        registry,
        *,
        operation: str,
        outcome: str,
        latency_seconds: float,
    ) -> None:
        """Record fenced writes, stale/duplicate suppression, and latency."""
        labels = {"operation": str(operation), "outcome": str(outcome)}
        registry.inc_counter(
            self.FENCING_TOTAL,
            labels=labels,
            label_names=("operation", "outcome"),
        )
        registry.observe_histogram(
            self.FENCING_LATENCY_SECONDS,
            max(0.0, float(latency_seconds)),
            labels=labels,
            label_names=("operation", "outcome"),
        )
        registry.set_gauge(
            self.FENCING_LAST_EVENT_TIMESTAMP_SECONDS,
            time.time(),
            labels=labels,
            label_names=("operation", "outcome"),
        )


@dataclass
class FencedBacklogCollector(StateMetricCollector):
    """Export low-cardinality PostgreSQL outbox and writer-pool state."""

    WRITER_POOL_HEALTHY: ClassVar[str] = MetricCollector.metric_name(
        "fenced", "writer_pool_healthy"
    )
    WRITER_CONCURRENCY: ClassVar[str] = MetricCollector.metric_name(
        "fenced", "writer_concurrency"
    )
    EFFECT_ITEMS: ClassVar[str] = MetricCollector.metric_name(
        "fenced", "effect_outbox_items"
    )
    EFFECT_OLDEST: ClassVar[str] = MetricCollector.metric_name(
        "fenced", "effect_outbox_oldest_age", unit="seconds"
    )
    COMMIT_ITEMS: ClassVar[str] = MetricCollector.metric_name(
        "fenced", "commit_work_items"
    )
    COMMIT_OLDEST: ClassVar[str] = MetricCollector.metric_name(
        "fenced", "commit_work_oldest_age", unit="seconds"
    )
    COLLECTION_ERRORS: ClassVar[str] = MetricCollector.metric_name(
        "fenced", "backlog_collection_errors", unit="total"
    )
    COLLECTION_LAST_ERROR_TIMESTAMP_SECONDS: ClassVar[str] = (
        MetricCollector.metric_name(
            "fenced",
            "backlog_collection_last_error_timestamp",
            unit="seconds",
        )
    )

    data_source: FencedBacklogDataSource
    config: CollectorConfig = CollectorConfig(
        ttl_seconds=1.0,
        timeout_seconds=1.0,
    )

    def read_metric_input(self):
        return self.data_source.read_backlog()

    def collect_hook(self, registry, metric_input) -> None:
        registry.set_gauge(
            self.WRITER_POOL_HEALTHY,
            1.0 if metric_input["writer_healthy"] else 0.0,
        )
        for kind in ("effect", "commit"):
            registry.set_gauge(
                self.WRITER_CONCURRENCY,
                float(metric_input[f"{kind}_concurrency"]),
                labels={"kind": kind},
                label_names=("kind",),
            )
        for state in ("queued", "running"):
            snapshot = metric_input["effect"][state]
            labels = {"state": state}
            registry.set_gauge(
                self.EFFECT_ITEMS,
                float(snapshot["items"]),
                labels=labels,
                label_names=("state",),
            )
            registry.set_gauge(
                self.EFFECT_OLDEST,
                float(snapshot["oldest_age_seconds"]),
                labels=labels,
                label_names=("state",),
            )
        for state in ("pending", "running", "ambiguous"):
            snapshot = metric_input["commit"][state]
            labels = {"state": state}
            registry.set_gauge(
                self.COMMIT_ITEMS,
                float(snapshot["items"]),
                labels=labels,
                label_names=("state",),
            )
            registry.set_gauge(
                self.COMMIT_OLDEST,
                float(snapshot["oldest_age_seconds"]),
                labels=labels,
                label_names=("state",),
            )

    def collect_error_hook(self, registry, error: Exception) -> None:
        del error
        registry.inc_counter(self.COLLECTION_ERRORS)
        registry.set_gauge(
            self.COLLECTION_LAST_ERROR_TIMESTAMP_SECONDS,
            time.time(),
        )
        registry.set_gauge(self.WRITER_POOL_HEALTHY, 0.0)
