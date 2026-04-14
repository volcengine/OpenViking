# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from .base import EventMetricDataSource


class ResourceIngestionEventDataSource(EventMetricDataSource):
    """
    Emit resource ingestion lifecycle events consumed by `ResourceIngestionCollector`.

    Resource processing already knows stage names and wait durations at the instrumentation call
    site, so this datasource simply normalizes those facts into event payloads.
    """

    @staticmethod
    def record_stage(
        *,
        stage: str,
        status: str,
        duration_seconds: float,
        account_id: str | None = None,
    ) -> None:
        """
        Emit the outcome and latency of one named resource-processing stage.

        The event represents a completed stage execution, not a streaming update while the stage
        is still in progress.
        """
        payload = {
            "stage": str(stage),
            "status": str(status),
            "duration_seconds": float(duration_seconds),
        }
        if account_id and str(account_id).strip():
            payload["account_id"] = str(account_id).strip()
        EventMetricDataSource._emit(
            "resource.stage",
            payload,
        )

    @staticmethod
    def record_wait(
        *, operation: str, duration_seconds: float, account_id: str | None = None
    ) -> None:
        """
        Emit the wait time spent on a named resource-ingestion operation.

        This is typically used for queueing or dependency wait segments that are operationally
        useful but distinct from active resource-processing stages.
        """
        payload = {
            "operation": str(operation),
            "duration_seconds": float(duration_seconds),
        }
        if account_id and str(account_id).strip():
            payload["account_id"] = str(account_id).strip()
        EventMetricDataSource._emit(
            "resource.wait",
            payload,
        )
