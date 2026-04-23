# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

import openviking.observability.http_observability_middleware as http_middleware
from openviking.metrics.collectors.http import HTTPCollector
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.datasources.base import EventMetricDataSource
from openviking.metrics.datasources.http import HttpRequestLifecycleDataSource
from openviking.metrics.datasources.model_usage import VLMEventDataSource
from openviking.metrics.exporters.prometheus import PrometheusExporter
from openviking.metrics.global_api import configure_metric_account_dimension, shutdown_metrics
from openviking.models.vlm.base import VLMBase
from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.observability.http_observability_middleware import (
    _INFLIGHT,
    _get_route_template,
    _inflight_delta,
    create_http_observability_middleware,
)
from openviking.telemetry.span_models import RootSpanAttributes


def _bind_root_context_for_account(account_id: str | None):
    root = RootSpanAttributes(http_method="GET", http_route="/items", request_id="req-test")
    root.account_id = account_id
    return bind_root_observability_context(root)


def _build_test_app(middleware_factory, *, route_path: str, method: str, handler):
    app = FastAPI()

    @app.middleware("http")
    async def middleware_entry(request, call_next):
        return await middleware_factory(request, call_next)

    if method == "GET":
        app.get(route_path)(handler)
    elif method == "POST":
        app.post(route_path)(handler)
    else:
        raise ValueError(f"unsupported method: {method}")

    return app


def test_http_collector_emits_account_label():
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

    token = _bind_root_context_for_account("acct-1")
    try:
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
    finally:
        reset_root_observability_context(token)
        shutdown_metrics(app=None)

    text = PrometheusExporter(registry=registry).render()
    assert (
        'openviking_http_requests_total{account_id="acct-1",method="GET",route="/items",status="200"} 1'
        in text
    )


def test_http_request_datasource_propagates_explicit_account_id(monkeypatch):
    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, dict(payload)))

    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_fake_emit), raising=False)

    HttpRequestLifecycleDataSource.record_request(
        method="POST",
        route="/api/v1/resources",
        status="200",
        duration_seconds=0.25,
        account_id="acct-http",
    )
    HttpRequestLifecycleDataSource.set_inflight(
        route="/api/v1/resources",
        value=1.0,
        account_id="acct-http",
    )

    assert captured == [
        (
            "http.request",
            {
                "method": "POST",
                "route": "/api/v1/resources",
                "status": "200",
                "duration_seconds": 0.25,
                "account_id": "acct-http",
            },
        ),
        (
            "http.inflight",
            {
                "route": "/api/v1/resources",
                "value": 1.0,
                "account_id": "acct-http",
            },
        ),
    ]


def test_get_route_template_uses_low_cardinality_fallback_for_unmatched_route():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/resources/123e4567-e89b-12d3-a456-426614174000",
            "headers": [],
            "state": {},
        }
    )

    assert _get_route_template(request) == "/__unmatched__"


def test_inflight_delta_removes_zero_value_entries():
    _INFLIGHT.clear()

    assert _inflight_delta("/api/v1/resources", None, +1) == 1
    assert _INFLIGHT[("/api/v1/resources", None)] == 1

    assert _inflight_delta("/api/v1/resources", None, -1) == 0
    assert ("/api/v1/resources", None) not in _INFLIGHT


def test_http_metrics_module_exposes_only_unified_middleware_entrypoint():
    assert not hasattr(http_middleware, "create_http_metrics_middleware")


def test_http_metrics_middleware_emits_authenticated_account_id(monkeypatch):
    captured: list[tuple[str, dict]] = []
    middleware = create_http_observability_middleware()

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, dict(payload)))

    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_fake_emit), raising=False)
    monkeypatch.setattr(http_middleware, "maybe_start_root_span", lambda _req, _attrs: None)

    async def resources_handler(request: Request):
        request.state.root_span_attrs.account_id = "acct-real"
        return {"ok": True}

    app = _build_test_app(
        middleware,
        route_path="/api/v1/resources",
        method="POST",
        handler=resources_handler,
    )

    with TestClient(app) as client:
        resp = client.post("/api/v1/resources")
        assert resp.status_code == 200

    request_events = [payload for event_name, payload in captured if event_name == "http.request"]
    assert request_events
    assert request_events[0] == {
        "method": "POST",
        "route": "/api/v1/resources",
        "status": "200",
        "duration_seconds": request_events[0]["duration_seconds"],
        "account_id": "acct-real",
    }
    assert isinstance(request_events[0]["duration_seconds"], float)
    assert request_events[0]["duration_seconds"] >= 0.0
    assert any(
        event_name == "http.inflight" and payload.get("account_id") == "acct-real"
        for event_name, payload in captured
    )


def test_http_metrics_middleware_ignores_internal_metrics_route(monkeypatch):
    captured: list[tuple[str, dict]] = []
    middleware = create_http_observability_middleware()

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, dict(payload)))

    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_fake_emit), raising=False)
    monkeypatch.setattr(http_middleware, "maybe_start_root_span", lambda _req, _attrs: None)

    async def metrics_handler():
        return {"ok": True}

    app = _build_test_app(
        middleware,
        route_path="/metrics",
        method="GET",
        handler=metrics_handler,
    )

    with TestClient(app) as client:
        resp = client.get("/metrics")
        assert resp.status_code == 200

    assert captured == []


def test_http_metrics_middleware_still_records_business_route(monkeypatch):
    captured: list[tuple[str, dict]] = []
    middleware = create_http_observability_middleware()

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, dict(payload)))

    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_fake_emit), raising=False)
    monkeypatch.setattr(http_middleware, "maybe_start_root_span", lambda _req, _attrs: None)

    async def resources_handler(request: Request):
        request.state.root_span_attrs.account_id = "acct-real"
        return {"ok": True}

    app = _build_test_app(
        middleware,
        route_path="/api/v1/resources",
        method="POST",
        handler=resources_handler,
    )

    with TestClient(app) as client:
        resp = client.post("/api/v1/resources")
        assert resp.status_code == 200

    assert any(event_name == "http.request" for event_name, _payload in captured)


def test_http_metrics_middleware_uses_route_bound_during_call_next(monkeypatch):
    captured: list[tuple[str, dict]] = []
    middleware = create_http_observability_middleware()

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, dict(payload)))

    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_fake_emit), raising=False)
    monkeypatch.setattr(http_middleware, "maybe_start_root_span", lambda _req, _attrs: None)

    async def get_session_handler(session_id: str):
        return {"session_id": session_id}

    app = _build_test_app(
        middleware,
        route_path="/api/v1/sessions/{session_id}",
        method="GET",
        handler=get_session_handler,
    )

    with TestClient(app) as client:
        resp = client.get("/api/v1/sessions/session_123")
        assert resp.status_code == 200

    request_events = [payload for event_name, payload in captured if event_name == "http.request"]
    assert request_events
    assert request_events[0]["route"] == "/api/v1/sessions/{session_id}"


def test_http_metrics_middleware_logs_debug_when_metrics_write_fails(monkeypatch):
    middleware = create_http_observability_middleware()
    debug_calls: list[tuple[str, tuple, dict]] = []

    def _boom(**_kwargs):
        raise RuntimeError("metrics write failed")

    def _debug(message, *args, **kwargs):
        debug_calls.append((message, args, kwargs))

    monkeypatch.setattr(HttpRequestLifecycleDataSource, "set_inflight", staticmethod(_boom))
    monkeypatch.setattr(http_middleware.logger, "isEnabledFor", lambda _level: True)
    monkeypatch.setattr(http_middleware.logger, "debug", _debug)
    monkeypatch.setattr(http_middleware, "maybe_start_root_span", lambda _req, _attrs: None)

    async def resources_handler(request: Request):
        request.state.root_span_attrs.account_id = "acct-real"
        return {"ok": True}

    app = _build_test_app(
        middleware,
        route_path="/api/v1/resources",
        method="POST",
        handler=resources_handler,
    )

    with TestClient(app) as client:
        resp = client.post("/api/v1/resources")
        assert resp.status_code == 200

    assert any(
        message == "http metrics write failed: %s" for message, _args, _kwargs in debug_calls
    )
    assert any("http.inflight" in str(args) for _message, args, _kwargs in debug_calls)


class _DummyVLM(VLMBase):
    def get_completion(self, *args, **kwargs):
        return ""

    async def get_completion_async(self, *args, **kwargs):
        return ""

    def get_vision_completion(self, *args, **kwargs):
        return ""

    async def get_vision_completion_async(self, *args, **kwargs):
        return ""


def test_vlm_base_update_token_usage_propagates_current_account(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_record_call(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(VLMEventDataSource, "record_call", staticmethod(_fake_record_call))

    token = _bind_root_context_for_account("acct-vlm-callsite")
    try:
        _DummyVLM({"provider": "volcengine", "model": "m1"}).update_token_usage(
            model_name="m1",
            provider="volcengine",
            prompt_tokens=3,
            completion_tokens=2,
            duration_seconds=0.5,
        )
    finally:
        reset_root_observability_context(token)

    assert captured["account_id"] == "acct-vlm-callsite"
