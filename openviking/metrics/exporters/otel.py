# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
OpenTelemetry OTLP metrics exporter implementation.

This exporter serializes the in-process MetricRegistry directly into OTLP protobuf
messages and sends them to an OTLP endpoint over HTTP or gRPC.

Key design goals:
- Export the same MetricRegistry snapshot semantics that PrometheusExporter renders
- Preserve histogram bucket/count/sum semantics without re-aggregation
- Keep exporter lifecycle self-contained through start()/shutdown()
- Refresh collectors before export so snapshot-based metrics stay aligned
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Iterable, Optional, Tuple

import requests

from openviking.metrics.core.base import MetricExporter

logger = logging.getLogger(__name__)

try:
    import grpc
except ImportError:
    grpc = None

try:
    from requests import RequestException
except Exception:
    RequestException = Exception

try:
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import (
        MetricsServiceStub,
    )
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, InstrumentationScope, KeyValue
    from opentelemetry.proto.metrics.v1.metrics_pb2 import (
        AGGREGATION_TEMPORALITY_CUMULATIVE,
        Gauge,
        Histogram,
        HistogramDataPoint,
        Metric,
        NumberDataPoint,
        Sum,
    )
    from opentelemetry.proto.resource.v1.resource_pb2 import Resource
except ImportError:
    ExportMetricsServiceRequest = None
    MetricsServiceStub = None
    AnyValue = None
    InstrumentationScope = None
    KeyValue = None
    Gauge = None
    Histogram = None
    HistogramDataPoint = None
    Metric = None
    NumberDataPoint = None
    Sum = None
    Resource = None
    AGGREGATION_TEMPORALITY_CUMULATIVE = None


def _compute_next_tick_and_sleep(
    *,
    now: float,
    interval_s: float,
    next_tick: float,
) -> tuple[float, float]:
    """
    Compute next tick time and sleep duration for a fixed-interval loop.

    This is a pure function to keep scheduling behavior testable without adding
    production-only hooks.

    Rules:
    - If the loop overruns (now >= next_tick), advance next_tick by interval until it is in the future.
    - Sleep only until the next_tick.

    Args:
        now: Current monotonic time in seconds.
        interval_s: Target loop interval in seconds.
        next_tick: Previously scheduled tick time in seconds.

    Returns:
        (next_tick, sleep_seconds)
    """
    while next_tick <= now:
        next_tick += interval_s
    return next_tick, max(0.0, next_tick - now)


class OTelMetricExporter(MetricExporter):
    """
    Export MetricRegistry snapshots to OTLP metrics.

    This exporter intentionally avoids OTel SDK metric aggregation. Instead, it
    periodically snapshots MetricRegistry, converts the snapshot into OTLP protobuf
    payloads, and pushes them to the configured collector endpoint.
    """

    def __init__(
        self,
        *,
        registry,
        collector_manager=None,
        refresh_deadline_seconds: float = 1.0,
        protocol: str = "grpc",
        insecure: bool = False,
        endpoint: str = "localhost:4317",
        service_name: str = "openviking-server",
        export_interval_ms: int = 10000,
        export_timeout_seconds: Optional[float] = None,
        enabled: bool = True,
    ) -> None:
        """
        Initialize the OTLP metrics exporter.

        Args:
            registry: The in-process metric registry backing all exporters.
            collector_manager: Optional collector refresh orchestrator.
            refresh_deadline_seconds: Best-effort refresh timeout before each export.
            protocol: Transport protocol, either "grpc" or "http".
            insecure: Whether gRPC transport should use plaintext.
            endpoint: OTLP collector endpoint.
            service_name: Service name written into OTLP resource attributes.
            export_interval_ms: Periodic export interval in milliseconds.
            export_timeout_seconds: Transport timeout for one OTLP export request.
            enabled: Whether the exporter is enabled.
        """
        self._registry = registry
        self._collector_manager = collector_manager
        self._refresh_deadline_seconds = float(refresh_deadline_seconds)
        self._protocol = protocol.lower()
        self._insecure = bool(insecure)
        self._endpoint = endpoint
        self._service_name = service_name
        self._export_interval_ms = max(1000, int(export_interval_ms))
        self._export_timeout_seconds = float(
            export_timeout_seconds
            if export_timeout_seconds is not None
            else max(5.0, self._refresh_deadline_seconds)
        )
        self._enabled = bool(enabled)
        self._export_task: Optional[asyncio.Task[Any]] = None
        self._http_session: Optional[requests.Session] = None
        self._grpc_channel: Optional[Any] = None
        self._grpc_stub: Optional[Any] = None
        self._start_time_ns = time.time_ns()

        if self._protocol not in {"grpc", "http"}:
            logger.warning(
                "[OTelMetricExporter] invalid protocol=%s, defaulting to grpc",
                self._protocol,
            )
            self._protocol = "grpc"

        if self._enabled:
            self._init_transport()

    def _init_transport(self) -> None:
        """
        Initialize the transport client for the configured OTLP protocol.

        The exporter keeps the transport client alive across export cycles so the
        periodic loop only pays the cost of serializing current registry snapshots.
        """
        if not self._protobuf_available():
            logger.warning(
                "[OTelMetricExporter] initialization failed: OTLP metrics protobuf dependencies are not available"
            )
            self._enabled = False
            return

        try:
            if self._protocol == "http":
                self._validate_http_endpoint(self._endpoint)
                self._http_session = requests.Session()
            else:
                if grpc is None or MetricsServiceStub is None:
                    raise ImportError("gRPC OTLP metrics dependencies are not available")
                if self._insecure:
                    self._grpc_channel = grpc.insecure_channel(self._endpoint)
                else:
                    self._grpc_channel = grpc.secure_channel(
                        self._endpoint,
                        grpc.ssl_channel_credentials(),
                    )
                self._grpc_stub = MetricsServiceStub(self._grpc_channel)

            logger.info(
                "[OTelMetricExporter] initialized: phase=init_transport protocol=%s endpoint=%s service_name=%s export_interval_ms=%s export_timeout_seconds=%s",
                self._protocol,
                self._endpoint,
                self._service_name,
                self._export_interval_ms,
                self._export_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "[OTelMetricExporter] initialization failed: phase=init_transport protocol=%s endpoint=%s service_name=%s export_timeout_seconds=%s error=%s",
                self._protocol,
                self._endpoint,
                self._service_name,
                self._export_timeout_seconds,
                exc,
            )
            self._enabled = False

    def _protobuf_available(self) -> bool:
        """Return whether all required OTLP protobuf symbols are available."""
        return all(
            symbol is not None
            for symbol in (
                ExportMetricsServiceRequest,
                AnyValue,
                InstrumentationScope,
                KeyValue,
                Metric,
                NumberDataPoint,
                HistogramDataPoint,
                Gauge,
                Sum,
                Histogram,
                Resource,
                AGGREGATION_TEMPORALITY_CUMULATIVE,
            )
        )

    def _validate_http_endpoint(self, endpoint: str) -> None:
        """
        Validate that an OTLP/HTTP endpoint includes a URL scheme and path.

        Args:
            endpoint: OTLP/HTTP metrics endpoint.
        """
        if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
            raise ValueError(
                "OTLP/HTTP endpoint must include scheme, e.g. 'http://localhost:4318/v1/metrics'"
            )

    async def export(self) -> str:
        """
        Refresh collectors and export the current MetricRegistry snapshot.

        Returns:
            An empty string to preserve the MetricExporter interface contract.
        """
        if not self._enabled:
            return ""

        await self._refresh_collectors()
        request = self._build_export_request()
        if request is None:
            return ""

        await self._send_request(request)
        return ""

    async def _refresh_collectors(self) -> None:
        """
        Best-effort refresh of snapshot-driven collectors before serialization.

        Export must never break application flow, so refresh failures are swallowed.
        """
        if self._collector_manager is None:
            return
        started = time.monotonic()
        try:
            await asyncio.wait_for(
                self._collector_manager.refresh_all(
                    self._registry,
                    deadline_seconds=self._refresh_deadline_seconds,
                ),
                timeout=self._refresh_deadline_seconds,
            )
        except asyncio.TimeoutError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "[OTelMetricExporter] collector refresh failed: phase=refresh_collectors reason=timeout protocol=%s endpoint=%s deadline_seconds=%s elapsed_ms=%s error=%s",
                self._protocol,
                self._endpoint,
                self._refresh_deadline_seconds,
                elapsed_ms,
                exc,
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "[OTelMetricExporter] collector refresh failed: phase=refresh_collectors reason=exception protocol=%s endpoint=%s deadline_seconds=%s elapsed_ms=%s error=%s",
                self._protocol,
                self._endpoint,
                self._refresh_deadline_seconds,
                elapsed_ms,
                exc,
            )

    def _build_export_request(self) -> Optional[ExportMetricsServiceRequest]:
        """
        Build an OTLP ExportMetricsServiceRequest from the current registry snapshot.

        Returns:
            A populated OTLP export request, or None when there is no metric family to send.
        """
        if not self._protobuf_available():
            return None

        now_ns = time.time_ns()
        request = ExportMetricsServiceRequest()
        resource_metrics = request.resource_metrics.add()
        resource_metrics.resource.CopyFrom(self._build_resource())
        scope_metrics = resource_metrics.scope_metrics.add()
        scope_metrics.scope.CopyFrom(
            InstrumentationScope(name="openviking.metrics.exporter.otel", version="1")
        )

        self._append_counter_metrics(scope_metrics.metrics, now_ns)
        self._append_gauge_metrics(scope_metrics.metrics, now_ns)
        self._append_histogram_metrics(scope_metrics.metrics, now_ns)
        self._append_dropped_series_metrics(scope_metrics.metrics, now_ns)

        if not scope_metrics.metrics:
            return None
        return request

    def _build_resource(self) -> Resource:
        """
        Build the OTLP resource envelope for this exporter.

        Returns:
            A Resource message containing the service name.
        """
        resource = Resource()
        resource.attributes.append(
            KeyValue(key="service.name", value=AnyValue(string_value=self._service_name))
        )
        return resource

    def _append_counter_metrics(self, metrics, now_ns: int) -> None:
        """
        Append all counter families from MetricRegistry as cumulative OTLP sums.

        Empty unlabeled counters emit a single zero data point to match PrometheusExporter.
        """
        for name, counter_series in self._registry.iter_counters():
            metric = metrics.add()
            metric.name = name
            metric.description = "OpenViking metric."
            metric.sum.CopyFrom(self._build_sum(counter_series, now_ns, name=name))

    def _append_gauge_metrics(self, metrics, now_ns: int) -> None:
        """
        Append all gauge families from MetricRegistry as OTLP gauges.

        Empty unlabeled gauges emit a single zero data point to match PrometheusExporter.
        """
        for name, gauge_series in self._registry.iter_gauges():
            metric = metrics.add()
            metric.name = name
            metric.description = "OpenViking metric."
            metric.gauge.CopyFrom(self._build_gauge(gauge_series, now_ns, name=name))

    def _append_histogram_metrics(self, metrics, now_ns: int) -> None:
        """
        Append all histogram families from MetricRegistry as cumulative OTLP histograms.

        Bucket counts and explicit bounds are copied directly from the registry snapshot.
        """
        for name, label_names, bucket_bounds, series_iter in self._registry.iter_histograms():
            series_list = list(series_iter)
            metric = metrics.add()
            metric.name = name
            metric.description = "OpenViking metric."
            metric.histogram.CopyFrom(
                self._build_histogram(
                    label_names=label_names,
                    bucket_bounds=bucket_bounds,
                    series_list=series_list,
                    now_ns=now_ns,
                )
            )

    def _append_dropped_series_metrics(self, metrics, now_ns: int) -> None:
        """
        Append dropped-series diagnostics as a cumulative OTLP sum family.

        This mirrors the `openviking_metrics_dropped_series_total` metric emitted by
        PrometheusExporter.
        """
        dropped_points: list[tuple[tuple[tuple[str, str], ...], float]] = []
        for metric_name, dropped in self._registry.iter_dropped_series():
            dropped_points.append(((("metric", metric_name),), float(dropped)))

        if not dropped_points:
            return

        metric = metrics.add()
        metric.name = "openviking_metrics_dropped_series_total"
        metric.description = "OpenViking metric."
        metric.sum.CopyFrom(self._build_sum(dropped_points, now_ns, name=metric.name))

    def _build_sum(self, series, now_ns: int, *, name: str) -> Sum:
        """
        Build an OTLP Sum message from a counter-like registry family.

        Args:
            series: List of `(labels, value)` tuples from MetricRegistry.
            now_ns: Export timestamp in nanoseconds.
            name: Metric family name for zero-series handling.
        """
        sum_metric = Sum(
            aggregation_temporality=AGGREGATION_TEMPORALITY_CUMULATIVE,
            is_monotonic=True,
        )

        if not series and not self._registry.counter_label_names(name):
            point = NumberDataPoint(
                start_time_unix_nano=self._start_time_ns,
                time_unix_nano=now_ns,
                as_int=0,
            )
            sum_metric.data_points.append(point)
            return sum_metric

        for labels, value in series:
            point = NumberDataPoint(
                start_time_unix_nano=self._start_time_ns,
                time_unix_nano=now_ns,
            )
            point.attributes.extend(self._build_attributes(labels))
            if float(value).is_integer():
                point.as_int = int(value)
            else:
                point.as_double = float(value)
            sum_metric.data_points.append(point)
        return sum_metric

    def _build_gauge(self, series, now_ns: int, *, name: str) -> Gauge:
        """
        Build an OTLP Gauge message from a registry gauge family.

        Args:
            series: List of `(labels, value)` tuples from MetricRegistry.
            now_ns: Export timestamp in nanoseconds.
            name: Metric family name for zero-series handling.
        """
        gauge_metric = Gauge()

        if not series and not self._registry.gauge_label_names(name):
            point = NumberDataPoint(time_unix_nano=now_ns, as_int=0)
            gauge_metric.data_points.append(point)
            return gauge_metric

        for labels, value in series:
            point = NumberDataPoint(time_unix_nano=now_ns)
            point.attributes.extend(self._build_attributes(labels))
            if float(value).is_integer():
                point.as_int = int(value)
            else:
                point.as_double = float(value)
            gauge_metric.data_points.append(point)
        return gauge_metric

    def _build_histogram(
        self,
        *,
        label_names: tuple[str, ...],
        bucket_bounds: tuple[float, ...],
        series_list,
        now_ns: int,
    ) -> Histogram:
        """
        Build an OTLP Histogram message from a registry histogram family.

        Args:
            name: Histogram family name.
            label_names: Registered label keys for the family.
            bucket_bounds: Explicit bucket upper bounds from the registry.
            series_list: Histogram series snapshot from the registry.
            now_ns: Export timestamp in nanoseconds.
        """
        histogram_metric = Histogram(
            aggregation_temporality=AGGREGATION_TEMPORALITY_CUMULATIVE,
        )

        if not series_list and not label_names:
            point = HistogramDataPoint(
                start_time_unix_nano=self._start_time_ns,
                time_unix_nano=now_ns,
                count=0,
                sum=0.0,
            )
            point.bucket_counts.extend((0,) * (len(bucket_bounds) + 1))
            point.explicit_bounds.extend(bucket_bounds)
            histogram_metric.data_points.append(point)
            return histogram_metric

        for labels, bucket_counts, count, value_sum in series_list:
            point = HistogramDataPoint(
                start_time_unix_nano=self._start_time_ns,
                time_unix_nano=now_ns,
                count=int(count),
                sum=float(value_sum),
            )
            point.attributes.extend(self._build_attributes(labels))
            point.bucket_counts.extend(int(v) for v in bucket_counts)
            point.explicit_bounds.extend(float(v) for v in bucket_bounds)
            histogram_metric.data_points.append(point)
        return histogram_metric

    def _build_attributes(self, labels: Iterable[Tuple[str, str]]) -> list[KeyValue]:
        """
        Convert MetricRegistry labels into OTLP KeyValue attributes.

        Args:
            labels: Iterable of sorted `(key, value)` label pairs.
        """
        return [KeyValue(key=key, value=AnyValue(string_value=value)) for key, value in labels]

    async def _send_request(self, request: ExportMetricsServiceRequest) -> None:
        """
        Send one OTLP metrics export request using the configured transport.

        Args:
            request: The populated OTLP metrics export payload.
        """
        metric_count = self._count_otlp_metrics(request)
        request_bytes = request.ByteSize()
        try:
            if self._protocol == "http":
                await asyncio.to_thread(self._send_http_request, request)
            else:
                await asyncio.to_thread(self._send_grpc_request, request)
        except (RequestException, OSError, TimeoutError) as exc:
            logger.warning(
                "[OTelMetricExporter] export failed: phase=send_request protocol=%s endpoint=%s timeout_seconds=%s metric_count=%s request_bytes=%s error_type=%s error=%s",
                self._protocol,
                self._endpoint,
                self._export_timeout_seconds,
                metric_count,
                request_bytes,
                type(exc).__name__,
                exc,
            )
        except Exception as exc:
            # Keep exporter failure best-effort. Log unexpected failures at warning level.
            logger.warning(
                "[OTelMetricExporter] export failed: phase=send_request protocol=%s endpoint=%s timeout_seconds=%s metric_count=%s request_bytes=%s error_type=%s error=%s",
                self._protocol,
                self._endpoint,
                self._export_timeout_seconds,
                metric_count,
                request_bytes,
                type(exc).__name__,
                exc,
            )

    def _count_otlp_metrics(self, request: ExportMetricsServiceRequest) -> int:
        """
        Count OTLP metric families in one export request for logging purposes.

        Args:
            request: The populated OTLP metrics export payload.

        Returns:
            Number of OTLP Metric messages in the request.
        """
        count = 0
        for resource_metrics in request.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                count += len(scope_metrics.metrics)
        return count

    def _send_http_request(self, request: ExportMetricsServiceRequest) -> None:
        """
        Send one OTLP/HTTP metrics request.

        Args:
            request: The populated OTLP metrics export payload.
        """
        if self._http_session is None:
            return
        payload = request.SerializeToString()
        self._http_session.post(
            self._endpoint,
            data=payload,
            headers={"Content-Type": "application/x-protobuf"},
            timeout=self._export_timeout_seconds,
        ).raise_for_status()

    def _send_grpc_request(self, request: ExportMetricsServiceRequest) -> None:
        """
        Send one OTLP/gRPC metrics request.

        Args:
            request: The populated OTLP metrics export payload.
        """
        if self._grpc_stub is None:
            return
        self._grpc_stub.Export(request, timeout=self._export_timeout_seconds)

    def start(self) -> None:
        """
        Start the exporter-owned periodic background loop.

        The exporter keeps its own lifecycle so application startup can create the
        exporter once and let it handle periodic exports internally.
        """
        if not self._enabled:
            return
        if self._export_task is not None and not self._export_task.done():
            return
        try:
            self._export_task = asyncio.create_task(
                self._run_export_loop(),
                name="openviking-otel-metrics-export-loop",
            )
        except RuntimeError:
            logger.warning(
                "[OTelMetricExporter] periodic export loop not started: phase=start protocol=%s endpoint=%s reason=no running event loop",
                self._protocol,
                self._endpoint,
            )
            self._export_task = None

    async def _run_export_loop(self) -> None:
        """
        Run the periodic export loop until shutdown is requested.
        """
        interval_s = float(self._export_interval_ms) / 1000.0
        next_tick = time.monotonic() + interval_s
        while self._enabled:
            try:
                await self.export()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[OTelMetricExporter] periodic export loop iteration failed: phase=run_export_loop protocol=%s endpoint=%s error=%s",
                    self._protocol,
                    self._endpoint,
                    exc,
                )
            now = time.monotonic()
            next_tick, sleep_s = _compute_next_tick_and_sleep(
                now=now,
                interval_s=interval_s,
                next_tick=next_tick,
            )
            await asyncio.sleep(sleep_s)

    async def shutdown(self) -> None:
        """
        Stop the exporter loop and release transport resources.
        """
        self._enabled = False

        if self._export_task is not None:
            self._export_task.cancel()
            try:
                await self._export_task
            except asyncio.CancelledError:
                logger.info(
                    "[OTelMetricExporter] periodic export loop cancelled: phase=shutdown protocol=%s endpoint=%s",
                    self._protocol,
                    self._endpoint,
                )
            except Exception as exc:
                logger.warning(
                    "[OTelMetricExporter] periodic export loop shutdown failed: phase=shutdown protocol=%s endpoint=%s error=%s",
                    self._protocol,
                    self._endpoint,
                    exc,
                )
            self._export_task = None

        if self._http_session is not None:
            try:
                self._http_session.close()
            except Exception as exc:
                logger.warning(
                    "[OTelMetricExporter] http session close failed: phase=shutdown protocol=%s endpoint=%s error_type=%s error=%s",
                    self._protocol,
                    self._endpoint,
                    type(exc).__name__,
                    exc,
                )
            self._http_session = None

        if self._grpc_channel is not None:
            try:
                # grpc.Channel.close() is synchronous and returns None.
                self._grpc_channel.close()
            except Exception as exc:
                logger.warning(
                    "[OTelMetricExporter] grpc channel close failed: phase=shutdown protocol=%s endpoint=%s error_type=%s error=%s",
                    self._protocol,
                    self._endpoint,
                    type(exc).__name__,
                    exc,
                )
            self._grpc_channel = None
            self._grpc_stub = None
