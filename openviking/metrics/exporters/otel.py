# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Refresh OpenViking state and send SDK metric data through official OTLP exporters."""

from __future__ import annotations

import asyncio

from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter as GrpcExporter,
)
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter as HttpExporter,
)

from openviking.metrics.core.base import MetricExporter
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class OTelMetricExporter(MetricExporter):
    """Own the application refresh cycle; the SDK owns encoding, transport and retries."""

    def __init__(
        self,
        *,
        registry,
        collector_manager=None,
        refresh_deadline_seconds=1.0,
        protocol="grpc",
        insecure=False,
        endpoint="localhost:4317",
        service_name="openviking-server",
        export_interval_ms=10000,
        export_timeout_seconds=None,
        headers=None,
    ):
        self._registry = registry
        self._collector_manager = collector_manager
        self._refresh_deadline_seconds = float(refresh_deadline_seconds)
        self._service_name = service_name
        self._interval = max(1000, int(export_interval_ms)) / 1000
        self._timeout = float(
            export_timeout_seconds
            if export_timeout_seconds is not None
            else max(5.0, self._refresh_deadline_seconds)
        )
        self._closed = False
        self._export_task = None
        self._export_lock = asyncio.Lock()
        if protocol not in ("grpc", "http"):
            raise ValueError(f"unsupported OTLP protocol: {protocol}")
        options = {"endpoint": endpoint, "headers": headers, "timeout": self._timeout}
        if protocol == "http":
            self._sdk = HttpExporter(**options)
        else:
            self._sdk = GrpcExporter(**options, insecure=insecure)

    async def export(self) -> str:
        async with self._export_lock:
            if self._closed:
                return ""
            if self._collector_manager is not None:
                try:
                    await asyncio.wait_for(
                        self._collector_manager.refresh_all(
                            self._registry, deadline_seconds=self._refresh_deadline_seconds
                        ),
                        timeout=self._refresh_deadline_seconds,
                    )
                except Exception as exc:
                    logger.warning("OTLP metric source refresh failed: %s", exc)
            data = self._registry.collect(service_name=self._service_name)
            if not any(
                scope.metrics
                for resource in data.resource_metrics
                for scope in resource.scope_metrics
            ):
                return ""
            sending = asyncio.create_task(
                asyncio.to_thread(self._sdk.export, data, timeout_millis=self._timeout * 1000)
            )
            try:
                await asyncio.shield(sending)
            except asyncio.CancelledError:
                # Closing the SDK transport must wait for the in-flight blocking export.
                await sending
                raise
        return ""

    def start(self) -> None:
        if self._closed or self._export_task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("OTLP periodic export requires a running application event loop")
            return
        self._export_task = loop.create_task(self._run(), name="openviking-otel-metrics")

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while not self._closed:
            try:
                await self.export()
            except Exception as exc:
                logger.warning("OTLP metrics export failed: %s", exc)
            next_tick += self._interval
            now = loop.time()
            if next_tick < now:
                next_tick = now + self._interval
            await asyncio.sleep(next_tick - now)

    async def shutdown(self) -> None:
        self._closed = True
        if self._export_task is not None:
            self._export_task.cancel()
            try:
                await self._export_task
            except asyncio.CancelledError:
                pass
            self._export_task = None
        async with self._export_lock:
            if self._sdk is not None:
                await asyncio.to_thread(self._sdk.shutdown)
                self._sdk = None
