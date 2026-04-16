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
    Translate HTTP lifecycle events into request volume, latency, and inflight gauges.

    Events are emitted by `HttpRequestLifecycleDataSource` (and middleware call sites) and are
    normalized to avoid framework-specific request objects leaking into the metrics layer.
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
        """Implement the collector interface as a no-op because HTTP metrics are event-driven."""
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """
        Dispatch one normalized HTTP event payload to the matching metric writers.

        Supported payloads:
        - http.request: method, route, status, duration_seconds, optional account_id
        - http.inflight: route, value, optional account_id
        """
        if event_name == "http.request":
            # Be defensive: router/middleware bugs should never crash metrics.
            required_keys = ("method", "route", "status", "duration_seconds")
            if not all(key in payload for key in required_keys):
                return
            try:
                duration_seconds = float(payload["duration_seconds"])
            except (TypeError, ValueError):
                return
            self.record_request(
                registry,
                method=str(payload["method"]),
                route=str(payload["route"]),
                status=str(payload["status"]),
                duration_seconds=duration_seconds,
                account_id=payload.get("account_id"),
            )
            return
        if event_name == "http.inflight":
            if "route" not in payload or "value" not in payload:
                return
            try:
                value = float(payload["value"])
            except (TypeError, ValueError):
                return
            self.record_inflight(
                registry,
                route=str(payload["route"]),
                value=value,
                account_id=payload.get("account_id"),
            )
            return

        # Unknown events are ignored; `EventMetricCollector.receive()` already gates supported
        # events, but keep this explicit to avoid future regressions.
        return

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
        """Record a completed HTTP request counter increment and one latency sample."""
        labels = {"method": str(method), "route": str(route), "status": str(status)}
        registry.inc_counter(
            self.REQUESTS_TOTAL,
            labels=labels,
            label_names=("method", "route", "status"),
            account_id=None if account_id is None else str(account_id),
        )
        if duration_seconds < 0:
            return
        registry.observe_histogram(
            self.REQUEST_DURATION_SECONDS,
            float(duration_seconds),
            labels=labels,
            label_names=("method", "route", "status"),
            account_id=None if account_id is None else str(account_id),
        )

    def record_inflight(
        self, registry, *, route: str, value: float, account_id: str | None = None
    ) -> None:
        """Set the inflight request gauge value for one route template."""
        registry.set_gauge(
            self.INFLIGHT_REQUESTS,
            float(value),
            labels={"route": str(route)},
            label_names=("route",),
            account_id=None if account_id is None else str(account_id),
        )
