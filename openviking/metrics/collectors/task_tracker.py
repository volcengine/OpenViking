# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.task import TaskStateDataSource
from openviking.observability.errors import normalize_metric_error_code

from .base import CollectorConfig, EventMetricCollector, StateMetricCollector


@dataclass
class TaskTrackerCollector(StateMetricCollector):
    """
    Export task tracker backlog and completion counts grouped by task type.

    The datasource returns a snapshot of task counts, so all families are exposed as gauges
    keyed only by `task_type` rather than as cumulative counters.
    """

    DOMAIN: ClassVar[str] = "task"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_pending
    # e.g.: openviking_task_pending
    PENDING: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "pending")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_running
    # e.g.: openviking_task_running
    RUNNING: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "running")
    CANCELLING: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "cancelling")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_completed
    # e.g.: openviking_task_completed
    COMPLETED: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "completed")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_failed
    # e.g.: openviking_task_failed
    FAILED: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "failed")
    CANCELLED: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "cancelled")

    data_source: TaskStateDataSource
    config: CollectorConfig = CollectorConfig(timeout_seconds=0.5)

    def read_metric_input(self):
        """Read the latest task-count snapshot grouped by task type from the datasource."""
        return self.data_source.read_task_counts()

    def collect_hook(self, registry, metric_input) -> None:
        """Translate the task-count snapshot into lifecycle-state gauges."""
        counts_by_type = metric_input
        # Snapshot semantics: types can disappear entirely, so clear previous series to avoid
        # exporting stale non-zero gauges forever.
        for metric_name in (
            self.PENDING,
            self.RUNNING,
            self.CANCELLING,
            self.COMPLETED,
            self.FAILED,
            self.CANCELLED,
        ):
            registry.delete_matching(metric_name, match_labels={})
        for task_type, counts in counts_by_type.items():
            labels = {"task_type": str(task_type)}
            registry.set_gauge(
                self.PENDING,
                float(counts.get("pending", 0)),
                labels=labels,
                label_names=("task_type",),
            )
            registry.set_gauge(
                self.RUNNING,
                float(counts.get("running", 0)),
                labels=labels,
                label_names=("task_type",),
            )
            registry.set_gauge(
                self.CANCELLING,
                float(counts.get("cancelling", 0)),
                labels=labels,
                label_names=("task_type",),
            )
            registry.set_gauge(
                self.COMPLETED,
                float(counts.get("completed", 0)),
                labels=labels,
                label_names=("task_type",),
            )
            registry.set_gauge(
                self.FAILED,
                float(counts.get("failed", 0)),
                labels=labels,
                label_names=("task_type",),
            )
            registry.set_gauge(
                self.CANCELLED,
                float(counts.get("cancelled", 0)),
                labels=labels,
                label_names=("task_type",),
            )


@dataclass
class TaskEventCollector(EventMetricCollector):
    """Export terminal task latency and accepted runtime error reports."""

    END_TO_END_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        "task", "end_to_end_duration", unit="seconds"
    )
    ERROR_EVENTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        "task", "error_events", unit="total"
    )
    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"task.finished", "task.error"})

    def collect(self, registry=None) -> None:
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        task_type = str(payload.get("task_type") or "")
        if not task_type:
            return
        if event_name == "task.error":
            registry.inc_counter(
                self.ERROR_EVENTS_TOTAL,
                labels={
                    "task_type": task_type,
                    "error_code": normalize_metric_error_code(payload.get("error_code"))
                    or "unknown",
                },
                label_names=("task_type", "error_code"),
            )
            return
        status = str(payload.get("status") or "")
        if status not in {"completed", "failed", "cancelled"}:
            return
        try:
            duration = float(payload["end_to_end_duration_seconds"])
        except (KeyError, TypeError, ValueError):
            return
        if not math.isfinite(duration) or duration < 0:
            return
        registry.observe_histogram(
            self.END_TO_END_DURATION_SECONDS,
            duration,
            labels={
                "task_type": task_type,
                "status": status,
            },
            label_names=("task_type", "status"),
        )
