# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openviking.metrics.account_context import (
    bind_metric_account_context,
    get_metric_account_context,
    reset_metric_account_context,
)
from openviking.metrics.account_dimension import (
    configure_metric_account_dimension,
    reset_metric_account_dimension,
)
from openviking.metrics.collectors.base import EventMetricCollector
from openviking.metrics.collectors.embedding import EmbeddingCollector
from openviking.metrics.collectors.http import HTTPCollector
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.datasources.base import EventMetricDataSource
from openviking.metrics.exporters.prometheus import PrometheusExporter
from openviking.metrics.http_middleware import create_http_metrics_middleware


def test_metric_account_context_binds_and_resets_account_id():
    """Binding a context must be observable via `get_metric_account_context()` and resettable."""
    token = bind_metric_account_context(account_id="acct_123")
    try:
        assert get_metric_account_context().http_account_id == "acct_123"
    finally:
        reset_metric_account_context(token)

    assert get_metric_account_context().http_account_id is None


@pytest.mark.asyncio
async def test_metric_account_context_is_isolated_per_task():
    """Two concurrent tasks must not leak metric account context into each other."""

    async def _worker(account_id: str, seen: list[str | None]):
        token = bind_metric_account_context(account_id=account_id)
        try:
            await asyncio.sleep(0)
            seen.append(get_metric_account_context().http_account_id)
        finally:
            reset_metric_account_context(token)

    seen: list[str | None] = []
    await asyncio.gather(_worker("acct_a", seen), _worker("acct_b", seen))
    assert sorted(seen) == ["acct_a", "acct_b"]


def test_http_collector_uses_unknown_account_when_unbound():
    """When no context is bound, supported allowlisted metrics still resolve to `__unknown__`."""
    registry = MetricRegistry()
    configure_metric_account_dimension(
        enabled=True,
        metric_allowlist={
            HTTPCollector.REQUESTS_TOTAL,
            HTTPCollector.REQUEST_DURATION_SECONDS,
            HTTPCollector.INFLIGHT_REQUESTS,
        },
        max_active_accounts=10,
    )
    collector = HTTPCollector()
    collector.receive(
        "http.request",
        {
            "method": "GET",
            "route": "/items",
            "status": "200",
            "duration_seconds": 0.1,
        },
        registry,
    )
    reset_metric_account_dimension()

    text = PrometheusExporter(registry=registry).render()
    assert (
        'openviking_http_requests_total{account_id="__unknown__",method="GET",route="/items",status="200"} 1'
        in text
    )


@pytest.mark.asyncio
async def test_http_metrics_middleware_propagates_state_account_to_collector(monkeypatch):
    """HTTP middleware must attach the authenticated account id to emitted http events."""
    captured: list[tuple[str, dict]] = []
    middleware = create_http_metrics_middleware()

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, dict(payload)))

    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_fake_emit), raising=False)

    async def _call_next(request):
        request.state.metric_account_id = "acct-mdw"
        return SimpleNamespace(status_code=200)

    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/resources",
            "route": SimpleNamespace(path="/api/v1/resources"),
            "headers": [],
            "state": {},
        }
    )
    await middleware(request, _call_next)

    assert any(
        event_name == "http.request" and payload.get("account_id") == "acct-mdw"
        for event_name, payload in captured
    )


def test_account_dimension_injects_account_id_when_metric_allowlisted_exact():
    """Allowlist exact match must inject the bound account id for supported metrics."""
    registry = MetricRegistry()
    configure_metric_account_dimension(
        enabled=True,
        metric_allowlist={EmbeddingCollector.CALLS_TOTAL},
        max_active_accounts=10,
    )
    token = bind_metric_account_context(account_id="acct-embed-1")
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
        reset_metric_account_context(token)
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
    token = bind_metric_account_context(account_id="acct-embed-2")
    try:
        EmbeddingCollector().receive(
            "embedding.success",
            {"latency_seconds": 0.2},
            registry,
        )
    finally:
        reset_metric_account_context(token)
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
            token = bind_metric_account_context(account_id=account_id)
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
                reset_metric_account_context(token)
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
    token = bind_metric_account_context(account_id="acct-x")
    try:
        EmbeddingCollector().receive(
            "embedding.success",
            {"latency_seconds": 0.01},
            registry,
        )
    finally:
        reset_metric_account_context(token)
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
    token = bind_metric_account_context(account_id="acct-z")
    try:
        DummyCollector().receive("demo", {}, registry)
    finally:
        reset_metric_account_context(token)
        reset_metric_account_dimension()

    text = PrometheusExporter(registry=registry).render()
    assert "openviking_unsupported_total{" not in text
    assert "openviking_unsupported_total 1" in text
