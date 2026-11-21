# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import re

import pytest

from openviking.metrics.collectors.base import EventMetricCollector
from openviking.metrics.collectors.cache import CacheCollector
from openviking.metrics.collectors.embedding import EmbeddingCollector
from openviking.metrics.collectors.queue import QueueDurationCollector
from openviking.metrics.collectors.rerank import RerankCollector
from openviking.metrics.collectors.resource import ResourceIngestionCollector
from openviking.metrics.collectors.retrieval import RetrievalCollector
from openviking.metrics.collectors.session import SessionCollector
from openviking.metrics.collectors.telemetry_bridge import TelemetryBridgeCollector
from openviking.metrics.collectors.vlm import VLMCollector


class _DummyEventCollector(EventMetricCollector):
    SUPPORTED_EVENTS = frozenset({"demo.hit"})

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        self.seen.append(
            (
                str(event_name),
                str(payload["value"]),
                registry.collector_name() if hasattr(registry, "collector_name") else "registry",
            )
        )


def test_event_metric_collector_uses_supported_events_and_receive_hook(registry):
    collector = _DummyEventCollector()

    collector.receive("demo.hit", {"value": "ok"}, registry)
    collector.receive("demo.unknown", {"value": "skip"}, registry)

    assert collector.SUPPORTED_EVENTS == frozenset({"demo.hit"})
    assert collector.seen == [("demo.hit", "ok", "registry")]


def test_cache_collector_uses_supported_events_and_receive_hook_routing(
    registry, render_prometheus
):
    collector = CacheCollector()

    collector.receive("cache.hit", {"level": "L0"}, registry)
    collector.receive("cache.miss", {"level": "L1"}, registry)

    text = render_prometheus(registry)

    assert collector.SUPPORTED_EVENTS == frozenset({"cache.hit", "cache.miss"})
    assert 'openviking_cache_hits_total{level="L0"} 1' in text
    assert 'openviking_cache_misses_total{level="L1"} 1' in text


def test_queue_duration_collector_records_processing_and_end_to_end_histograms(
    registry, render_prometheus
):
    collector = QueueDurationCollector()

    collector.receive(
        "queue.processed",
        {
            "queue": "Semantic",
            "outcome": "success",
            "process_duration_seconds": 60.0,
            "end_to_end_duration_seconds": 404261.58497,
        },
        registry,
    )

    text = render_prometheus(registry)
    schema = re.search(r'bucket_schema="([0-9a-f]{16})"', text).group(1)
    labels = f'bucket_schema="{schema}",outcome="success",queue="Semantic"'
    assert f"openviking_queue_process_duration_seconds_count{{{labels}}} 1" in text
    assert f"openviking_queue_process_duration_seconds_sum{{{labels}}} 60.0" in text
    assert f"openviking_queue_end_to_end_duration_seconds_count{{{labels}}} 1" in text
    assert f"openviking_queue_end_to_end_duration_seconds_sum{{{labels}}} 404261.58497" in text
    assert (
        f'openviking_queue_end_to_end_duration_seconds_bucket{{bucket_schema="{schema}",'
        'le="500000.0",outcome="success",queue="Semantic"} 1' in text
    )


def test_queue_duration_collector_allows_processing_only_and_ignores_bad_payloads(
    registry, render_prometheus
):
    collector = QueueDurationCollector()
    collector.receive(
        "queue.processed",
        {
            "queue": "Embedding",
            "outcome": "requeued",
            "process_duration_seconds": 0.5,
        },
        registry,
    )
    collector.receive(
        "queue.processed",
        {
            "queue": "Embedding",
            "outcome": "unknown",
            "process_duration_seconds": 1.0,
        },
        registry,
    )
    collector.receive("queue.processed", {"queue": "Embedding"}, registry)
    collector.receive(
        "queue.processed",
        {
            "queue": "Embedding",
            "outcome": "success",
            "process_duration_seconds": float("nan"),
        },
        registry,
    )

    text = render_prometheus(registry)
    assert re.search(
        r'openviking_queue_process_duration_seconds_count\{bucket_schema="[0-9a-f]{16}",'
        r'outcome="requeued",queue="Embedding"\} 1',
        text,
    )
    assert "openviking_queue_end_to_end_duration_seconds" not in text
    assert 'outcome="unknown"' not in text


def test_resource_collector_records_connector_imports(registry, render_prometheus):
    ResourceIngestionCollector().receive(
        "resource.import",
        {
            "pipeline": "connector",
            "source_type": "repository",
            "account_id": "acct-code",
        },
        registry,
    )

    text = render_prometheus(registry)
    assert (
        'openviking_resource_imports_total{account_id="__unknown__",'
        'pipeline="connector",source_type="repository"} 1'
    ) in text


def test_session_collector_records_lifecycle_and_archive(registry, render_prometheus):
    c = SessionCollector()
    c.receive("session.lifecycle", {"action": "create", "status": "ok"}, registry)
    c.receive("session.archive", {"status": "ok"}, registry)

    text = render_prometheus(registry)
    assert (
        'openviking_session_lifecycle_total{account_id="__unknown__",action="create",status="ok"} 1'
        in text
    )
    assert 'openviking_session_archive_total{account_id="__unknown__",status="ok"} 1' in text


def test_session_collector_ignores_malformed_payloads_instead_of_raising(
    registry, render_prometheus
):
    c = SessionCollector()

    c.receive("session.lifecycle", "not-a-dict", registry)
    c.receive("session.lifecycle", {"action": "create"}, registry)
    c.receive("session.archive", {}, registry)

    text = render_prometheus(registry)
    assert "openviking_session_lifecycle_total" not in text
    assert "openviking_session_archive_total" not in text


def test_rerank_collector_maps_calls_duration_and_tokens():
    from openviking.metrics.core.registry import MetricRegistry
    from openviking.metrics.exporters.prometheus import PrometheusExporter

    registry = MetricRegistry()
    RerankCollector().receive(
        "rerank.call",
        {
            "provider": "cohere",
            "model_name": "rerank-v3.5",
            "duration_seconds": 0.3,
            "prompt_tokens": 9,
            "completion_tokens": 0,
        },
        registry,
    )
    text = PrometheusExporter(registry=registry).render()

    assert re.search(
        r'openviking_rerank_calls_total\{(?=[^}]*model_name="rerank-v3.5")(?=[^}]*provider="cohere")[^}]*\} 1(?:\.0)?',
        text,
    )
    assert re.search(
        r'openviking_rerank_call_duration_seconds_count\{(?=[^}]*model_name="rerank-v3.5")(?=[^}]*provider="cohere")[^}]*\} 1(?:\.0)?',
        text,
    )
    assert re.search(
        r'openviking_rerank_tokens_input_total\{(?=[^}]*model_name="rerank-v3.5")(?=[^}]*provider="cohere")[^}]*\} 9(?:\.0)?',
        text,
    )
    assert re.search(
        r'openviking_rerank_tokens_total\{(?=[^}]*model_name="rerank-v3.5")(?=[^}]*provider="cohere")[^}]*\} 9(?:\.0)?',
        text,
    )


def test_telemetry_bridge_collector_records_basic_operation_metrics(registry, render_prometheus):
    c = TelemetryBridgeCollector()
    c.receive(
        "telemetry.summary",
        {
            "summary": {
                "operation": "resource.process",
                "status": "ok",
                "duration_ms": 250.0,
                "tokens": {
                    "total": 10,
                    "llm": {"input": 3, "output": 2},
                    "embedding": {"total": 5},
                    "rerank": {"total": 4},
                    "stages": {
                        "embed_query": {"embedding": {"total": 5}},
                        "rerank": {"rerank": {"total": 4}},
                        "vlm": {"llm": {"input": 3, "output": 2, "total": 5}},
                    },
                },
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
        'openviking_operation_tokens_total{account_id="__unknown__",operation="resource.process",stage="vlm",token_type="llm_input"} 3'
        in text
    )
    assert (
        'openviking_operation_tokens_total{account_id="__unknown__",operation="resource.process",stage="vlm",token_type="llm_output"} 2'
        in text
    )
    assert (
        'openviking_operation_tokens_total{account_id="__unknown__",operation="resource.process",stage="embed_query",token_type="embedding"} 5'
        in text
    )
    assert (
        'openviking_operation_tokens_total{account_id="__unknown__",operation="resource.process",stage="rerank",token_type="rerank"} 4'
        in text
    )


def test_telemetry_bridge_collector_ignores_malformed_payloads_instead_of_raising(
    registry, render_prometheus
):
    c = TelemetryBridgeCollector()

    c.receive("telemetry.summary", {}, registry)
    c.receive("telemetry.summary", {"summary": "not-a-mapping"}, registry)

    text = render_prometheus(registry)
    assert "openviking_operation_requests_total" not in text


def test_embedding_collector_maps_call_metrics_and_tokens(registry, render_prometheus):
    """`embedding.call` must produce calls/duration/tokens metrics keyed by provider+model."""
    EmbeddingCollector().receive(
        "embedding.call",
        {
            "provider": "openai",
            "model_name": "text-embedding-3-large",
            "duration_seconds": 0.12,
            "prompt_tokens": 3,
            "completion_tokens": 2,
        },
        registry,
    )
    text = render_prometheus(registry)

    # Account dimension is supported for embedding families; default runtime resolves to __unknown__.
    assert re.search(
        r'openviking_embedding_calls_total\{(?=[^}]*account_id="__unknown__")(?=[^}]*error_code="OK")(?=[^}]*model_name="text-embedding-3-large")(?=[^}]*provider="openai")[^}]*\} 1(?:\.0)?',
        text,
    )
    assert re.search(
        r'openviking_embedding_call_duration_seconds_count\{(?=[^}]*account_id="__unknown__")(?=[^}]*error_code="OK")(?=[^}]*model_name="text-embedding-3-large")(?=[^}]*provider="openai")[^}]*\} 1(?:\.0)?',
        text,
    )
    assert re.search(
        r'openviking_embedding_tokens_input_total\{(?=[^}]*account_id="__unknown__")(?=[^}]*model_name="text-embedding-3-large")(?=[^}]*provider="openai")[^}]*\} 3(?:\.0)?',
        text,
    )
    assert re.search(
        r'openviking_embedding_tokens_output_total\{(?=[^}]*account_id="__unknown__")(?=[^}]*model_name="text-embedding-3-large")(?=[^}]*provider="openai")[^}]*\} 2(?:\.0)?',
        text,
    )
    assert re.search(
        r'openviking_embedding_tokens_total\{(?=[^}]*account_id="__unknown__")(?=[^}]*model_name="text-embedding-3-large")(?=[^}]*provider="openai")[^}]*\} 5(?:\.0)?',
        text,
    )


def test_model_collectors_attribute_ingestion_tokens(registry, render_prometheus):
    EmbeddingCollector().receive(
        "embedding.call",
        {
            "provider": "openai",
            "model_name": "text-embedding-3-large",
            "duration_seconds": 0.12,
            "prompt_tokens": 3,
            "completion_tokens": 0,
            "source_type": "repository",
        },
        registry,
    )
    VLMCollector().receive(
        "vlm.call",
        {
            "provider": "volcengine",
            "model_name": "doubao-vlm",
            "duration_seconds": 0.2,
            "prompt_tokens": 5,
            "completion_tokens": 2,
            "source_type": "document",
        },
        registry,
    )

    text = render_prometheus(registry)
    assert re.search(
        r'openviking_embedding_ingestion_tokens_total\{[^}]*source_type="repository"[^}]*\} 3(?:\.0)?',
        text,
    )
    assert re.search(
        r'openviking_vlm_ingestion_tokens_total\{[^}]*source_type="document"[^}]*\} 7(?:\.0)?',
        text,
    )


def test_embedding_collector_records_failed_call_duration_with_error_code(
    registry, render_prometheus
):
    EmbeddingCollector().receive(
        "embedding.call",
        {
            "provider": "volcengine",
            "model_name": "doubao-embedding",
            "duration_seconds": 0.12,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "error_code": "429",
        },
        registry,
    )
    text = render_prometheus(registry)

    assert re.search(
        r'openviking_embedding_calls_total\{(?=[^}]*error_code="429")(?=[^}]*model_name="doubao-embedding")(?=[^}]*provider="volcengine")[^}]*\} 1(?:\.0)?',
        text,
    )
    assert re.search(
        r'openviking_embedding_call_duration_seconds_count\{(?=[^}]*error_code="429")(?=[^}]*model_name="doubao-embedding")(?=[^}]*provider="volcengine")[^}]*\} 1(?:\.0)?',
        text,
    )
    assert "openviking_embedding_tokens_total" not in text


def test_vlm_collector_records_error_code_on_calls_and_duration_only(registry, render_prometheus):
    VLMCollector().receive(
        "vlm.call",
        {
            "provider": "volcengine",
            "model_name": "doubao-vlm",
            "duration_seconds": 0.12,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "error_code": "timeout",
        },
        registry,
    )
    text = render_prometheus(registry)

    assert re.search(
        r'openviking_vlm_calls_total\{(?=[^}]*error_code="timeout")(?=[^}]*model_name="doubao-vlm")(?=[^}]*provider="volcengine")[^}]*\} 1(?:\.0)?',
        text,
    )
    assert re.search(
        r'openviking_vlm_call_duration_seconds_count\{(?=[^}]*error_code="timeout")(?=[^}]*model_name="doubao-vlm")(?=[^}]*provider="volcengine")[^}]*\} 1(?:\.0)?',
        text,
    )
    assert "openviking_vlm_tokens_total" not in text


def test_embedding_collector_records_success_volume_and_latency(registry, render_prometheus):
    """`embedding.success` must increment request volume and observe latency."""
    EmbeddingCollector().receive(
        "embedding.success",
        {"latency_seconds": 0.2},
        registry,
    )
    text = render_prometheus(registry)
    assert (
        'openviking_embedding_requests_total{account_id="__unknown__",status="ok"} 1' in text
        or 'openviking_embedding_requests_total{account_id="__unknown__",status="ok"} 1.0' in text
    )
    assert re.search(
        r'openviking_embedding_latency_seconds_count\{account_id="__unknown__",'
        r'bucket_schema="[0-9a-f]{16}",status="ok"\} 1',
        text,
    )


def test_embedding_collector_records_error_volume_and_error_code_counter(
    registry, render_prometheus
):
    """`embedding.error` must track request errors and normalize error_code labels."""
    EmbeddingCollector().receive(
        "embedding.error",
        {"error_code": "rate_limit"},
        registry,
    )
    text = render_prometheus(registry)
    assert (
        'openviking_embedding_requests_total{account_id="__unknown__",status="error"} 1' in text
        or 'openviking_embedding_requests_total{account_id="__unknown__",status="error"} 1.0'
        in text
    )
    assert (
        'openviking_embedding_errors_total{account_id="__unknown__",error_code="rate_limit"} 1'
        in text
        or 'openviking_embedding_errors_total{account_id="__unknown__",error_code="rate_limit"} 1.0'
        in text
    )


def _emit_retrieval(registry, *, result_count, context_type="search", rerank_used=False):
    RetrievalCollector().receive(
        "retrieval.completed",
        {
            "context_type": context_type,
            "result_count": result_count,
            "latency_seconds": 0.05,
            "rerank_used": rerank_used,
            "rerank_fallback": False,
        },
        registry,
    )


def test_retrieval_collector_zero_result_records_downstream_without_raising(
    registry, render_prometheus
):
    """Regression for #2922: a zero-result retrieval is a normal outcome, not an error.

    Previously ``record_completed`` incremented RESULTS_TOTAL by ``max(0, result_count)``,
    so an empty retrieval passed ``amount=0`` and the registry raised
    "counter can only be increased by a positive amount". As the 2nd registry call this
    aborted the rest of the method: ZERO_RESULT_TOTAL, the latency histogram and the
    rerank counters were silently dropped and a WARNING was logged on every empty retrieval.
    """
    _emit_retrieval(registry, result_count=0)
    text = render_prometheus(registry)

    assert re.search(
        r'openviking_retrieval_requests_total\{[^}]*context_type="search"[^}]*\} 1(?:\.0)?', text
    )
    assert re.search(
        r'openviking_retrieval_zero_result_total\{[^}]*context_type="search"[^}]*\} 1(?:\.0)?', text
    )
    # Latency histogram still observed one sample (proving the method did not abort early).
    assert re.search(
        r'openviking_retrieval_latency_seconds_count\{[^}]*context_type="search"[^}]*\} 1(?:\.0)?',
        text,
    )
    # No positive results -> RESULTS_TOTAL has no series for this context.
    assert not re.search(r'openviking_retrieval_results_total\{[^}]*context_type="search"', text)


def test_retrieval_collector_positive_result_increments_results_total(registry, render_prometheus):
    """A non-empty retrieval increments RESULTS_TOTAL by the result count and does not
    touch the zero-result counter."""
    _emit_retrieval(registry, result_count=3, rerank_used=True)
    text = render_prometheus(registry)

    assert re.search(
        r'openviking_retrieval_results_total\{[^}]*context_type="search"[^}]*\} 3(?:\.0)?', text
    )
    assert re.search(
        r'openviking_retrieval_requests_total\{[^}]*context_type="search"[^}]*\} 1(?:\.0)?', text
    )
    assert not re.search(
        r'openviking_retrieval_zero_result_total\{[^}]*context_type="search"', text
    )


def test_retrieval_collector_negative_result_count_is_bucketed_as_zero_result(
    registry, render_prometheus
):
    """A malformed negative result_count must not raise or drop metrics; it is classified
    as an empty retrieval (no positive results) so telemetry stays internally consistent."""
    _emit_retrieval(registry, result_count=-1)
    text = render_prometheus(registry)

    assert re.search(
        r'openviking_retrieval_requests_total\{[^}]*context_type="search"[^}]*\} 1(?:\.0)?', text
    )
    assert re.search(
        r'openviking_retrieval_zero_result_total\{[^}]*context_type="search"[^}]*\} 1(?:\.0)?', text
    )
    assert re.search(
        r'openviking_retrieval_latency_seconds_count\{[^}]*context_type="search"[^}]*\} 1(?:\.0)?',
        text,
    )
    assert not re.search(r'openviking_retrieval_results_total\{[^}]*context_type="search"', text)


@pytest.mark.parametrize("value", [None, -1, 11, 1.5, "5", True, float("nan")])
def test_vlm_invalid_or_unknown_subset_does_not_claim_reporting(registry, render_prometheus, value):
    VLMCollector().receive(
        "vlm.call",
        {
            "provider": "synthetic",
            "model_name": "synthetic",
            "duration_seconds": 0.1,
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "prompt_cached_tokens": value,
        },
        registry,
    )
    text = render_prometheus(registry)
    assert "openviking_vlm_usage_subset" not in text
    assert re.search(r"openviking_vlm_tokens_total\{[^}]*\} 13(?:\.0)?", text)


def test_vlm_subset_ratios_use_only_the_reporting_population(registry, render_prometheus):
    collector = VLMCollector()
    for cached, reasoning, prompt, output in [(None, None, 100, 20), (0, 0, 10, 3), (5, 2, 10, 3)]:
        collector.receive(
            "vlm.call",
            {
                "provider": "synthetic",
                "model_name": "synthetic",
                "duration_seconds": 0.1,
                "prompt_tokens": prompt,
                "completion_tokens": output,
                "prompt_cached_tokens": cached,
                "completion_reasoning_tokens": reasoning,
            },
            registry,
        )
    text = render_prometheus(registry)
    # 5/20 among reporting calls, rather than 5/120 including unknown-cache calls.
    for metric, amount in [("tokens", 5), ("parent_tokens", 20), ("reports", 2)]:
        assert re.search(
            r"openviking_vlm_usage_subset_"
            + metric
            + r'_total\{[^}]*subset="cached_prompt"[^}]*\} '
            + str(amount)
            + r"(?:\.0)?",
            text,
        )
    assert re.search(r"openviking_vlm_tokens_total\{[^}]*\} 146(?:\.0)?", text)


def test_failed_vlm_call_cannot_create_subset_usage(registry, render_prometheus):
    VLMCollector().receive(
        "vlm.call",
        {
            "provider": "synthetic",
            "model_name": "synthetic",
            "duration_seconds": 0.1,
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "prompt_cached_tokens": 5,
            "completion_reasoning_tokens": 2,
            "error_code": "503",
        },
        registry,
    )
    assert "openviking_vlm_usage_subset" not in render_prometheus(registry)


def test_vlm_subset_metrics_follow_account_dimension(
    registry, render_prometheus, configure_account_dimension
):
    configure_account_dimension(enabled=True, metric_allowlist=["openviking_vlm_*"])
    VLMCollector().receive(
        "vlm.call",
        {
            "provider": "synthetic",
            "model_name": "synthetic",
            "duration_seconds": 0.1,
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "prompt_cached_tokens": 5,
            "completion_reasoning_tokens": 2,
            "account_id": "account-one",
        },
        registry,
    )
    text = render_prometheus(registry)
    lines = [line for line in text.splitlines() if line.startswith("openviking_vlm_usage_subset")]
    assert len(lines) == 6
    assert all('account_id="account-one"' in line for line in lines)


def test_vlm_explicit_zero_input_and_output_still_report_subset_presence(
    registry, render_prometheus
):
    VLMCollector().receive(
        "vlm.call",
        {
            "provider": "synthetic",
            "model_name": "synthetic",
            "duration_seconds": 0.1,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "prompt_cached_tokens": 0,
            "completion_reasoning_tokens": 0,
        },
        registry,
    )
    text = render_prometheus(registry)
    for subset in ("cached_prompt", "reasoning_completion"):
        assert re.search(
            r'openviking_vlm_usage_subset_reports_total\{[^}]*subset="'
            + subset
            + r'"[^}]*\} 1(?:\.0)?',
            text,
        )
    assert "openviking_vlm_usage_subset_tokens_total" not in text
    assert "openviking_vlm_tokens_total" not in text
