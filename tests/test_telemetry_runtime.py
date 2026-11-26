# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult, embed_compat
from openviking.models.vlm.base import VLMBase
from openviking.observability.context import (
    get_root_observability_context,
)
from openviking.storage.collection_schemas import TextEmbeddingHandler
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_executor import SemanticTreeStats
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.telemetry import (
    get_current_telemetry,
    register_telemetry,
    tracer_module,
    unregister_telemetry,
)
from openviking.telemetry.backends.memory import MemoryOperationTelemetry
from openviking.telemetry.context import (
    bind_ingestion_source_type,
    bind_telemetry,
    bind_telemetry_stage,
    get_ingestion_source_type,
)
from openviking_cli.utils import logger as logger_module


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
async def test_ingestion_source_type_is_scoped_and_propagates_across_async_tasks():
    assert get_ingestion_source_type() is None

    with bind_ingestion_source_type("repository"):
        assert await asyncio.create_task(_read_ingestion_source_type()) == "repository"

    assert get_ingestion_source_type() is None


async def _read_ingestion_source_type() -> str | None:
    await asyncio.sleep(0)
    return get_ingestion_source_type()


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
    search = MemoryOperationTelemetry(
        operation="search.find",
        enabled=True,
    )
    search.count("vector.searches", 2)
    search.count("vector.scored", 5)
    search.count("vector.passed", 3)
    search.set("vector.returned", 2)
    search.count("vector.scanned", 5)
    search.set("vector.scan_reason", "")
    search.set("semantic_nodes.total", 4)
    search.set("semantic_nodes.done", 3)
    search.set("semantic_nodes.pending", 1)
    search.set("semantic_nodes.running", 0)
    search.set("memory.extracted", 6)

    snapshot = search.finish()
    assert snapshot is not None
    result = snapshot.summary

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

    telemetry = MemoryOperationTelemetry(operation="session.commit", enabled=True)
    telemetry.record_token_usage("llm", 5, 3)
    telemetry.set("memory.extracted", 4)

    snapshot = telemetry.finish()
    assert snapshot is not None
    summary = snapshot.summary

    assert summary["memory"] == {"extracted": 4}
    assert "queue" not in summary
    assert "vector" not in summary
    assert "semantic_nodes" not in summary
    assert "errors" not in summary


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


@pytest.mark.parametrize(
    "protocol,endpoint,exporter_name,transport_options",
    [
        ("grpc", "localhost:4317", "OTLPGrpcSpanExporter", {"insecure": True}),
        ("http", "http://localhost:4318/v1/traces", "OTLPHttpSpanExporter", {}),
    ],
    ids=["grpc", "http"],
)
def test_init_tracer_forwards_headers_and_exports_span(
    monkeypatch, protocol, endpoint, exporter_name, transport_options
):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    sink = InMemorySpanExporter()
    exporter = Mock(return_value=sink)
    providers: list[TracerProvider] = []
    monkeypatch.setattr(tracer_module, exporter_name, exporter)
    monkeypatch.setattr(tracer_module, "_otel_tracer", None)
    monkeypatch.setattr(tracer_module, "_propagator", None)
    monkeypatch.setattr(tracer_module.otel_trace, "set_tracer_provider", providers.append)
    monkeypatch.setattr(
        tracer_module.otel_trace, "get_tracer", lambda name: providers[0].get_tracer(name)
    )
    monkeypatch.setattr(tracer_module, "_setup_logging", lambda: None)
    monkeypatch.setattr(tracer_module, "_init_asyncio_instrumentation", lambda: None)
    headers = {"x-byteapm-appkey": "trace-test-key"}

    try:
        tracer = tracer_module.init_tracer(
            endpoint=endpoint,
            service_name="memorydb",
            protocol=protocol,
            insecure=True,
            headers=headers,
        )
        assert tracer is not None
        exporter.assert_called_once_with(endpoint=endpoint, headers=headers, **transport_options)
        with tracer.start_as_current_span("test-operation"):
            pass
        assert providers[0].force_flush(timeout_millis=1000)
        (span,) = sink.get_finished_spans()
        assert span.name == "test-operation"
        assert span.resource.attributes["service.name"] == "memorydb"
    finally:
        for provider in providers:
            provider.shutdown()


@pytest.mark.parametrize(
    ("protocol", "endpoint", "exporter_name", "transport_options"),
    [
        ("grpc", "localhost:4317", "OTLPGrpcLogExporter", {"insecure": True}),
        ("http", "http://localhost:4318/v1/logs", "OTLPHttpLogExporter", {}),
    ],
    ids=["grpc", "http"],
)
def test_init_otel_log_handler_forwards_headers(
    monkeypatch, protocol, endpoint, exporter_name, transport_options
):
    import logging

    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter

    exporter = InMemoryLogRecordExporter()
    exporter_factory = Mock(return_value=exporter)
    providers: list[LoggerProvider] = []
    monkeypatch.setattr(logger_module, "_otel_log_handler_initialized", False)
    monkeypatch.setattr(logger_module, "_otel_log_handler", None)
    monkeypatch.setattr(logger_module, exporter_name, exporter_factory)
    monkeypatch.setattr(logger_module, "set_logger_provider", providers.append)
    headers = {"x-byteapm-appkey": "test-log-key"}

    try:
        handler = logger_module.init_otel_log_handler(
            protocol=protocol,
            endpoint=endpoint,
            service_name="memorydb",
            insecure=True,
            headers=headers,
        )

        assert handler is not None
        exporter_factory.assert_called_once_with(
            endpoint=endpoint, headers=headers, **transport_options
        )
        record = logging.LogRecord(
            "openviking.test", logging.ERROR, __file__, 1, "test log", (), None
        )
        record.api_key = "private-key"
        handler.handle(record)
        assert providers[0].force_flush(timeout_millis=1000)
        (exported,) = (
            item for item in exporter.get_finished_logs() if item.log_record.body == "test log"
        )
        assert exported.log_record.attributes is not None
        assert "api_key" not in exported.log_record.attributes
        assert exported.resource.attributes["service.name"] == "memorydb"
    finally:
        for provider in providers:
            provider.shutdown()


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
