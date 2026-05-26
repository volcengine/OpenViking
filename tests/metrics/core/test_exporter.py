# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest

from openviking.metrics.exporters.otel import OTelMetricExporter, _compute_next_tick_and_sleep


def test_exporter_outputs_help_type_and_empty_metrics(registry, render_prometheus):
    registry.counter("openviking_empty_counter_total")
    registry.gauge("openviking_empty_gauge")
    registry.histogram("openviking_empty_histogram_seconds")
    text = render_prometheus(registry)
    assert "# TYPE openviking_empty_counter_total counter" in text
    assert "openviking_empty_counter_total 0" in text
    assert "# TYPE openviking_empty_histogram_seconds histogram" in text
    assert "openviking_empty_histogram_seconds_count 0" in text
    assert 'openviking_empty_histogram_seconds_bucket{le="+Inf"} 0' in text


@pytest.mark.asyncio
async def test_otel_exporter_start_and_shutdown_manage_background_loop(registry, monkeypatch):
    """OTel exporter should own its periodic export loop lifecycle."""
    exporter = OTelMetricExporter(registry=registry, enabled=False, export_interval_ms=10)
    exporter._enabled = True

    calls = 0
    tick = asyncio.Event()

    async def _fake_export():
        nonlocal calls
        calls += 1
        tick.set()
        return ""

    monkeypatch.setattr(exporter, "export", _fake_export)

    exporter.start()
    await asyncio.wait_for(tick.wait(), timeout=0.2)
    assert calls >= 1
    assert exporter._export_task is not None

    task_before = exporter._export_task
    exporter.start()
    assert exporter._export_task is task_before

    await exporter.shutdown()
    assert exporter._export_task is None


@pytest.mark.asyncio
async def test_otel_export_loop_aligns_to_next_fixed_tick_after_overrun():
    """Scheduling helper should advance next tick and compute sleep after one interval overrun."""
    next_tick, sleep_s = _compute_next_tick_and_sleep(now=1.15, interval_s=1.0, next_tick=1.0)
    assert next_tick == pytest.approx(2.0)
    assert sleep_s == pytest.approx(0.85)


def test_otel_http_send_uses_export_timeout_not_refresh_deadline(registry):
    """HTTP transport timeout should be decoupled from collector refresh timeout."""
    exporter = OTelMetricExporter(
        registry=registry,
        enabled=False,
        protocol="http",
        endpoint="http://localhost:4318/v1/metrics",
        refresh_deadline_seconds=1.0,
    )

    class _FakeSession:
        def __init__(self):
            self.timeout = None

        def post(self, _url, **kwargs):
            self.timeout = kwargs.get("timeout")

            class _Resp:
                @staticmethod
                def raise_for_status():
                    return None

            return _Resp()

    fake_session = _FakeSession()
    exporter._http_session = fake_session

    request = exporter._build_export_request()
    assert request is None

    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )

    exporter._send_http_request(ExportMetricsServiceRequest())

    assert fake_session.timeout == 5.0


def test_otel_http_send_forwards_custom_headers(registry):
    """HTTP transport should merge vendor auth headers with protobuf content type."""
    exporter = OTelMetricExporter(
        registry=registry,
        enabled=False,
        protocol="http",
        endpoint="https://apmplus-cn-beijing.ivolces.com/api/otlp/v1/metrics",
        headers={"X-ByteAPM-AppKey": "metric-appkey"},
    )

    class _FakeSession:
        def __init__(self):
            self.headers = None

        def post(self, _url, **kwargs):
            self.headers = kwargs.get("headers")

            class _Resp:
                @staticmethod
                def raise_for_status():
                    return None

            return _Resp()

    exporter._http_session = _FakeSession()

    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )

    exporter._send_http_request(ExportMetricsServiceRequest())

    assert exporter._http_session.headers == {
        "Content-Type": "application/x-protobuf",
        "X-ByteAPM-AppKey": "metric-appkey",
    }


def test_otel_grpc_send_forwards_custom_metadata(registry):
    """gRPC transport should forward vendor auth headers as metadata pairs."""
    exporter = OTelMetricExporter(
        registry=registry,
        enabled=False,
        protocol="grpc",
        endpoint="apmplus-cn-beijing.ivolces.com:4317",
        headers={"x-byteapm-appkey": "metric-appkey"},
    )

    class _FakeStub:
        def __init__(self):
            self.metadata = None
            self.timeout = None

        def Export(self, _request, **kwargs):
            self.metadata = kwargs.get("metadata")
            self.timeout = kwargs.get("timeout")
            return None

    exporter._grpc_stub = _FakeStub()

    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )

    exporter._send_grpc_request(ExportMetricsServiceRequest())

    assert exporter._grpc_stub.metadata == [("x-byteapm-appkey", "metric-appkey")]
    assert exporter._grpc_stub.timeout == 5.0
