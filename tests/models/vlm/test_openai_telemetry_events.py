# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

try:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
except ImportError:
    from opentelemetry.sdk.trace.export import InMemorySpanExporter

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.telemetry import tracer_module


@pytest.mark.asyncio
async def test_async_completion_exports_typed_metadata_without_content(monkeypatch):
    sentinels = {
        "message": "MESSAGE_SECRET_47",
        "tool": "TOOL_SECRET_53",
        "body": "BODY_SECRET_59",
        "output": "OUTPUT_SECRET_61",
    }
    usage = SimpleNamespace(
        prompt_tokens=17,
        completion_tokens=5,
        total_tokens=22,
        prompt_tokens_details=SimpleNamespace(cached_tokens=3, cache_write_tokens=6),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=4),
    )
    response = SimpleNamespace(
        id="chatcmpl-safe-id",
        model="gpt-safe-response-model",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=sentinels["output"], tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=usage,
    )
    received = {}

    async def create(**kwargs):
        received.update(kwargs)
        return response

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-safe-request-model",
            "extra_request_body": {"metadata": sentinels["body"]},
        }
    )
    monkeypatch.setattr(vlm, "get_async_client", lambda: client)
    monkeypatch.setattr(vlm, "update_token_usage", lambda **_kwargs: None)

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracer_module, "_otel_tracer", provider.get_tracer("test"))
    monkeypatch.setattr(tracer_module, "_trace_capture_content", False)

    result = await vlm.get_completion_async(
        messages=[{"role": "user", "content": sentinels["message"]}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": sentinels["tool"],
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    assert result.content == sentinels["output"]
    assert received["messages"][0]["content"] == sentinels["message"]
    assert received["tools"][0]["function"]["description"] == sentinels["tool"]
    assert received["extra_body"]["metadata"] == sentinels["body"]

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "openai.vlm.call"
    assert all(
        event.name in {"openviking.log", "gen_ai.client.inference.operation.details"}
        for event in span.events
    )

    gen_ai_events = [
        event for event in span.events if event.name == "gen_ai.client.inference.operation.details"
    ]
    assert len(gen_ai_events) == 1
    attributes = gen_ai_events[0].attributes
    assert dict(attributes) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": "gpt-safe-request-model",
        "gen_ai.response.id": "chatcmpl-safe-id",
        "gen_ai.response.model": "gpt-safe-response-model",
        "gen_ai.response.finish_reasons": ("stop",),
        "gen_ai.usage.input_tokens": 17,
        "gen_ai.usage.output_tokens": 5,
        "gen_ai.usage.cache_creation.input_tokens": 6,
        "gen_ai.usage.cache_read.input_tokens": 3,
        "gen_ai.usage.reasoning.output_tokens": 4,
    }
    assert type(attributes["gen_ai.usage.input_tokens"]) is int
    assert type(attributes["gen_ai.usage.output_tokens"]) is int

    exported = repr((span.attributes, tuple(span.events)))
    for sentinel in sentinels.values():
        assert sentinel not in exported
    assert "response.usage=" not in exported


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("openai", "openai"), ("azure", "azure.ai.openai")],
)
def test_provider_name_uses_semantic_convention_value(monkeypatch, provider, expected):
    events = []
    monkeypatch.setattr(
        tracer_module.tracer,
        "add_event",
        lambda name, attributes=None: events.append((name, attributes)),
    )
    vlm = OpenAIVLM({"provider": provider, "model": "test-model"})

    vlm._record_inference_event(SimpleNamespace())

    assert events == [
        (
            "gen_ai.client.inference.operation.details",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": expected,
                "gen_ai.request.model": "test-model",
            },
        )
    ]


def test_zero_token_counts_are_preserved(monkeypatch):
    events = []
    monkeypatch.setattr(
        tracer_module.tracer,
        "add_event",
        lambda name, attributes=None: events.append((name, attributes)),
    )
    vlm = OpenAIVLM({"provider": "openai", "model": "test-model"})
    monkeypatch.setattr(vlm, "update_token_usage", lambda **_kwargs: None)

    vlm._update_token_usage_from_response(
        SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=0,
                completion_tokens=0,
                prompt_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
            )
        )
    )

    attributes = events[0][1]
    assert attributes["gen_ai.usage.input_tokens"] == 0
    assert attributes["gen_ai.usage.output_tokens"] == 0
    assert attributes["gen_ai.usage.cache_creation.input_tokens"] == 0
    assert attributes["gen_ai.usage.cache_read.input_tokens"] == 0
    assert attributes["gen_ai.usage.reasoning.output_tokens"] == 0
