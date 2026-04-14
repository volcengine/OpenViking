# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Event collector: ResourceIngestionCollector.

Exports stage-level metrics for resource ingestion and processing:
- stage counters by stage/status
- stage duration histogram
- wait duration histogram (operation-level)

This collector is fed by ResourceIngestionEventDataSource, which emits events from the
resource processing pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector


@dataclass
class ResourceIngestionCollector(EventMetricCollector):
    """
    Translate resource-ingestion pipeline events into counters and latency histograms.

    The collector records both stage-level outcomes and queue wait time so the resource ingestion
    path can be observed from enqueue delay through each named processing stage.
    """

    DOMAIN: ClassVar[str] = "resource"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_stage_total
    # e.g.: openviking_resource_stage_total
    STAGE_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "stage", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_stage_duration_seconds
    # e.g.: openviking_resource_stage_duration_seconds
    STAGE_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "stage_duration", unit="seconds"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_wait_duration_seconds
    # e.g.: openviking_resource_wait_duration_seconds
    WAIT_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "wait_duration", unit="seconds"
    )

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"resource.stage", "resource.wait"})

    def collect(self, registry=None) -> None:
        """Implement the collector interface as a no-op because resource metrics are push-driven."""
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """
        Dispatch one normalized resource-ingestion event to the matching metric writer.

        `resource.stage` emits both a counter increment and duration sample, while `resource.wait`
        emits only a wait-duration histogram observation.
        """
        if event_name == "resource.stage":
            self.record_stage(
                registry,
                stage=str(payload["stage"]),
                status=str(payload["status"]),
                duration_seconds=float(payload["duration_seconds"]),
                account_id=payload.get("account_id"),
            )
            return
        if event_name == "resource.wait":
            self.record_wait(
                registry,
                operation=str(payload["operation"]),
                duration_seconds=float(payload["duration_seconds"]),
                account_id=payload.get("account_id"),
            )

    def record_stage(
        self,
        registry,
        *,
        stage: str,
        status: str,
        duration_seconds: float,
        account_id: str | None = None,
    ) -> None:
        """Record one named resource stage outcome together with its execution latency."""
        labels = {"stage": str(stage), "status": str(status)}
        registry.inc_counter(
            self.STAGE_TOTAL,
            labels=labels,
            label_names=("stage", "status"),
            account_id=None if account_id is None else str(account_id),
        )
        registry.observe_histogram(
            self.STAGE_DURATION_SECONDS,
            float(duration_seconds),
            labels=labels,
            label_names=("stage", "status"),
            account_id=None if account_id is None else str(account_id),
        )

    def record_wait(
        self, registry, *, operation: str, duration_seconds: float, account_id: str | None = None
    ) -> None:
        """Record one wait-duration sample for a resource-ingestion operation."""
        registry.observe_histogram(
            self.WAIT_DURATION_SECONDS,
            float(duration_seconds),
            labels={"operation": str(operation)},
            label_names=("operation",),
            account_id=None if account_id is None else str(account_id),
        )
