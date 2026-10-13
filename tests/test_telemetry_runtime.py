# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult, embed_compat
from openviking.models.vlm.base import VLMBase
from openviking.observability.context import (
    bind_operation_observability_context,
    bind_root_observability_context,
    get_operation_observability_context,
    get_root_observability_context,
    reset_operation_observability_context,
    reset_root_observability_context,
)
from openviking.storage.collection_schemas import TextEmbeddingHandler
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_executor import SemanticTreeStats
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.telemetry import execution as telemetry_execution
from openviking.telemetry import (
    get_current_telemetry,
    register_telemetry,
    tracer_module,
    unregister_telemetry,
)
from openviking.telemetry.backends.memory import MemoryOperationTelemetry
from openviking.telemetry.context import bind_telemetry, bind_telemetry_stage
from openviking.telemetry.snapshot import TelemetrySnapshot
from openviking.telemetry.span_models import OperationSpanAttributes, RootSpanAttributes
from openviking_cli.utils import logger as logger_module


def test_root_observability_context_bind_and_reset():
    root = RootSpanAttributes(http_method="GET", http_route="/demo", request_id="req-1")
    token = bind_root_observability_context(root)
    try:
        assert get_root_observability_context() is root
    finally:
        reset_root_observability_context(token)
    assert get_root_observability_context() is None


def test_operation_observability_context_bind_and_reset():
    operation = OperationSpanAttributes(operation="search.find", telemetry_id="tm-demo")
    token = bind_operation_observability_context(operation)
    try:
        assert get_operation_observability_context() is operation
    finally:
        reset_operation_observability_context(token)
    assert get_operation_observability_context() is None


def test_telemetry_snapshot_to_dict_supports_summary_only():
    snapshot = TelemetrySnapshot(
        telemetry_id="tm_demo",
        summary={"duration_ms": 1.2, "tokens": {"total": 3}},
    )

    payload = snapshot.to_dict(include_summary=True)

    assert payload == {
        "id": "tm_demo",
        "summary": {"duration_ms": 1.2, "tokens": {"total": 3}},
    }


def test_telemetry_summary_breaks_down_llm_and_embedding_token_usage():
    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=True)
    telemetry.record_token_usage("llm", 11, 7)
    telemetry.record_token_usage("embedding", 13, 0)

    summary = telemetry.finish().summary
    assert telemetry.telemetry_id
    assert telemetry.telemetry_id.startswith("tm_")
    assert summary["tokens"]["total"] == 31
    assert summary["duration_ms"] >= 0
    assert summary["tokens"]["llm"] == {
        "input": 11,
        "output": 7,
        "total": 18,
    }
    assert summary["tokens"]["embedding"] == {"total": 13}
    assert "queue" not in summary
    assert "vector" not in summary
    assert "semantic_nodes" not in summary
    assert "memory" not in summary
    assert "errors" not in summary


@pytest.fixture
def vlm_span_exporter(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    test_tracer = provider.get_tracer("openviking-vlm-test")
    monkeypatch.setattr(tracer_module, "_otel_tracer", test_tracer)
    monkeypatch.setattr(telemetry_execution.otel_trace, "get_tracer", lambda _name: test_tracer)
    yield exporter
    provider.shutdown()


def _chat_spans(exporter):
    return [s for s in exporter.get_finished_spans() if s.kind is otel_trace.SpanKind.CLIENT]


def _chat_response(text="private response", tokens=11):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=None, reasoning_content=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=tokens, completion_tokens=7, total_tokens=tokens + 7),
    )


def _traced_vlm(monkeypatch, backend="openai", **config):
    from openviking.models.vlm.backends import litellm_vlm
    from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
    from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM

    classes = {
        "openai": OpenAIVLM,
        "litellm": litellm_vlm.LiteLLMVLMProvider,
        "volcengine": VolcEngineVLM,
    }
    vlm = classes[backend](
        {
            "provider": backend,
            "model": "test-model",
            "api_key": "private-key",
            "max_retries": 0,
            **config,
        }
    )
    create = Mock(return_value=_chat_response())
    acreate = AsyncMock(return_value=_chat_response())
    if backend == "litellm":
        monkeypatch.setattr(litellm_vlm, "completion", create)
        monkeypatch.setattr(litellm_vlm, "acompletion", acreate)
    else:
        monkeypatch.setattr(
            vlm,
            "get_client",
            lambda: SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
        )
        monkeypatch.setattr(
            vlm,
            "get_async_client",
            lambda: SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=acreate))
            ),
        )
    return vlm, create, acreate


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["openai", "litellm", "volcengine"])
@pytest.mark.parametrize(
    "method",
    [
        "get_completion",
        "get_completion_async",
        "get_vision_completion",
        "get_vision_completion_async",
    ],
)
async def test_vlm_provider_spans_preserve_results_usage_and_operation_parent(
    monkeypatch,
    vlm_span_exporter,
    backend,
    method,
):
    vlm, _, _ = _traced_vlm(monkeypatch, backend)

    async def call():
        result = getattr(vlm, method)(prompt="private prompt")
        return await result if method.endswith("_async") else result

    execution = await telemetry_execution.run_with_telemetry(
        operation="session.commit",
        telemetry=True,
        fn=call,
    )
    assert execution.result == "private response"
    spans = _chat_spans(vlm_span_exporter)
    assert len(spans) == 1
    span = spans[0]
    parent = next(s for s in vlm_span_exporter.get_finished_spans() if s.name == "session.commit")
    assert span.parent.span_id == parent.context.span_id
    assert span.end_time >= span.start_time
    assert span.attributes == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": backend,
        "gen_ai.request.model": "test-model",
        "gen_ai.usage.input_tokens": 11,
        "gen_ai.usage.output_tokens": 7,
    }
    assert not span.events
    assert execution.telemetry["summary"]["tokens"]["llm"]["total"] == 18


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_model", [None, "", "custom-codex-model"])
@pytest.mark.parametrize(
    "method",
    [
        "get_completion",
        "get_completion_async",
        "get_vision_completion",
        "get_vision_completion_async",
    ],
)
async def test_codex_span_model_matches_actual_responses_request(
    monkeypatch, vlm_span_exporter, configured_model, method
):
    from openviking.models.vlm.backends.codex_vlm import CodexVLM

    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="private response")],
            )
        ],
        usage=SimpleNamespace(input_tokens=11, output_tokens=7, total_tokens=18),
    )
    create = Mock(
        side_effect=lambda **_kwargs: iter(
            [SimpleNamespace(type="response.completed", response=response)]
        )
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    vlm = CodexVLM({"model": configured_model, "api_key": "private-key", "max_retries": 0})
    monkeypatch.setattr(vlm, "_build_responses_client", lambda *_args: client)
    result = getattr(vlm, method)(prompt="private prompt")
    if method.endswith("_async"):
        result = await result
    assert result == "private response"
    request_model = create.call_args.kwargs["model"]
    assert request_model == (configured_model or "gpt-5.3-codex")
    spans = _chat_spans(vlm_span_exporter)
    assert len(spans) == 1
    assert spans[0].name == f"chat {request_model}"
    assert spans[0].attributes["gen_ai.request.model"] == request_model


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["openai", "litellm"])
async def test_vlm_known_default_model_matches_request(monkeypatch, vlm_span_exporter, backend):
    vlm, _, call = _traced_vlm(monkeypatch, backend, model=None)
    await vlm.get_completion_async("private prompt")
    assert call.call_args.kwargs["model"] == "gpt-4o-mini"
    assert _chat_spans(vlm_span_exporter)[0].attributes["gen_ai.request.model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_vlm_without_known_default_traces_unknown_model(vlm_span_exporter):
    from openviking.models.vlm.base import trace_vlm_call

    class UnknownBackend(VLMBase):
        def get_completion(self, **_kwargs):
            return "response"

        get_vision_completion = get_completion

        @trace_vlm_call
        async def get_completion_async(self, **_kwargs):
            return "response"

        get_vision_completion_async = get_completion_async

    assert await UnknownBackend({"provider": "custom"}).get_completion_async() == "response"
    span = _chat_spans(vlm_span_exporter)[0]
    assert span.name == "chat unknown"
    assert span.attributes["gen_ai.request.model"] == "unknown"


@pytest.mark.asyncio
async def test_vlm_retry_and_failover_span_cardinality(monkeypatch, vlm_span_exporter):
    from openviking.models.vlm.base import FailoverVLM

    class ProviderError(RuntimeError):
        status_code = 503

    async def no_delay(_delay):
        pass

    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", no_delay)
    vlm, _, call = _traced_vlm(monkeypatch, max_retries=1)
    call.side_effect = [ProviderError("HTTP 503 private provider body"), _chat_response()]
    assert await vlm.get_completion_async("private prompt") == "private response"
    assert call.await_count == 2
    assert len(_chat_spans(vlm_span_exporter)) == 1

    primary, _, call = _traced_vlm(monkeypatch, model="primary")
    error = ProviderError("HTTP 401 private provider body with private-key")
    error.status_code = 401
    call.side_effect = error
    with pytest.raises(ProviderError) as raised:
        await primary.get_completion_async("private prompt")
    assert raised.value is error
    backup, _, _ = _traced_vlm(monkeypatch, model="backup")
    assert (
        await FailoverVLM(primary, backup).get_completion_async("private prompt")
        == "private response"
    )
    spans = _chat_spans(vlm_span_exporter)
    assert [s.name for s in spans] == [
        "chat test-model",
        "chat primary",
        "chat primary",
        "chat backup",
    ]
    for span in spans[1:3]:
        assert span.status.status_code is otel_trace.StatusCode.ERROR
        assert span.attributes["error.type"] == "401"
        assert "private" not in span.to_json()


@pytest.mark.asyncio
async def test_concurrent_vlm_spans_keep_usage_isolated(monkeypatch, vlm_span_exporter):
    vlm, _, call = _traced_vlm(monkeypatch)
    ready, started = asyncio.Event(), 0

    async def reply(**kwargs):
        nonlocal started
        started += 1
        if started == 2:
            ready.set()
        await ready.wait()
        return _chat_response(tokens=len(kwargs["messages"][0]["content"]))

    call.side_effect = reply
    await asyncio.gather(vlm.get_completion_async("a"), vlm.get_completion_async("abcdef"))
    assert sorted(
        s.attributes["gen_ai.usage.input_tokens"] for s in _chat_spans(vlm_span_exporter)
    ) == [1, 6]


@pytest.mark.asyncio
async def test_cancelled_vlm_call_closes_span_and_preserves_cancellation(
    monkeypatch, vlm_span_exporter
):
    vlm, _, call = _traced_vlm(monkeypatch)
    started = asyncio.Event()

    async def wait(**_kwargs):
        started.set()
        await asyncio.Event().wait()

    call.side_effect = wait
    task = asyncio.create_task(vlm.get_completion_async("private prompt"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(_chat_spans(vlm_span_exporter)) == 1
    call.side_effect = None
    await vlm.get_completion_async("next request")
    assert len(_chat_spans(vlm_span_exporter)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", [False, True])
async def test_vlm_tracing_disabled_or_broken_preserves_provider_behavior(monkeypatch, broken):
    class BrokenSpan:
        def set_attribute(self, *_args):
            raise RuntimeError("trace write failed")

        record_exception = set_attribute
        set_status = set_attribute
        end = set_attribute

    monkeypatch.setattr(
        tracer_module,
        "_otel_tracer",
        SimpleNamespace(
            start_span=lambda *_args, **_kwargs: BrokenSpan(),
        )
        if broken
        else None,
    )
    vlm, _, call = _traced_vlm(monkeypatch)
    assert await vlm.get_completion_async("private prompt") == "private response"
    error = RuntimeError("original provider error")
    call.side_effect = error
    with pytest.raises(RuntimeError) as raised:
        await vlm.get_completion_async("private prompt")
    assert raised.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [
        "/Users/private/models/model.gguf",
        "provider//home/private/model.gguf",
        r"C:\private\model.gguf",
    ],
)
async def test_vlm_span_omits_local_model_paths_without_changing_requests(
    monkeypatch, vlm_span_exporter, model
):
    vlm, _, call = _traced_vlm(monkeypatch, model=model)
    await vlm.get_completion_async("private prompt")
    assert call.await_args.kwargs["model"] == model
    span = _chat_spans(vlm_span_exporter)[0]
    assert span.name == "chat local-model"
    assert "private" not in span.to_json()


@pytest.mark.asyncio
async def test_volcengine_media_span_includes_usage_and_preserves_cleanup(
    monkeypatch, tmp_path, vlm_span_exporter
):
    vlm, _, _ = _traced_vlm(monkeypatch, "volcengine", media={"enabled": True})
    path = tmp_path / "private-meeting.mp3"
    path.write_bytes(b"ID3-audio")
    client = SimpleNamespace(
        files=SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(id="private-file")),
            wait_for_processing=AsyncMock(return_value=SimpleNamespace(status="active")),
            delete=AsyncMock(),
        ),
        responses=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    id="response-1",
                    status="completed",
                    usage=SimpleNamespace(input_tokens=11, output_tokens=7),
                    output=[
                        SimpleNamespace(
                            type="message",
                            content=[SimpleNamespace(type="output_text", text="private response")],
                        )
                    ],
                )
            )
        ),
    )
    monkeypatch.setattr(vlm, "get_async_client", lambda: client)
    result = await vlm.get_media_completion_async(
        prompt="private prompt", media_path=path, filename=path.name, media_type="audio"
    )
    assert result == "private response"
    assert client.files.delete.await_count == 1
    span = _chat_spans(vlm_span_exporter)[0]
    assert span.attributes["gen_ai.usage.input_tokens"] == 11
    assert "private" not in span.to_json()


def test_telemetry_summary_breaks_down_stage_token_usage():
    telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
    telemetry.record_token_usage("embedding", 11, 0, stage="embed_query")
    telemetry.record_token_usage("rerank", 7, 0, stage="rerank")
    telemetry.record_token_usage("llm", 5, 3, stage="vlm")

    summary = telemetry.finish().summary

    assert summary["tokens"]["total"] == 26
    assert summary["tokens"]["rerank"] == {"total": 7}
    assert summary["tokens"]["stages"]["embed_query"]["embedding"] == {"total": 11}
    assert summary["tokens"]["stages"]["rerank"]["rerank"] == {"total": 7}
    assert summary["tokens"]["stages"]["vlm"]["llm"] == {
        "input": 5,
        "output": 3,
        "total": 8,
    }


@pytest.mark.asyncio
async def test_bind_telemetry_stage_propagates_across_async_tasks():
    telemetry = MemoryOperationTelemetry(operation="resource.process", enabled=True)

    async def _worker() -> None:
        await asyncio.sleep(0)
        get_current_telemetry().add_token_usage(6, 4)

    with bind_telemetry(telemetry):
        with bind_telemetry_stage("resource_summarize"):
            await asyncio.create_task(_worker())

    summary = telemetry.finish().summary
    assert summary["tokens"]["stages"]["resource_summarize"]["llm"] == {
        "input": 6,
        "output": 4,
        "total": 10,
    }


@pytest.mark.asyncio
async def test_embed_compat_binds_query_stage_for_embedding_tokens():
    telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
    query = " ".join(f"token-{idx}" for idx in range(200))

    class _TelemetryAwareAsyncEmbedder(DenseEmbedderBase):
        def __init__(self):
            super().__init__("telemetry-test", config={"max_input_tokens": 20})

        def embed(self, text: str, is_query: bool = False) -> EmbedResult:
            raise AssertionError("embed_async should be used")

        async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
            assert is_query is True
            assert text.endswith("...(truncated for embedding)")
            assert "token-199" not in text
            get_current_telemetry().record_token_usage("embedding", 9, 0)
            return EmbedResult(dense_vector=[0.1, 0.2])

        def get_dimension(self) -> int:
            return 2

    with bind_telemetry(telemetry):
        await embed_compat(_TelemetryAwareAsyncEmbedder(), query, is_query=True)

    summary = telemetry.finish().summary
    assert summary["tokens"]["stages"]["embed_query"]["embedding"] == {"total": 9}


def test_vlm_base_defaults_operation_tokens_to_vlm_stage():
    class _DummyVLM(VLMBase):
        def get_completion(self, *args, **kwargs):
            raise NotImplementedError()

        async def get_completion_async(self, *args, **kwargs):
            raise NotImplementedError()

        def get_vision_completion(self, *args, **kwargs):
            raise NotImplementedError()

        async def get_vision_completion_async(self, *args, **kwargs):
            raise NotImplementedError()

    telemetry = MemoryOperationTelemetry(operation="session.commit", enabled=True)
    with bind_telemetry(telemetry):
        _DummyVLM({"provider": "openai", "model": "gpt-4o-mini"}).update_token_usage(
            model_name="gpt-4o-mini",
            provider="openai",
            prompt_tokens=7,
            completion_tokens=5,
        )

    summary = telemetry.finish().summary
    assert summary["tokens"]["stages"]["vlm"]["llm"] == {
        "input": 7,
        "output": 5,
        "total": 12,
    }


def test_disabled_telemetry_still_has_request_id():
    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=False)

    assert telemetry.telemetry_id
    assert telemetry.telemetry_id.startswith("tm_")


def test_telemetry_summary_uses_simplified_internal_metric_keys():
    summary = MemoryOperationTelemetry(
        operation="search.find",
        enabled=True,
    )
    summary.count("vector.searches", 2)
    summary.count("vector.scored", 5)
    summary.count("vector.passed", 3)
    summary.set("vector.returned", 2)
    summary.count("vector.scanned", 5)
    summary.set("vector.scan_reason", "")
    summary.set("semantic_nodes.total", 4)
    summary.set("semantic_nodes.done", 3)
    summary.set("semantic_nodes.pending", 1)
    summary.set("semantic_nodes.running", 0)
    summary.set("memory.extracted", 6)

    result = summary.finish().summary

    assert result["vector"] == {
        "searches": 2,
        "scored": 5,
        "passed": 3,
        "returned": 2,
        "scanned": 5,
        "scan_reason": "",
    }
    assert result["semantic_nodes"] == {
        "total": 4,
        "done": 3,
        "pending": 1,
    }
    assert result["memory"] == {"extracted": 6}


def test_telemetry_summary_includes_cuvs_route_and_stage_timings():
    telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
    telemetry.record_cuvs_search(
        {
            "algorithm": "brute_force",
            "dtype": "float16",
            "max_concurrent_gpu_searches": 2,
            "auto_mode": True,
            "route_reason": "native_filter_threshold",
            "filter_kind": "path",
            "filter_cache_hit": True,
            "native_filter_reused": True,
            "build_performed": False,
            "eligible_count": 12,
            "records_generation": 3,
            "index_size": 1000,
            "memory_estimated_peak_bytes": 4096,
            "memory_free_bytes": 8192,
            "memory_usable_bytes": 6144,
            "total_ms": 1.25,
            "preflight_ms": 0.4,
            "native_search_ms": 0.7,
        }
    )

    cuvs = telemetry.finish().summary["vector"]["cuvs"]

    assert cuvs == {
        "searches": 1,
        "algorithms": {"brute_force": 1},
        "dtypes": {"float16": 1},
        "max_concurrent_gpu_searches": 2,
        "auto_mode_searches": 1,
        "routes": {"native_filter_threshold": 1},
        "filter_kinds": {"path": 1},
        "filter_cache_hits": 1,
        "native_filter_reuses": 1,
        "eligible_count_max": 12,
        "records_generation_max": 3,
        "index_size_max": 1000,
        "memory": {
            "estimated_peak_bytes_max": 4096,
            "free_bytes_min": 8192,
            "usable_bytes_min": 6144,
        },
        "timings_ms": {
            "total": {"sum": 1.25, "max": 1.25},
            "preflight": {"sum": 0.4, "max": 0.4},
            "native_search": {"sum": 0.7, "max": 0.7},
        },
    }


def test_telemetry_summary_includes_cuvs_micro_batching_fields():
    telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
    for batch_size, batch_wait_ms in ((4, 0.8), (4, 0.7), (1, 1.0)):
        telemetry.record_cuvs_search(
            {
                "algorithm": "brute_force",
                "dtype": "float32",
                "route_reason": "cuvs",
                "filter_kind": "none",
                "micro_batching_enabled": True,
                "micro_batching_warm_fast_path": batch_size == 4,
                "batch_size": batch_size,
                "batch_wait_ms": batch_wait_ms,
            }
        )

    cuvs = telemetry.finish().summary["vector"]["cuvs"]

    assert cuvs["micro_batching_searches"] == 3
    assert cuvs["micro_batched_searches"] == 2
    assert cuvs["micro_batching_warm_fast_path_searches"] == 2
    assert cuvs["batch_size_max"] == 4
    assert cuvs["searches_by_batch_size"] == {"1": 1, "4": 2}
    assert cuvs["timings_ms"]["batch_wait"] == {"sum": 2.5, "max": 1.0}


def test_cuvs_telemetry_aggregation_is_completion_order_independent():
    samples = [
        {
            "algorithm": "brute_force",
            "dtype": "float32",
            "max_concurrent_gpu_searches": 1,
            "auto_mode": False,
            "route_reason": "cuvs",
            "filter_kind": "none",
            "filter_cache_eviction_fallback": True,
            "filter_words_packed": True,
            "build_performed": True,
            "records_generation": 2,
            "index_size": 100,
            "memory_estimated_peak_bytes": 4000,
            "memory_free_bytes": 8000,
            "memory_usable_bytes": 7000,
            "total_ms": 12,
            "queue_ms": 2,
            "gpu_gate_queue_ms": 1.5,
            "build_ms": 5,
            "gpu_search_ms": 5,
        },
        {
            "algorithm": "brute_force",
            "dtype": "float32",
            "max_concurrent_gpu_searches": 2,
            "auto_mode": True,
            "route_reason": "native_filter_threshold",
            "filter_kind": "path",
            "filter_cache_hit": True,
            "native_filter_reused": True,
            "eligible_count": 3,
            "records_generation": 2,
            "index_size": 100,
            "memory_free_bytes": 6000,
            "memory_usable_bytes": 5000,
            "total_ms": 3,
            "preflight_ms": 1,
            "native_search_ms": 2,
        },
    ]

    def aggregate(order):
        telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
        for index in order:
            telemetry.record_cuvs_search(samples[index])
        return telemetry.finish().summary["vector"]["cuvs"]

    forward = aggregate([0, 1])
    reverse = aggregate([1, 0])

    assert forward == reverse
    assert forward["routes"] == {"cuvs": 1, "native_filter_threshold": 1}
    assert forward["builds"] == 1
    assert forward["filter_cache_eviction_fallbacks"] == 1
    assert forward["packed_filter_queries"] == 1
    assert forward["memory"]["free_bytes_min"] == 6000
    assert forward["timings_ms"]["total"] == {"sum": 15.0, "max": 12.0}
    assert forward["timings_ms"]["gpu_gate_queue"] == {"sum": 1.5, "max": 1.5}


def test_cuvs_telemetry_timing_sum_is_strictly_order_independent():
    # A large first value makes ordinary floating-point associativity loss observable.
    durations_ms = [10_000_000_000_000.0, 0.001, 0.001]

    def aggregate(order):
        telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
        for index in order:
            telemetry.record_cuvs_search(
                {
                    "algorithm": "brute_force",
                    "dtype": "float32",
                    "route_reason": "cuvs",
                    "filter_kind": "none",
                    "total_ms": durations_ms[index],
                }
            )
        return telemetry.finish().summary["vector"]["cuvs"]["timings_ms"]["total"]

    forward = aggregate([0, 1, 2])
    reverse = aggregate([2, 1, 0])

    assert (
        forward
        == reverse
        == {
            "sum": 10_000_000_000_000.002,
            "max": 10_000_000_000_000.0,
        }
    )


def test_cuvs_telemetry_aggregation_is_thread_safe():
    telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
    samples = [
        {
            "algorithm": "brute_force",
            "dtype": "float32",
            "route_reason": "cuvs",
            "filter_kind": "none",
            "memory_free_bytes": 8000,
            "total_ms": 0.001,
            "gpu_search_ms": 0.001,
        },
        {
            "algorithm": "cagra",
            "dtype": "float16",
            "route_reason": "native_filter_threshold",
            "filter_kind": "path",
            "memory_free_bytes": 6000,
            "total_ms": 0.002,
            "native_search_ms": 0.002,
        },
    ] * 100

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(telemetry.record_cuvs_search, samples))

    cuvs = telemetry.finish().summary["vector"]["cuvs"]

    assert cuvs["searches"] == 200
    assert cuvs["algorithms"] == {"brute_force": 100, "cagra": 100}
    assert cuvs["dtypes"] == {"float16": 100, "float32": 100}
    assert cuvs["routes"] == {"cuvs": 100, "native_filter_threshold": 100}
    assert cuvs["filter_kinds"] == {"none": 100, "path": 100}
    assert cuvs["memory"]["free_bytes_min"] == 6000
    assert cuvs["timings_ms"]["total"] == {"sum": 0.3, "max": 0.002}


def test_cuvs_telemetry_bounds_dimensions_and_prunes_unobserved_values():
    telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
    telemetry.record_cuvs_search(
        {
            "algorithm": "tenant-specific-algorithm",
            "dtype": "tenant-specific-dtype",
            "route_reason": "tenant/1234",
            "filter_kind": "tenant-specific-filter",
            "auto_mode": "false",
            "filter_cache_hit": "false",
            "native_filter_reused": "false",
            "build_performed": "false",
            "eligible_count": None,
            "memory_estimated_peak_bytes": float("inf"),
            "total_ms": float("nan"),
            "gpu_search_ms": -1,
        }
    )

    cuvs = telemetry.finish().summary["vector"]["cuvs"]

    assert cuvs == {
        "searches": 1,
        "algorithms": {"other": 1},
        "dtypes": {"other": 1},
        "max_concurrent_gpu_searches": 1,
        "routes": {"other": 1},
        "filter_kinds": {"other": 1},
    }


def test_cuvs_telemetry_distinguishes_zero_memory_from_unobserved_memory():
    telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
    telemetry.record_cuvs_search(
        {
            "algorithm": "brute_force",
            "dtype": "float32",
            "route_reason": "native_memory_budget",
            "filter_kind": "none",
            "memory_free_bytes": 0,
            "memory_usable_bytes": 0,
        }
    )

    cuvs = telemetry.finish().summary["vector"]["cuvs"]

    assert cuvs["memory"] == {"free_bytes_min": 0, "usable_bytes_min": 0}
    assert "estimated_peak_bytes_max" not in cuvs["memory"]


def test_init_tracer_forwards_headers_to_grpc_exporter(monkeypatch):
    captured = {}

    class FakeExporter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(tracer_module, "OTLPGrpcSpanExporter", FakeExporter)
    monkeypatch.setattr(tracer_module, "BatchSpanProcessor", lambda exporter, **kwargs: exporter)

    class FakeTracerProvider:
        def __init__(self, resource=None):
            self.resource = resource

        def add_span_processor(self, _processor):
            return None

    monkeypatch.setattr(tracer_module, "TracerProvider", FakeTracerProvider)
    monkeypatch.setattr(
        tracer_module,
        "Resource",
        SimpleNamespace(create=lambda attrs: attrs),
    )
    monkeypatch.setattr(
        tracer_module,
        "otel_trace",
        SimpleNamespace(
            set_tracer_provider=lambda _provider: None,
            get_tracer=lambda service_name: f"tracer:{service_name}",
        ),
    )
    monkeypatch.setattr(
        tracer_module,
        "TraceContextTextMapPropagator",
        lambda: "propagator",
    )
    monkeypatch.setattr(tracer_module, "_setup_logging", lambda: None)
    monkeypatch.setattr(tracer_module, "_init_asyncio_instrumentation", lambda: None)

    tracer_module.init_tracer(
        endpoint="apmplus-cn-beijing.ivolces.com:4317",
        service_name="memorydb",
        protocol="grpc",
        insecure=True,
        headers={"x-byteapm-appkey": "trace-appkey"},
        enabled=True,
    )

    assert captured["endpoint"] == "apmplus-cn-beijing.ivolces.com:4317"
    assert captured["insecure"] is True
    assert captured["headers"] == {"x-byteapm-appkey": "trace-appkey"}


def test_init_tracer_forwards_headers_to_http_exporter(monkeypatch):
    captured = {}

    class FakeExporter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(tracer_module, "OTLPHttpSpanExporter", FakeExporter)
    monkeypatch.setattr(tracer_module, "BatchSpanProcessor", lambda exporter, **kwargs: exporter)

    class FakeTracerProvider:
        def __init__(self, resource=None):
            self.resource = resource

        def add_span_processor(self, _processor):
            return None

    monkeypatch.setattr(tracer_module, "TracerProvider", FakeTracerProvider)
    monkeypatch.setattr(
        tracer_module,
        "Resource",
        SimpleNamespace(create=lambda attrs: attrs),
    )
    monkeypatch.setattr(
        tracer_module,
        "otel_trace",
        SimpleNamespace(
            set_tracer_provider=lambda _provider: None,
            get_tracer=lambda service_name: f"tracer:{service_name}",
        ),
    )
    monkeypatch.setattr(
        tracer_module,
        "TraceContextTextMapPropagator",
        lambda: "propagator",
    )
    monkeypatch.setattr(tracer_module, "_setup_logging", lambda: None)
    monkeypatch.setattr(tracer_module, "_init_asyncio_instrumentation", lambda: None)

    tracer_module.init_tracer(
        endpoint="https://apmplus-cn-beijing.ivolces.com/api/otlp/v1/traces",
        service_name="memorydb",
        protocol="http",
        headers={"X-ByteAPM-AppKey": "trace-appkey"},
        enabled=True,
    )

    assert captured["endpoint"] == "https://apmplus-cn-beijing.ivolces.com/api/otlp/v1/traces"
    assert captured["headers"] == {"X-ByteAPM-AppKey": "trace-appkey"}


def test_init_otel_log_handler_forwards_headers_to_grpc_exporter(monkeypatch):
    captured = {}

    class FakeExporter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeLoggerProvider:
        def __init__(self, resource=None):
            self.resource = resource

        def add_log_record_processor(self, _processor):
            return None

    monkeypatch.setattr(logger_module, "_otel_log_handler_initialized", False)
    monkeypatch.setattr(logger_module, "_otel_log_handler", None)
    monkeypatch.setattr(logger_module, "OTLPGrpcLogExporter", FakeExporter)
    monkeypatch.setattr(
        logger_module,
        "BatchLogRecordProcessor",
        lambda exporter: exporter,
    )
    monkeypatch.setattr(logger_module, "LoggerProvider", FakeLoggerProvider)
    monkeypatch.setattr(
        logger_module,
        "LoggingHandler",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        logger_module,
        "Resource",
        SimpleNamespace(create=lambda attrs: attrs),
    )
    monkeypatch.setattr(logger_module, "set_logger_provider", lambda _provider: None)
    monkeypatch.setattr(
        logger_module,
        "get_logger",
        lambda _name: SimpleNamespace(
            info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None
        ),
    )

    handler = logger_module.init_otel_log_handler(
        protocol="grpc",
        endpoint="apmplus-cn-beijing.ivolces.com:4317",
        service_name="memorydb",
        insecure=True,
        headers={"x-byteapm-appkey": "log-appkey"},
        enabled=True,
    )

    assert handler is not None
    assert captured["endpoint"] == "apmplus-cn-beijing.ivolces.com:4317"
    assert captured["insecure"] is True
    assert captured["headers"] == {"x-byteapm-appkey": "log-appkey"}


def test_init_otel_log_handler_forwards_headers_to_http_exporter(monkeypatch):
    captured = {}

    class FakeExporter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeLoggerProvider:
        def __init__(self, resource=None):
            self.resource = resource

        def add_log_record_processor(self, _processor):
            return None

    monkeypatch.setattr(logger_module, "_otel_log_handler_initialized", False)
    monkeypatch.setattr(logger_module, "_otel_log_handler", None)
    monkeypatch.setattr(logger_module, "OTLPHttpLogExporter", FakeExporter)
    monkeypatch.setattr(
        logger_module,
        "BatchLogRecordProcessor",
        lambda exporter: exporter,
    )
    monkeypatch.setattr(logger_module, "LoggerProvider", FakeLoggerProvider)
    monkeypatch.setattr(
        logger_module,
        "LoggingHandler",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        logger_module,
        "Resource",
        SimpleNamespace(create=lambda attrs: attrs),
    )
    monkeypatch.setattr(logger_module, "set_logger_provider", lambda _provider: None)
    monkeypatch.setattr(
        logger_module,
        "get_logger",
        lambda _name: SimpleNamespace(
            info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None
        ),
    )

    handler = logger_module.init_otel_log_handler(
        protocol="http",
        endpoint="https://apmplus-cn-beijing.ivolces.com/api/otlp/v1/logs",
        service_name="memorydb",
        headers={"X-ByteAPM-AppKey": "log-appkey"},
        enabled=True,
    )

    assert handler is not None
    assert captured["endpoint"] == "https://apmplus-cn-beijing.ivolces.com/api/otlp/v1/logs"
    assert captured["headers"] == {"X-ByteAPM-AppKey": "log-appkey"}


def test_telemetry_summary_detects_groups_by_prefix_without_static_key_lists():
    telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
    telemetry.set("vector.debug_probe", 1)
    telemetry.set("queue.semantic.processed", 2)
    telemetry.set("memory.extracted", 1)

    result = telemetry.finish().summary

    assert "vector" in result
    assert "queue" in result
    assert "memory" in result


@pytest.mark.asyncio
async def test_semantic_processor_binds_registered_operation_telemetry(monkeypatch):
    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=True)
    register_telemetry(telemetry)

    resolver = SimpleNamespace(get_vlm=AsyncMock())
    processor = SemanticProcessor(vlm_resolver=resolver)

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def ls(self, uri, ctx=None):
            return []

    class _FakeTreeExecutor:
        stale = False

        def __init__(self, **kwargs):
            assert kwargs["processor"]._vlm_resolver is resolver
            assert kwargs["ctx"].account_id == "default"

        async def run(self, root_uri):
            assert get_current_telemetry() is telemetry
            get_current_telemetry().record_token_usage("llm", 11, 7)

        def get_stats(self):
            return SemanticTreeStats()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticTreeExecutor",
        lambda **kwargs: _FakeTreeExecutor(**kwargs),
    )

    try:
        outcome = await processor.on_dequeue(
            SemanticMsg(
                uri="viking://resources/demo",
                context_type="resource",
                recursive=False,
                propagate_to_parent=False,
                telemetry_id=telemetry.telemetry_id,
            ).to_dict()
        )
    finally:
        unregister_telemetry(telemetry.telemetry_id)

    assert outcome.outcome is ProcessOutcome.SUCCESS
    result = telemetry.finish()
    summary = result.summary
    assert summary["tokens"]["total"] == 18
    assert summary["tokens"]["llm"]["total"] == 18
    assert "embedding" not in summary["tokens"]


@pytest.mark.asyncio
async def test_semantic_processor_binds_metric_account_context(monkeypatch):
    resolver = SimpleNamespace(get_vlm=AsyncMock())
    processor = SemanticProcessor(vlm_resolver=resolver)
    ran = {"value": False}

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def ls(self, uri, ctx=None):
            return []

    class _FakeTreeExecutor:
        stale = False

        def __init__(self, **kwargs):
            assert kwargs["processor"]._vlm_resolver is resolver
            assert kwargs["ctx"].account_id == "acct-semantic"

        async def run(self, root_uri):
            ran["value"] = True
            root_context = get_root_observability_context()
            assert root_context is not None
            assert root_context.account_id == "acct-semantic"

        def get_stats(self):
            return SemanticTreeStats()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticTreeExecutor",
        lambda **kwargs: _FakeTreeExecutor(**kwargs),
    )

    outcome = await processor.on_dequeue(
        SemanticMsg(
            uri="viking://resources/demo",
            context_type="resource",
            recursive=False,
            propagate_to_parent=False,
            account_id="acct-semantic",
        ).to_dict()
    )
    assert outcome.outcome is ProcessOutcome.SUCCESS
    assert ran["value"] is True


@pytest.mark.asyncio
async def test_embedding_handler_binds_registered_operation_telemetry(monkeypatch):
    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=True)
    register_telemetry(telemetry)

    class _TelemetryAwareEmbedder:
        def prepare_embedding_input(self, content):
            return content

        def embed(self, text: str, is_query: bool = False) -> EmbedResult:
            assert text == "hello"
            assert is_query is False
            get_current_telemetry().record_token_usage("embedding", 9, 0)
            return EmbedResult(dense_vector=[0.1, 0.2])

        async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
            return self.embed(text, is_query=is_query)

    class _DummyVikingDB:
        is_closing = False
        uses_content_field = False

        async def account_uses_content_field(self, account_id):
            assert account_id == "default"
            return False

        async def upsert(self, _data, *, ctx=None, options=None):
            return "rec-1"

    provider = SimpleNamespace(bind=Mock(return_value=_TelemetryAwareEmbedder()))
    handler = TextEmbeddingHandler(
        _DummyVikingDB(),
        embedding_provider=provider,
    )
    payload = {
        "data": json.dumps(
            {
                "id": "msg-1",
                "message": "hello",
                "telemetry_id": telemetry.telemetry_id,
                "context_data": {
                    "id": "id-1",
                    "uri": "viking://resources/sample",
                    "account_id": "default",
                    "abstract": "sample",
                },
            }
        )
    }

    try:
        outcome = await handler.on_dequeue(payload)
    finally:
        unregister_telemetry(telemetry.telemetry_id)

    assert outcome.outcome is ProcessOutcome.SUCCESS
    provider.bind.assert_called_once_with("default")
    result = telemetry.finish()
    summary = result.summary
    assert summary["tokens"]["embedding"] == {"total": 9}


def test_telemetry_summary_includes_only_memory_group_when_memory_metrics_exist():
    telemetry = MemoryOperationTelemetry(operation="session.commit", enabled=True)
    telemetry.record_token_usage("llm", 5, 3)
    telemetry.set("memory.extracted", 4)

    summary = telemetry.finish().summary

    assert summary["memory"] == {"extracted": 4}
    assert "queue" not in summary
    assert "vector" not in summary
    assert "semantic_nodes" not in summary
    assert "errors" not in summary
