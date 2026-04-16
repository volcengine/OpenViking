# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
HTTP request lifecycle DataSource.

This DataSource emits two event types:
- http.request: for request count and duration metrics.
- http.inflight: for inflight request gauge.

The actual MetricRegistry writes are performed by HTTPCollector.
"""

from __future__ import annotations

from .base import EventMetricDataSource


class HttpRequestLifecycleDataSource(EventMetricDataSource):
    """
    Emit normalized HTTP lifecycle events for request counters, latency, and inflight gauges.

    The datasource intentionally exposes a small, route-template-based payload shape so HTTP
    collectors can remain agnostic of framework request objects.
    """

    @staticmethod
    def record_request(
        *,
        method: str,
        route: str,
        status: str,
        duration_seconds: float,
        account_id: str | None = None,
    ) -> None:
        """
        Emit a completed-request event with normalized method, route, status, and duration data.

        The payload is plain data so middleware can attach an explicit `account_id` when the
        authenticated tenant is already known.
        """
        EventMetricDataSource._emit(
            "http.request",
            {
                "method": str(method),
                "route": str(route),
                "status": str(status),
                "duration_seconds": float(duration_seconds),
                "account_id": None if account_id is None else str(account_id),
            },
        )

    @staticmethod
    def set_inflight(*, route: str, value: float, account_id: str | None = None) -> None:
        """
        Emit the current inflight request gauge value for one normalized route template.

        Callers are expected to supply the post-delta value so the datasource remains stateless.
        """
        EventMetricDataSource._emit(
            "http.inflight",
            {
                "route": str(route),
                "value": float(value),
                "account_id": None if account_id is None else str(account_id),
            },
        )
