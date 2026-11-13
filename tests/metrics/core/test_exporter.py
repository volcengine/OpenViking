# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread

import pytest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

from openviking.metrics.exporters.otel import OTelMetricExporter


def test_exporter_outputs_help_type_and_empty_metrics(registry, render_prometheus):
    registry.set_counter("openviking_empty_counter_total", 0)
    registry.set_gauge("openviking_empty_gauge", 0)
    registry.set_histogram(
        "openviking_empty_histogram_seconds",
        bucket_bounds=(1,),
        bucket_counts=(0, 0),
        count=0,
        value_sum=0,
    )
    text = render_prometheus(registry)
    assert "# TYPE openviking_empty_counter_total counter" in text
    assert "openviking_empty_counter_total 0.0" in text
    assert "# TYPE openviking_empty_histogram_seconds histogram" in text
    assert 'le="+Inf"} 0.0' in text


@pytest.mark.asyncio
async def test_otel_exporter_start_and_shutdown_manage_background_loop(registry):
    """Exercise the real SDK HTTP exporter and wait for its in-flight send at shutdown."""
    received, release = Event(), Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, self.headers, ExportMetricsServiceRequest.FromString(body)))
            received.set()
            release.wait(5)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    registry.inc_counter("openviking_operation_requests_total", amount=2)
    registry.observe_histogram("openviking_operation_duration_seconds", 60.123)
    exporter = OTelMetricExporter(
        registry=registry,
        protocol="http",
        endpoint=f"http://127.0.0.1:{server.server_port}/v1/metrics",
        service_name="metrics-contract",
        headers={"X-ByteAPM-AppKey": "test-app-key"},
    )
    stopping = None
    try:
        exporter.start()
        exporter.start()
        assert await asyncio.to_thread(received.wait, 2)
        stopping = asyncio.create_task(exporter.shutdown())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(stopping), 0.05)
        release.set()
        await asyncio.wait_for(stopping, 2)
        await exporter.export()
        assert len(requests) == 1
        path, headers, request = requests[0]
        assert path == "/v1/metrics"
        assert headers["X-ByteAPM-AppKey"] == "test-app-key"
        assert headers["Content-Type"] == "application/x-protobuf"
        resource = request.resource_metrics[0]
        assert {item.key: item.value.string_value for item in resource.resource.attributes} == {
            "service.name": "metrics-contract"
        }
        metrics = {metric.name: metric for metric in resource.scope_metrics[0].metrics}
        assert metrics["openviking_operation_requests_total"].sum.data_points[0].as_double == 2
        point = metrics["openviking_operation_duration_seconds"].histogram.data_points[0]
        assert point.count == 1 and point.sum == pytest.approx(60.123)
        assert point.bucket_counts[-1] == 0
    finally:
        release.set()
        if stopping is not None:
            await stopping
        await exporter.shutdown()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)
