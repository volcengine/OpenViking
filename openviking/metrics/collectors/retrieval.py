# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Event/DomainStats collector: RetrievalCollector.

This collector records retrieval outcomes:
- request count, results count, zero-result count
- latency histogram
- rerank usage/fallback counts

It is fed by `RetrievalStatsDataSource.record_retrieval(...)` which emits a single
`retrieval.completed` event per retrieval operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector


@dataclass
class RetrievalCollector(EventMetricCollector):
    """
    Translate retrieval completion events into throughput, result-volume, and latency metrics.

    Each completed retrieval emits a single normalized event summarizing the request. The collector
    expands that event into bounded counters and one latency histogram sample keyed by
    `context_type`.
    """

    DOMAIN: ClassVar[str] = "retrieval"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_requests_total
    # e.g.: openviking_retrieval_requests_total
    REQUESTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "requests", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_results_total
    # e.g.: openviking_retrieval_results_total
    RESULTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "results", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_zero_result_total
    # e.g.: openviking_retrieval_zero_result_total
    ZERO_RESULT_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "zero_result", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_latency_seconds
    # e.g.: openviking_retrieval_latency_seconds
    LATENCY_SECONDS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "latency", unit="seconds")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_rerank_used_total
    # e.g.: openviking_retrieval_rerank_used_total
    RERANK_USED_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "rerank_used", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_rerank_fallback_total
    # e.g.: openviking_retrieval_rerank_fallback_total
    RERANK_FALLBACK_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "rerank_fallback", unit="total"
    )

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"retrieval.completed"})

    def collect(self, registry=None) -> None:
        """Implement the collector interface as a no-op because retrieval metrics are event-driven."""
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """
        Translate the retrieval completion payload into retrieval outcome metric updates.

        The event has already been normalized by the datasource, so the hook only coerces field
        types before delegating to `record_completed`.
        """
        self.record_completed(
            registry,
            context_type=str(payload["context_type"]),
            result_count=int(payload["result_count"]),
            latency_seconds=float(payload["latency_seconds"]),
            rerank_used=bool(payload.get("rerank_used")),
            rerank_fallback=bool(payload.get("rerank_fallback")),
        )

    def record_completed(
        self,
        registry,
        *,
        context_type: str,
        result_count: int,
        latency_seconds: float,
        rerank_used: bool,
        rerank_fallback: bool,
    ) -> None:
        """
        Record counters and latency for one completed retrieval operation.

        Result counts are clamped to non-negative counter increments, while rerank usage and
        rerank fallback are tracked as separate global counters for coarse operational visibility.
        """
        labels = {"context_type": str(context_type or "unknown")}
        registry.inc_counter(
            self.REQUESTS_TOTAL,
            labels=labels,
            label_names=("context_type",),
        )
        registry.inc_counter(
            self.RESULTS_TOTAL,
            labels=labels,
            label_names=("context_type",),
            amount=max(0, int(result_count)),
        )
        if int(result_count) == 0:
            registry.inc_counter(
                self.ZERO_RESULT_TOTAL,
                labels=labels,
                label_names=("context_type",),
            )
        registry.observe_histogram(
            self.LATENCY_SECONDS,
            float(latency_seconds),
            labels=labels,
            label_names=("context_type",),
        )
        if rerank_used:
            registry.inc_counter(self.RERANK_USED_TOTAL)
        if rerank_fallback:
            registry.inc_counter(self.RERANK_FALLBACK_TOTAL)
