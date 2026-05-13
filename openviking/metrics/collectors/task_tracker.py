# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.task import TaskStateDataSource

from .base import CollectorConfig, StateMetricCollector


@dataclass
class TaskTrackerCollector(StateMetricCollector):
    """
    Export task tracker backlog and completion counts grouped by task type.

    The datasource returns a snapshot of task counts, so all four families are exposed as gauges
    keyed only by `task_type` rather than as cumulative counters.
    """

    DOMAIN: ClassVar[str] = "task"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_pending
    # e.g.: openviking_task_pending
    PENDING: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "pending")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_running
    # e.g.: openviking_task_running
    RUNNING: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "running")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_completed
    # e.g.: openviking_task_completed
    COMPLETED: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "completed")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_failed
    # e.g.: openviking_task_failed
    FAILED: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "failed")

    data_source: TaskStateDataSource
    config: CollectorConfig = CollectorConfig(timeout_seconds=0.5)

    def read_metric_input(self):
        """Read the latest task-count snapshot grouped by task type from the datasource."""
        return self.data_source.read_task_counts()

    def collect_hook(self, registry, metric_input) -> None:
        """Translate the task-count snapshot into pending, running, completed, and failed gauges."""
        counts_by_type = metric_input
        # Snapshot semantics: types can disappear entirely, so clear previous series to avoid
        # exporting stale non-zero gauges forever.
        for metric_name in (self.PENDING, self.RUNNING, self.COMPLETED, self.FAILED):
            registry.gauge_delete_matching(metric_name, match_labels={})
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
