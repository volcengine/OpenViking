# Copyright (c) 2026 Beijing Volcano Engine Technology Co. Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Queue wait-duration collector.

Events are emitted by ``NamedQueue._report_queue_wait`` at dequeue time with the
wall-clock duration the message spent between enqueue and dequeue. Long waits are
the measurable symptom of bulk ingestion starving interactive queues (#4578);
the existing ``QueueCollector`` already exports per-queue depth gauges — this
collector adds the latency dimension.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector

# Queue waits span from milliseconds (healthy) to hours (starved), so use a
# log-ish ladder rather than the request-latency default buckets.
QUEUE_WAIT_BUCKETS: tuple[float, ...] = (0.1, 0.5, 1, 5, 15, 60, 300, 1800, 7200)


@dataclass
class QueueWaitCollector(EventMetricCollector):
    """Translate ``queue.wait`` events into a per-queue wait-duration histogram."""

    DOMAIN: ClassVar[str] = "queue"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_wait_seconds
    # e.g.: openviking_queue_wait_seconds
    WAIT_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "wait", unit="seconds"
    )

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"queue.wait"})

    def collect(self, registry=None) -> None:
        """No-op: queue-wait metrics are event-driven."""
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """Record one dequeue wait observation for the emitting queue."""
        if event_name != "queue.wait":
            return
        if "queue" not in payload or "wait_seconds" not in payload:
            return
        try:
            wait_seconds = float(payload["wait_seconds"])
        except (TypeError, ValueError):
            return
        if wait_seconds < 0:
            return
        registry.observe_histogram(
            self.WAIT_SECONDS,
            wait_seconds,
            labels={"queue": str(payload["queue"])},
            label_names=("queue",),
            buckets=QUEUE_WAIT_BUCKETS,
        )
