# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Retrieval stats DataSource.

This DataSource provides a single entry point to record retrieval outcomes.
It emits an aggregated `retrieval.completed` event which is mapped to Prometheus
metrics by RetrievalCollector.

Why event-based?
The retrieval pipeline already knows the outcome (result_count, latency, rerank flags)
at the call site. Emitting a single event keeps the instrumentation lightweight while
keeping MetricRegistry writes in collectors.
"""

from __future__ import annotations

from .base import EventMetricDataSource


class RetrievalStatsDataSource(EventMetricDataSource):
    """
    Emit aggregated retrieval outcome events for later collector-side translation.

    Retrieval code already knows the final result count, latency, and rerank decisions at the
    call site, so an event datasource keeps instrumentation lightweight and registry-free.
    """

    @staticmethod
    def record_retrieval(
        *,
        context_type: str,
        result_count: int,
        latency_seconds: float,
        rerank_used: bool = False,
        rerank_fallback: bool = False,
    ) -> None:
        """
        Emit the final retrieval outcome, latency, and rerank flags for one request.

        The payload intentionally represents one completed retrieval operation rather than a live
        progress stream.
        """
        EventMetricDataSource._emit(
            "retrieval.completed",
            {
                "context_type": str(context_type or "unknown"),
                "result_count": int(result_count),
                "latency_seconds": float(latency_seconds),
                "rerank_used": bool(rerank_used),
                "rerank_fallback": bool(rerank_fallback),
            },
        )
