# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Event collector for VectorDB operation outcomes, latency, and retries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector


@dataclass
class VikingDBOperationCollector(EventMetricCollector):
    """Translate bounded VectorDB events into counters and latency observations."""

    DOMAIN: ClassVar[str] = "vikingdb"
    OPERATIONS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "operations", unit="total")
    OPERATION_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "operation_duration", unit="seconds")
    RECORDS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "records", unit="total")
    SEARCH_RESULTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "search_results", unit="total")
    RETRIES_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "retries", unit="total")
    RETRY_DELAY_SECONDS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "retry_delay", unit="seconds")
    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"vikingdb.operation", "vikingdb.retry"})

    def collect(self, registry=None) -> None:
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        if event_name == "vikingdb.operation":
            operation_labels = {key: str(payload.get(key) or "unknown") for key in (
                "backend", "access_mode", "operation", "outcome", "error_source", "error_code", "error_category", "retryable",
            )}
            registry.inc_counter(self.OPERATIONS_TOTAL, labels=operation_labels, label_names=tuple(operation_labels))
            duration_labels = {key: operation_labels[key] for key in ("backend", "access_mode", "operation", "outcome")}
            registry.observe_histogram(self.OPERATION_DURATION_SECONDS, float(payload.get("duration_seconds") or 0.0), labels=duration_labels, label_names=tuple(duration_labels))
            records = int(payload.get("records") or 0)
            if records > 0:
                labels = {"backend": operation_labels["backend"], "operation": operation_labels["operation"], "direction": str(payload.get("record_direction") or "unknown")}
                registry.inc_counter(self.RECORDS_TOTAL, amount=records, labels=labels, label_names=tuple(labels))
            search_results = int(payload.get("search_results") or 0)
            if search_results > 0:
                labels = {"backend": operation_labels["backend"], "search_type": str(payload.get("search_type") or "unknown")}
                registry.inc_counter(self.SEARCH_RESULTS_TOTAL, amount=search_results, labels=labels, label_names=tuple(labels))
            return
        labels = {key: str(payload.get(key) or "unknown") for key in ("backend", "operation", "error_source", "error_code")}
        registry.inc_counter(self.RETRIES_TOTAL, labels=labels, label_names=tuple(labels))
        registry.observe_histogram(self.RETRY_DELAY_SECONDS, float(payload.get("delay_seconds") or 0.0), labels=labels, label_names=tuple(labels))


__all__ = ["VikingDBOperationCollector"]
