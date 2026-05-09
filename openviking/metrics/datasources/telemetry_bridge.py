# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Telemetry bridge event datasource.

This datasource emits the aggregated request/operation telemetry summary as a single
best-effort event. `TelemetryBridgeCollector` consumes that event and translates it into
Prometheus metrics.
"""

from __future__ import annotations

from typing import Any, Mapping

from .base import EventMetricDataSource


class TelemetryBridgeEventDataSource(EventMetricDataSource):
    """
    Emit aggregated telemetry summaries for later translation by `TelemetryBridgeCollector`.

    This datasource forms the bridge between the runtime telemetry subsystem and Prometheus
    collectors that turn higher-level summaries into multiple metric family updates.
    """

    @staticmethod
    def record_summary(summary: Mapping[str, Any]) -> None:
        """
        Emit a best-effort telemetry summary event using a plain-dictionary payload copy.

        Copying the summary into a plain dict prevents downstream consumers from depending on the
        mutability or concrete type of the source telemetry object.
        """
        EventMetricDataSource._emit("telemetry.summary", {"summary": dict(summary)})
