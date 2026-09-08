# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.telemetry import tracer_module


class _RecordingSpan:
    def __init__(self, *, end_time=None):
        self.end_time = end_time
        self.events = []
        self.attributes = {}

    def add_event(self, name, attributes=None):
        self.events.append((name, attributes))

    def set_attribute(self, key, value):
        self.attributes[key] = value


class _SpanContext:
    def __init__(self, span):
        self.span = span

    def __enter__(self):
        return self.span

    def __exit__(self, *_args):
        return None


def _enable_tracing(monkeypatch, span):
    sdk_tracer = SimpleNamespace(
        start_as_current_span=lambda **_kwargs: _SpanContext(span),
    )
    monkeypatch.setattr(tracer_module, "_otel_tracer", sdk_tracer)
    monkeypatch.setattr(
        tracer_module,
        "otel_trace",
        SimpleNamespace(get_current_span=lambda: span),
    )


@pytest.fixture(autouse=True)
def _reset_content_capture(monkeypatch):
    monkeypatch.setattr(tracer_module, "_trace_capture_content", False)
    monkeypatch.setattr(tracer_module, "_trace_content_max_length", 4096)


def test_info_uses_stable_name_and_omits_message_by_default(monkeypatch):
    span = _RecordingSpan()
    _enable_tracing(monkeypatch, span)

    tracer_module.tracer.info(
        "Skipping unresolved memory operation: events(page_id=102): PROMPT_SECRET_17"
    )

    assert len(span.events) == 1
    name, attributes = span.events[0]
    assert name == "openviking.log"
    assert "openviking.log.message" not in attributes
    assert "page_id=102" not in repr(attributes)
    assert "PROMPT_SECRET_17" not in repr(attributes)
    assert attributes["code.namespace"].endswith("test_tracer_events")
    assert attributes["code.function.name"].endswith(
        "test_info_uses_stable_name_and_omits_message_by_default"
    )
    assert type(attributes["code.line.number"]) is int


def test_content_capture_redacts_and_bounds_message(monkeypatch):
    span = _RecordingSpan()
    _enable_tracing(monkeypatch, span)
    tracer_module._configure_trace_content(True, 80)

    tracer_module.tracer.info(
        'api_key="KEY_SECRET_19" Authorization: Bearer BEARER_SECRET_23 '
        "image=data:image/png;base64,IMAGE_SECRET_29 " + "x" * 100
    )

    name, attributes = span.events[0]
    message = attributes["openviking.log.message"]
    assert name == "openviking.log"
    assert len(message) == 80
    assert message.endswith("...[truncated]")
    assert "KEY_SECRET_19" not in message
    assert "BEARER_SECRET_23" not in message
    assert "IMAGE_SECRET_29" not in message


def test_add_event_preserves_attribute_types(monkeypatch):
    span = _RecordingSpan()
    _enable_tracing(monkeypatch, span)

    tracer_module.add_event(
        "gen_ai.client.inference.operation.details",
        {
            "gen_ai.usage.input_tokens": 17,
            "gen_ai.usage.output_tokens": 5,
            "openviking.cached": 3,
            "openviking.success": True,
        },
    )

    assert span.events == [
        (
            "gen_ai.client.inference.operation.details",
            {
                "gen_ai.usage.input_tokens": 17,
                "gen_ai.usage.output_tokens": 5,
                "openviking.cached": 3,
                "openviking.success": True,
            },
        )
    ]
    assert type(span.events[0][1]["gen_ai.usage.input_tokens"]) is int
    assert span.events[0][1]["openviking.success"] is True


def test_decorator_arguments_and_result_are_content_opt_in(monkeypatch):
    span = _RecordingSpan()
    _enable_tracing(monkeypatch, span)

    @tracer_module.tracer("test.decorated", ignore_args=False, ignore_result=False)
    def decorated(value, *, metadata):
        return f"RESULT_SECRET_31:{value}:{metadata}"

    result = decorated("ARG_SECRET_37", metadata="KWARG_SECRET_41")

    assert result == "RESULT_SECRET_31:ARG_SECRET_37:KWARG_SECRET_41"
    assert span.attributes == {}
    assert span.events[0][0] == "openviking.log"
    assert "openviking.log.message" not in span.events[0][1]
    assert "SECRET" not in repr((span.attributes, span.events))


def test_decorator_content_capture_is_redacted_and_bounded(monkeypatch):
    span = _RecordingSpan()
    _enable_tracing(monkeypatch, span)
    tracer_module._configure_trace_content(True, 48)

    @tracer_module.tracer("test.decorated", ignore_args=False, ignore_result=False)
    def decorated(value):
        return value

    decorated("api_key=DECORATOR_SECRET_43 " + "x" * 80)

    assert "DECORATOR_SECRET_43" not in span.attributes["func_args"]
    assert len(span.attributes["func_args"]) == 48
    message = span.events[0][1]["openviking.log.message"]
    assert "DECORATOR_SECRET_43" not in message
    assert len(message) == 48


def test_info_console_logging_is_unchanged_when_tracing_is_disabled(monkeypatch):
    messages = []
    fake_logger = SimpleNamespace(
        opt=lambda **_kwargs: SimpleNamespace(info=messages.append),
    )
    monkeypatch.setattr(tracer_module, "logger", fake_logger)
    monkeypatch.setattr(tracer_module, "_otel_tracer", None)

    tracer_module.tracer.info("still visible", console=True)

    assert messages == ["still visible"]


def test_init_from_server_config_forwards_content_settings(monkeypatch):
    captured = {}
    trace_config = SimpleNamespace(
        enabled=True,
        endpoint="",
        service_name="test",
        protocol="local",
        tls=SimpleNamespace(insecure=False),
        headers={},
        local_path="trace.jsonl",
        local_rotation_mb=40,
        local_backup_count=2,
        capture_content=True,
        content_max_length=1234,
    )
    server_config = SimpleNamespace(
        observability=SimpleNamespace(traces=trace_config),
    )
    monkeypatch.setattr(
        tracer_module,
        "init_tracer",
        lambda **kwargs: captured.update(kwargs) or "tracer",
    )

    result = tracer_module.init_tracer_from_server_config(server_config)

    assert result == "tracer"
    assert captured["capture_content"] is True
    assert captured["content_max_length"] == 1234


def test_disabled_trace_config_disables_content_capture(monkeypatch):
    monkeypatch.setattr(tracer_module, "_trace_capture_content", True)
    server_config = SimpleNamespace(
        observability=SimpleNamespace(traces=SimpleNamespace(enabled=False))
    )

    assert tracer_module.init_tracer_from_server_config(server_config) is None
    assert tracer_module._trace_capture_content is False


def test_invalid_content_limit_fails_before_exporter_creation(monkeypatch):
    exporter_calls = []
    monkeypatch.setattr(
        tracer_module,
        "OTLPGrpcSpanExporter",
        lambda **kwargs: exporter_calls.append(kwargs) or object(),
    )

    result = tracer_module.init_tracer(
        endpoint="localhost:4317",
        service_name="test",
        content_max_length=0,
    )

    assert result is None
    assert exporter_calls == []
    assert tracer_module._trace_capture_content is False
