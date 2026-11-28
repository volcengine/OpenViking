# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import re

import pytest
from prometheus_client.parser import text_string_to_metric_families

from openviking.metrics.collectors.queue import QueueDurationCollector
from openviking.metrics.datasources.cache import CacheEventDataSource
from openviking.metrics.datasources.encryption import EncryptionEventDataSource
from openviking.metrics.datasources.model_usage import (
    EmbeddingEventDataSource,
    RerankEventDataSource,
    VLMEventDataSource,
)
from openviking.metrics.datasources.retrieval import RetrievalStatsDataSource
from openviking.metrics.datasources.session import SessionLifecycleDataSource
from openviking.metrics.global_api import init_metrics_from_server_config, shutdown_metrics
from openviking.server.config import ServerConfig


@pytest.fixture
def samples(registry, render_prometheus):
    init_metrics_from_server_config(
        ServerConfig.model_validate({"observability": {"metrics": {"enabled": True}}}),
        app=None,
        registry=registry,
    )

    def read(name, **labels):
        matches = [
            sample.value
            for family in text_string_to_metric_families(render_prometheus(registry))
            for sample in family.samples
            if sample.name == "openviking_" + name
            and {k: v for k, v in sample.labels.items() if k != "bucket_schema"} == labels
        ]
        assert len(matches) <= 1
        return matches[0] if matches else None

    try:
        yield read
    finally:
        shutdown_metrics()


def test_cache_events_reach_prometheus(samples):
    CacheEventDataSource.record_hit("L1")
    CacheEventDataSource.record_miss("L2")
    assert samples("cache_hits_total", level="L1") == 1
    assert samples("cache_misses_total", level="L2") == 1


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


def test_session_collector_records_lifecycle_and_archive(samples):
    SessionLifecycleDataSource.record_lifecycle(action="create", status="ok")
    SessionLifecycleDataSource.record_archive(status="ok")
    assert (
        samples("session_lifecycle_total", account_id="__unknown__", action="create", status="ok")
        == 1
    )
    assert samples("session_archive_total", account_id="__unknown__", status="ok") == 1


def test_encryption_event_datasource_emits_operation_event(samples):
    EncryptionEventDataSource.record_operation(
        operation="encrypt", status="ok", duration_seconds=0.25
    )
    for size in (12, 0, -1):
        EncryptionEventDataSource.record_bytes(operation="encrypt", size_bytes=size)
    assert samples("encryption_operations_total", operation="encrypt", status="ok") == 1
    assert samples("encryption_duration_seconds_sum", operation="encrypt", status="ok") == 0.25
    assert samples("encryption_bytes_total", operation="encrypt") == 12


@pytest.mark.parametrize(
    ("model_type", "source", "provider", "model", "ingestion", "failure", "tokens"),
    [
        (
            "embedding",
            EmbeddingEventDataSource,
            "openai",
            "text-embedding-3-large",
            "repository",
            "429",
            (3, 2),
        ),
        ("vlm", VLMEventDataSource, "volcengine", "doubao-vlm", "document", "timeout", (5, 2)),
        ("rerank", RerankEventDataSource, "cohere", "rerank-v3.5", None, None, (9, 0)),
    ],
    ids=["embedding", "vlm", "rerank"],
)
def test_model_calls_report_metrics_and_tokens(
    samples, model_type, source, provider, model, ingestion, failure, tokens
):
    call = {"provider": provider, "model_name": model, "duration_seconds": 0.12}
    model_labels = {"provider": provider, "model_name": model, "account_id": "__unknown__"}
    call_labels = {**model_labels, "error_code": "OK"} if failure else model_labels
    extra = {"source_type": ingestion} if failure else {}
    input_tokens, output_tokens = tokens
    source.record_call(**call, **extra, prompt_tokens=input_tokens, completion_tokens=output_tokens)
    assert samples(f"{model_type}_calls_total", **call_labels) == 1
    assert samples(f"{model_type}_call_duration_seconds_count", **call_labels) == 1
    assert samples(f"{model_type}_tokens_input_total", **model_labels) == input_tokens
    if output_tokens:
        assert samples(f"{model_type}_tokens_output_total", **model_labels) == output_tokens
    assert samples(f"{model_type}_tokens_total", **model_labels) == sum(tokens)

    if failure:
        assert samples(
            f"{model_type}_ingestion_tokens_total",
            account_id="__unknown__",
            source_type=ingestion,
        ) == sum(tokens)

        source.record_call(**call, prompt_tokens=0, completion_tokens=0, error_code=failure)
        failed_labels = {**model_labels, "error_code": failure}
        assert samples(f"{model_type}_calls_total", **failed_labels) == 1
        assert samples(f"{model_type}_call_duration_seconds_count", **failed_labels) == 1
        assert samples(f"{model_type}_tokens_total", **model_labels) == sum(tokens)
        assert samples(f"{model_type}_tokens_total", **failed_labels) is None


@pytest.mark.parametrize(
    ("model_type", "source", "prompt_tokens", "completion_tokens", "total_tokens"),
    [
        ("embedding", EmbeddingEventDataSource, None, None, None),
        ("vlm", VLMEventDataSource, 9, None, 9),
        ("vlm", VLMEventDataSource, None, 4, 4),
        ("vlm", VLMEventDataSource, None, None, None),
    ],
    ids=["embedding-no-usage", "vlm-input-only", "vlm-output-only", "vlm-no-usage"],
)
def test_model_calls_record_known_tokens(
    samples, model_type, source, prompt_tokens, completion_tokens, total_tokens
):
    source.record_call(
        provider="volcengine",
        model_name="test-model",
        duration_seconds=0.1,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        source_type="document",
    )
    labels = {"account_id": "__unknown__", "provider": "volcengine", "model_name": "test-model"}
    assert samples(f"{model_type}_calls_total", **labels, error_code="OK") == 1
    assert samples(f"{model_type}_call_duration_seconds_count", **labels, error_code="OK") == 1
    assert samples(f"{model_type}_tokens_input_total", **labels) == prompt_tokens
    assert samples(f"{model_type}_tokens_output_total", **labels) == completion_tokens
    assert samples(f"{model_type}_tokens_total", **labels) == total_tokens
    assert (
        samples(
            f"{model_type}_ingestion_tokens_total", account_id="__unknown__", source_type="document"
        )
        == total_tokens
    )


def test_embedding_collector_records_success_volume_and_latency(samples):
    EmbeddingEventDataSource.record_success(latency_seconds=0.2)
    EmbeddingEventDataSource.record_error(error_code="rate_limit")
    labels = {"account_id": "__unknown__"}
    assert samples("embedding_requests_total", **labels, status="ok") == 1
    assert samples("embedding_requests_total", **labels, status="error") == 1
    assert samples("embedding_latency_seconds_sum", **labels, status="ok") == 0.2
    assert samples("embedding_errors_total", **labels, error_code="rate_limit") == 1


def test_retrieval_collector_zero_result_records_downstream_without_raising(samples):
    source = RetrievalStatsDataSource()
    labels = {"account_id": "__unknown__"}
    source.record_retrieval(
        context_type="search", result_count=3, latency_seconds=0.05, rerank_used=True
    )
    assert samples("retrieval_results_total", **labels, context_type="search") == 3
    assert samples("retrieval_zero_result_total", **labels, context_type="search") is None

    source.record_retrieval(context_type="search", result_count=0, latency_seconds=0.1)
    source.record_retrieval(context_type="", result_count=-1, latency_seconds=0.15)
    assert samples("retrieval_requests_total", **labels, context_type="search") == 2
    assert samples("retrieval_zero_result_total", **labels, context_type="search") == 1
    assert samples("retrieval_latency_seconds_count", **labels, context_type="search") == 2
    assert samples("retrieval_results_total", **labels, context_type="search") == 3
    assert samples("retrieval_results_total", **labels, context_type="unknown") is None
    assert samples("retrieval_requests_total", **labels, context_type="unknown") == 1
    assert samples("retrieval_zero_result_total", **labels, context_type="unknown") == 1
    assert samples("retrieval_latency_seconds_count", **labels, context_type="unknown") == 1
    assert samples("retrieval_rerank_used_total", **labels) == 1
