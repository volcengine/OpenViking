# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from openviking.metrics.account_dimension import (
    configure_metric_account_dimension,
    reset_metric_account_dimension,
)
from openviking.metrics.collectors.base import EventMetricCollector
from openviking.metrics.collectors.embedding import EmbeddingCollector
from openviking.metrics.collectors.http import HTTPCollector
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.exporters.prometheus import PrometheusExporter
from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.telemetry.span_models import RootSpanAttributes


def _bind_root_context_for_account(account_id: str | None):
    root = RootSpanAttributes(http_method="GET", http_route="/test", request_id="req-test")
    root.account_id = account_id
    return bind_root_observability_context(root)


def test_account_dimension_injects_account_id_when_metric_allowlisted_exact():
    """Allowlist exact match must inject the bound account id for supported metrics."""
    registry = MetricRegistry()
    configure_metric_account_dimension(
        enabled=True,
        metric_allowlist={EmbeddingCollector.CALLS_TOTAL},
        max_active_accounts=10,
    )
    token = _bind_root_context_for_account("acct-embed-1")
    try:
        EmbeddingCollector().receive(
            "embedding.call",
            {
                "provider": "openai",
                "model_name": "text-embedding-3-large",
                "duration_seconds": 0.1,
                "prompt_tokens": 1,
                "completion_tokens": 1,
            },
            registry,
        )
    finally:
        reset_root_observability_context(token)
        reset_metric_account_dimension()

    text = PrometheusExporter(registry=registry).render()
    assert 'openviking_embedding_calls_total{account_id="acct-embed-1",' in text


def test_account_dimension_injects_account_id_when_metric_allowlisted_by_prefix():
    """Allowlist prefix match (trailing '*') must inject the bound account id."""
    registry = MetricRegistry()
    configure_metric_account_dimension(
        enabled=True,
        metric_allowlist={"openviking_embedding_*"},
        max_active_accounts=10,
    )
    token = _bind_root_context_for_account("acct-embed-2")
    try:
        EmbeddingCollector().receive(
            "embedding.success",
            {"latency_seconds": 0.2},
            registry,
        )
    finally:
        reset_root_observability_context(token)
        reset_metric_account_dimension()

    text = PrometheusExporter(registry=registry).render()
    assert (
        'openviking_embedding_requests_total{account_id="acct-embed-2",status="ok"} 1' in text
        or 'openviking_embedding_requests_total{account_id="acct-embed-2",status="ok"} 1.0' in text
    )


def test_account_dimension_returns_overflow_when_max_active_accounts_exceeded():
    """When active accounts exceed the cap, new accounts resolve to `__overflow__`."""
    registry = MetricRegistry()
    configure_metric_account_dimension(
        enabled=True,
        metric_allowlist={EmbeddingCollector.CALLS_TOTAL},
        max_active_accounts=2,
    )
    try:
        for account_id in ("acct-a", "acct-b", "acct-c"):
            token = _bind_root_context_for_account(account_id)
            try:
                EmbeddingCollector().receive(
                    "embedding.call",
                    {
                        "provider": "openai",
                        "model_name": "text-embedding-3-large",
                        "duration_seconds": 0.01,
                        "prompt_tokens": 1,
                        "completion_tokens": 0,
                    },
                    registry,
                )
            finally:
                reset_root_observability_context(token)
    finally:
        reset_metric_account_dimension()

    text = PrometheusExporter(registry=registry).render()
    assert 'openviking_embedding_calls_total{account_id="__overflow__",' in text
    assert 'account_id="acct-c"' not in text


def test_account_dimension_returns_unknown_for_not_allowlisted_metric_even_with_context():
    """Enabled account-dimension still resolves to `__unknown__` when metric is not allowlisted."""
    registry = MetricRegistry()
    configure_metric_account_dimension(
        enabled=True,
        metric_allowlist={HTTPCollector.REQUESTS_TOTAL},
        max_active_accounts=10,
    )
    token = _bind_root_context_for_account("acct-x")
    try:
        EmbeddingCollector().receive(
            "embedding.success",
            {"latency_seconds": 0.01},
            registry,
        )
    finally:
        reset_root_observability_context(token)
        reset_metric_account_dimension()

    text = PrometheusExporter(registry=registry).render()
    assert (
        'openviking_embedding_requests_total{account_id="__unknown__",status="ok"} 1' in text
        or 'openviking_embedding_requests_total{account_id="__unknown__",status="ok"} 1.0' in text
    )


def test_account_dimension_does_not_inject_account_id_for_unsupported_metric_names():
    """Metrics outside the supported set must never receive `account_id` label injection."""

    class DummyCollector(EventMetricCollector):
        SUPPORTED_EVENTS = frozenset({"demo"})

        def receive_hook(self, event_name: str, payload: dict, registry) -> None:
            registry.inc_counter("openviking_unsupported_total")

    registry = MetricRegistry()
    configure_metric_account_dimension(
        enabled=True,
        metric_allowlist={"openviking_unsupported_total"},
        max_active_accounts=10,
    )
    token = _bind_root_context_for_account("acct-z")
    try:
        DummyCollector().receive("demo", {}, registry)
    finally:
        reset_root_observability_context(token)
        reset_metric_account_dimension()

    text = PrometheusExporter(registry=registry).render()
    assert "openviking_unsupported_total{" not in text
    assert "openviking_unsupported_total 1" in text
