# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import re

import openviking.metrics.global_api as global_api
from openviking.metrics.collectors.base import EventMetricCollector
from openviking.metrics.collectors.cache import CacheCollector
from openviking.metrics.collectors.embedding import EmbeddingCollector
from openviking.metrics.collectors.rerank import RerankCollector
from openviking.metrics.collectors.session import SessionCollector
from openviking.metrics.collectors.telemetry_bridge import TelemetryBridgeCollector


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


def test_session_collector_records_lifecycle_and_contexts_and_archive(registry, render_prometheus):
    c = SessionCollector()
    c.receive("session.lifecycle", {"action": "create", "status": "ok"}, registry)
    c.receive("session.contexts_used", {"action": "create", "delta": 2}, registry)
    c.receive("session.archive", {"status": "ok"}, registry)

    text = render_prometheus(registry)
    assert (
        'openviking_session_lifecycle_total{account_id="__unknown__",action="create",status="ok"} 1'
        in text
    )
    assert (
        'openviking_session_contexts_used_total{account_id="__unknown__",action="create"} 2' in text
    )
    assert 'openviking_session_archive_total{account_id="__unknown__",status="ok"} 1' in text


def test_session_collector_ignores_malformed_payloads_instead_of_raising(
    registry, render_prometheus
):
    c = SessionCollector()

    c.receive("session.lifecycle", "not-a-dict", registry)
    c.receive("session.lifecycle", {"action": "create"}, registry)
    c.receive("session.contexts_used", {"action": "create"}, registry)
    c.receive("session.archive", {}, registry)

    text = render_prometheus(registry)
    assert "openviking_session_lifecycle_total" not in text
    assert "openviking_session_contexts_used_total" not in text
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


def test_http_collector_importable() -> None:
    from openviking.metrics.collectors.http import HTTPCollector

    assert HTTPCollector is not None


def test_global_api_no_longer_exports_metrics_enabled_helper() -> None:
    """Avoid redundant exports: metrics enablement is read via direct config access."""
    assert not hasattr(global_api, "is_metrics_enabled_from_server_config")


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
        r'openviking_embedding_calls_total\{(?=[^}]*account_id="__unknown__")(?=[^}]*model_name="text-embedding-3-large")(?=[^}]*provider="openai")[^}]*\} 1(?:\.0)?',
        text,
    )
    assert re.search(
        r'openviking_embedding_call_duration_seconds_count\{(?=[^}]*account_id="__unknown__")(?=[^}]*model_name="text-embedding-3-large")(?=[^}]*provider="openai")[^}]*\} 1(?:\.0)?',
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
    assert (
        'openviking_embedding_latency_seconds_count{account_id="__unknown__",status="ok"} 1' in text
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
