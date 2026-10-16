# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.queue import QueuePipelineStateDataSource

from .base import CollectorConfig, EventMetricCollector, StateMetricCollector

QUEUE_DURATION_BUCKETS: tuple[float, ...] = (
    0.01,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
    1800.0,
    3600.0,
    21600.0,
    86400.0,
)
_QUEUE_OUTCOMES = frozenset({"success", "failed", "requeued", "cancelled", "exception"})


@dataclass
class QueueDurationCollector(EventMetricCollector):
    """Export processing and end-to-end latency for completed queue deliveries."""

    DOMAIN: ClassVar[str] = "queue"
    PROCESS_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "process_duration", unit="seconds"
    )
    END_TO_END_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "end_to_end_duration", unit="seconds"
    )
    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"queue.processed"})

    def collect(self, registry=None) -> None:
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        queue = str(payload.get("queue") or "").strip()
        outcome = str(payload.get("outcome") or "").strip()
        if not queue or outcome not in _QUEUE_OUTCOMES:
            return
        try:
            process_duration = float(payload["process_duration_seconds"])
        except (KeyError, TypeError, ValueError):
            return
        if not math.isfinite(process_duration) or process_duration < 0:
            return

        labels = {"queue": queue, "outcome": outcome}
        registry.observe_histogram(
            self.PROCESS_DURATION_SECONDS,
            process_duration,
            labels=labels,
            label_names=("queue", "outcome"),
            buckets=QUEUE_DURATION_BUCKETS,
        )

        raw_end_to_end = payload.get("end_to_end_duration_seconds")
        if raw_end_to_end is None:
            return
        try:
            end_to_end_duration = float(raw_end_to_end)
        except (TypeError, ValueError):
            return
        if not math.isfinite(end_to_end_duration) or end_to_end_duration < 0:
            return
        registry.observe_histogram(
            self.END_TO_END_DURATION_SECONDS,
            end_to_end_duration,
            labels=labels,
            label_names=("queue", "outcome"),
            buckets=QUEUE_DURATION_BUCKETS,
        )


@dataclass
class QueueCollector(StateMetricCollector):
    """
    Export queue pipeline depth gauges and monotonic throughput/error counters.

    Pending and in-progress counts represent instantaneous queue state, while processed and error
    totals arrive from the datasource as cumulative counters that must be converted into positive
    Prometheus increments.
    """

    DOMAIN: ClassVar[str] = "queue"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_pending
    # e.g.: openviking_queue_pending
    PENDING: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "pending")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_in_progress
    # e.g.: openviking_queue_in_progress
    IN_PROGRESS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "in_progress")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_processed_total
    # e.g.: openviking_queue_processed_total
    PROCESSED_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "processed", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_errors_total
    # e.g.: openviking_queue_errors_total
    ERRORS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "errors", unit="total")

    data_source: QueuePipelineStateDataSource
    config: CollectorConfig = CollectorConfig(timeout_seconds=0.5)
    _last_processed: dict[str, int] = field(default_factory=dict)
    _last_errors: dict[str, int] = field(default_factory=dict)

    def read_metric_input(self):
        """Read the latest queue pipeline status snapshot from the datasource."""
        return self.data_source.read_queue_status()

    def collect_hook(self, registry, metric_input) -> None:
        """
        Refresh queue gauges and apply deltas to monotonic queue counters.

        The datasource provides cumulative `processed` and `error_count` values. This collector
        converts them into Prometheus counters by incrementing by positive deltas and ignoring
        decreases.
        """
        statuses = metric_input
        for queue_name, status in statuses.items():
            labels = {"queue": str(queue_name)}
            registry.set_gauge(
                self.PENDING,
                float(status.pending),
                labels=labels,
                label_names=("queue",),
            )
            registry.set_gauge(
                self.IN_PROGRESS,
                float(status.in_progress),
                labels=labels,
                label_names=("queue",),
            )

            processed = int(status.processed)
            prev_processed = int(self._last_processed.get(queue_name, 0))
            if processed >= prev_processed:
                delta = processed - prev_processed
                if delta:
                    registry.inc_counter(
                        self.PROCESSED_TOTAL,
                        labels=labels,
                        label_names=("queue",),
                        amount=delta,
                    )
                self._last_processed[queue_name] = processed

            errors = int(status.error_count)
            prev_errors = int(self._last_errors.get(queue_name, 0))
            if errors >= prev_errors:
                delta = errors - prev_errors
                if delta:
                    registry.inc_counter(
                        self.ERRORS_TOTAL,
                        labels=labels,
                        label_names=("queue",),
                        amount=delta,
                    )
                self._last_errors[queue_name] = errors
