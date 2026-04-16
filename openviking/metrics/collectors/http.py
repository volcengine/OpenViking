# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Event collector: HTTPCollector.

This collector is fed by the HTTP middleware via EventCollectorRouter events:
- http.request: records request count and duration histogram.
- http.inflight: records inflight requests gauge per route template.

Labels are chosen to be stable and low-cardinality:
- route is the Starlette route template when available (e.g., "/sessions/{id}").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector


@dataclass
class HTTPCollector(EventMetricCollector):
    """
    Translate HTTP lifecycle events into request counters, latency histograms, and inflight gauges.

    The collector expects already-normalized route templates and status codes from the middleware
    datasource, then applies account-dimension policy before writing registry series.
    """

    DOMAIN: ClassVar[str] = "http"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_requests_total
    # e.g.: openviking_http_requests_total
    REQUESTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "requests", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_request_duration_seconds
    # e.g.: openviking_http_request_duration_seconds
    REQUEST_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "request_duration", unit="seconds"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_inflight_requests
    # e.g.: openviking_http_inflight_requests
    INFLIGHT_REQUESTS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "inflight_requests")

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"http.request", "http.inflight"})

    def collect(self, registry=None) -> None:
        """Implement the unified collector interface as a no-op for this event-driven collector."""
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """
        Translate one supported HTTP lifecycle event into the corresponding metric writes.

        The hook assumes payload validation has already happened in the shared event-collector
        entrypoint and only branches on the normalized event name.
        """
        if event_name == "http.request":
            self.record_request(
                registry,
                method=str(payload["method"]),
                route=str(payload["route"]),
                status=str(payload["status"]),
                duration_seconds=float(payload["duration_seconds"]),
                account_id=(
                    None if payload.get("account_id") is None else str(payload.get("account_id"))
                ),
            )
            return
        if event_name == "http.inflight":
            self.record_inflight(
                registry,
                route=str(payload["route"]),
                value=float(payload["value"]),
                account_id=(
                    None if payload.get("account_id") is None else str(payload.get("account_id"))
                ),
            )

    def record_request(
        self,
        registry,
        *,
        method: str,
        route: str,
        status: str,
        duration_seconds: float,
        account_id: str | None = None,
    ) -> None:
        """
        Record a completed HTTP request as both a counter increment and a latency sample.

        Counter and histogram labels intentionally share the same route/method/status tuple so
        downstream PromQL can aggregate them consistently.
        """
        labels = {"method": str(method), "route": str(route), "status": str(status)}
        registry.inc_counter(
            self.REQUESTS_TOTAL,
            labels=labels,
            label_names=("method", "route", "status"),
            account_id=account_id,
        )
        registry.observe_histogram(
            self.REQUEST_DURATION_SECONDS,
            float(duration_seconds),
            labels=labels,
            label_names=("method", "route", "status"),
            account_id=account_id,
        )

    def record_inflight(
        self, registry, *, route: str, value: float, account_id: str | None = None
    ) -> None:
        """
        Set the current inflight request gauge value for one normalized route template.

        The caller provides the absolute inflight value, allowing middleware to keep the stateful
        accounting logic outside the collector itself.
        """
        registry.set_gauge(
            self.INFLIGHT_REQUESTS,
            float(value),
            labels={"route": str(route)},
            label_names=("route",),
            account_id=account_id,
        )
