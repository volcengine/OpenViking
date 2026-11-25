# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Operation spans must receive the telemetry summary before they end."""

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from openviking.telemetry import execution


def _install_span_exporter(monkeypatch) -> InMemorySpanExporter:
    """Route the telemetry module's spans to a private in-memory exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        execution,
        "otel_trace",
        SimpleNamespace(
            get_tracer=lambda name: provider.get_tracer(name),
            SpanKind=SpanKind,
        ),
    )
    return exporter


async def _succeed():
    return {"ok": True}


async def test_success_span_receives_status_and_telemetry_summary(monkeypatch):
    exporter = _install_span_exporter(monkeypatch)

    result = await execution.run_with_telemetry(
        operation="test.operation",
        telemetry=True,
        fn=_succeed,
    )

    assert result.result == {"ok": True}
    [span] = exporter.get_finished_spans()
    assert span.attributes["operation"] == "test.operation"
    assert span.attributes["status"] == "ok"
    assert span.attributes["telemetry_id"]
    assert [event.name for event in span.events] == ["telemetry_summary"]
    assert span.status.status_code is StatusCode.UNSET


async def test_error_span_receives_error_status_exception_and_summary(monkeypatch):
    exporter = _install_span_exporter(monkeypatch)

    async def _fail():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await execution.run_with_telemetry(
            operation="test.operation",
            telemetry=True,
            fn=_fail,
        )

    [span] = exporter.get_finished_spans()
    assert span.attributes["status"] == "error"
    assert span.status.status_code is StatusCode.ERROR
    assert [event.name for event in span.events] == ["telemetry_summary", "exception"]
