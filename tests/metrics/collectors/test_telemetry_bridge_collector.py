"""Module-level tests for `openviking.metrics.collectors.telemetry_bridge`."""

from __future__ import annotations

from openviking.metrics.collectors.telemetry_bridge import TelemetryBridgeCollector


def test_telemetry_bridge_collector_records_basic_operation_metrics(registry, render_prometheus):
    """TelemetryBridgeCollector must expand one telemetry summary into core operation metrics."""
    c = TelemetryBridgeCollector()
    c.receive(
        "telemetry.summary",
        {
            "summary": {
                "operation": "resource.process",
                "status": "ok",
                "duration_ms": 250.0,
                "tokens": {"total": 3},
            }
        },
        registry,
    )

    text = render_prometheus(registry)
    assert (
        'openviking_operation_requests_total{account_id="__unknown__",operation="resource.process",status="ok"} 1'
        in text
    )
    assert (
        'openviking_operation_tokens_total{account_id="__unknown__",operation="resource.process",token_type="all"} 3'
        in text
    )


def test_telemetry_bridge_collector_ignores_malformed_payloads_instead_of_raising(
    registry, render_prometheus
):
    """Missing/invalid `summary` payload must not raise; collector should ignore it best-effort."""
    c = TelemetryBridgeCollector()

    c.receive("telemetry.summary", {}, registry)
    c.receive("telemetry.summary", {"summary": "not-a-mapping"}, registry)

    text = render_prometheus(registry)
    assert "openviking_operation_requests_total" not in text
