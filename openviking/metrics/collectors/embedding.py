# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Event collector: EmbeddingCollector.

Tracks embedding request outcomes and latency:
- Requests counter by status
- Latency histogram by status
- Error counter by normalized error code

It is fed by EmbeddingEventDataSource events emitted from embedding call sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector


@dataclass
class EmbeddingCollector(EventMetricCollector):
    """
    Translate embedding success and error events into request, latency, and error metrics.

    The collector treats embedding outcomes as event-driven writes because the interesting facts
    are known at completion time and do not require scrape-time state inspection.
    """

    DOMAIN: ClassVar[str] = "embedding"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_requests_total
    # e.g.: openviking_embedding_requests_total
    REQUESTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "requests", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_latency_seconds
    # e.g.: openviking_embedding_latency_seconds
    LATENCY_SECONDS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "latency", unit="seconds")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_errors_total
    # e.g.: openviking_embedding_errors_total
    ERRORS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "errors", unit="total")

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"embedding.success", "embedding.error"})

    def collect(self, registry=None) -> None:
        """Implement the unified collector interface as a no-op for this event-driven collector."""
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """
        Translate one supported embedding event into the corresponding metric writes.

        Success and error events intentionally diverge into separate write paths so the collector
        can emit different label sets without overloading one payload shape.
        """
        if event_name == "embedding.success":
            self.record_success(
                registry,
                latency_seconds=float(payload["latency_seconds"]),
                account_id=(
                    None if payload.get("account_id") is None else str(payload.get("account_id"))
                ),
            )
            return
        if event_name == "embedding.error":
            self.record_error(
                registry,
                error_code=str(payload["error_code"]),
                account_id=(
                    None if payload.get("account_id") is None else str(payload.get("account_id"))
                ),
            )

    def record_success(
        self, registry, *, latency_seconds: float, account_id: str | None = None
    ) -> None:
        """
        Record a successful embedding request and its latency histogram sample.

        The collector emits both a request counter and a latency observation so success volume and
        latency remain queryable from the same event stream.
        """
        labels = {"status": "ok"}
        registry.inc_counter(
            self.REQUESTS_TOTAL,
            labels=labels,
            label_names=("status",),
            account_id=account_id,
        )
        registry.observe_histogram(
            self.LATENCY_SECONDS,
            float(latency_seconds),
            labels=labels,
            label_names=("status",),
            account_id=account_id,
        )

    def record_error(self, registry, *, error_code: str, account_id: str | None = None) -> None:
        """
        Record an errored embedding request and increment its normalized error-code counter.

        Error outcomes still increment the request-total family so total embedding attempt counts
        remain comparable with successful request counts.
        """
        registry.inc_counter(
            self.REQUESTS_TOTAL,
            labels={"status": "error"},
            label_names=("status",),
            account_id=account_id,
        )
        registry.inc_counter(
            self.ERRORS_TOTAL,
            labels={"error_code": str(error_code or "unknown")},
            label_names=("error_code",),
            account_id=account_id,
        )
