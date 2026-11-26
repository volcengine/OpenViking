# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import io
import logging

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from openviking.metrics.collectors.http import HTTPCollector
from openviking.metrics.datasources.http import HttpRequestLifecycleDataSource
from openviking.metrics.global_api import init_metrics_from_server_config, shutdown_metrics
from openviking.models.vlm.base import VLMBase
from openviking.observability import http_observability_middleware
from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.observability.events import (
    ObservabilityEvent,
    register_event_subscriber,
    unregister_event_subscriber,
)
from openviking.observability.http_observability_middleware import HTTPObservabilityMiddleware
from openviking.server.config import ServerConfig
from openviking.server.request_id import RequestIdMiddleware
from openviking.server.routers.metrics import router as metrics_router
from openviking.telemetry.span_models import RootSpanAttributes


def test_http_metrics_middleware_emits_authenticated_account_id(registry):
    app = FastAPI()
    app.add_middleware(HTTPObservabilityMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(metrics_router)
    route = "/api/v1/sessions/{session_id}"

    @app.get(route)
    async def get_session(request: Request, session_id: str):
        request.state.root_span_attrs.account_id = request.headers.get("x-test-account")
        return {"session_id": session_id}

    config = ServerConfig.model_validate(
        {
            "observability": {
                "metrics": {
                    "enabled": True,
                    "account_dimension": {
                        "enabled": True,
                        "metric_allowlist": [
                            HTTPCollector.REQUESTS_TOTAL,
                            HTTPCollector.REQUEST_DURATION_SECONDS,
                            HTTPCollector.INFLIGHT_REQUESTS,
                        ],
                    },
                }
            }
        }
    )
    events: list[ObservabilityEvent] = []
    init_metrics_from_server_config(config, app=app, registry=registry)
    register_event_subscriber("http-metrics-test", lambda event: events.append(event))
    try:
        with TestClient(app) as client:
            known = client.get("/api/v1/sessions/first", headers={"x-test-account": "acct-real"})
            unknown = client.get("/api/v1/sessions/second")
            scrape = client.get("/metrics")
    finally:
        unregister_event_subscriber("http-metrics-test")
        shutdown_metrics(app=app)

    assert known.json() == {"session_id": "first"}
    assert unknown.json() == {"session_id": "second"}
    assert scrape.status_code == 200
    finished = [event for event in events if event.event_name == "http.request"]
    assert [event.account_id for event in finished] == ["acct-real", None]
    assert [event.request_id for event in finished] == [
        known.headers["X-Request-ID"],
        unknown.headers["X-Request-ID"],
    ]
    for event, session_id in zip(finished, ("first", "second"), strict=True):
        assert event.payload["route"] == route
        assert event.payload["url_path"] == f"/api/v1/sessions/{session_id}"
        assert event.payload["duration_seconds"] >= 0
    for account in ("acct-real", "__unknown__"):
        labels = f'account_id="{account}",method="GET",route="{route}",status="200"'
        assert f"openviking_http_requests_total{{{labels}}} 1" in scrape.text
        assert f'openviking_http_inflight_requests{{account_id="{account}",route="{route}"}} 0' in (
            scrape.text
        )
    assert 'route="/metrics"' not in scrape.text
    assert not any(event.payload.get("route") == "/metrics" for event in events)


def test_http_metrics_middleware_logs_error_when_metrics_write_fails(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("metrics write failed")

    monkeypatch.setattr(HttpRequestLifecycleDataSource, "set_inflight", staticmethod(fail))
    log_output = io.StringIO()
    logger = logging.Logger("test.http_metrics", level=logging.ERROR)
    logger.addHandler(logging.StreamHandler(log_output))
    monkeypatch.setattr(http_observability_middleware, "logger", logger)
    app = FastAPI()
    app.add_middleware(HTTPObservabilityMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(session_id: str):
        return {"session_id": session_id}

    with TestClient(app) as client:
        response = client.get("/api/v1/sessions/existing")
    assert response.status_code == 200 and response.json() == {"session_id": "existing"}
    assert (
        "Unexpected error in http.inflight increment: metrics write failed" in log_output.getvalue()
    )


class _UsageOnlyVLM(VLMBase):
    def get_completion(self, *args, **kwargs):
        raise AssertionError("model I/O is outside the usage event contract")

    async def get_completion_async(self, *args, **kwargs):
        return self.get_completion(*args, **kwargs)

    get_vision_completion = get_completion
    get_vision_completion_async = get_completion_async


def test_vlm_base_update_token_usage_propagates_current_account():
    account = "acct-vlm"
    root = RootSpanAttributes(http_method="GET", http_route="/items", request_id="req-test")
    root.account_id = account
    token = bind_root_observability_context(root)
    events: list[ObservabilityEvent] = []
    register_event_subscriber("vlm-metrics-test", lambda event: events.append(event))
    try:
        _UsageOnlyVLM({"provider": "volcengine", "model": "m1"}).update_token_usage(
            model_name="m1",
            provider="volcengine",
            prompt_tokens=3,
            completion_tokens=2,
            duration_seconds=0.5,
        )
    finally:
        unregister_event_subscriber("vlm-metrics-test")
        reset_root_observability_context(token)

    (event,) = [event for event in events if event.event_name == "vlm.call"]
    assert event.account_id == account
    assert event.payload["prompt_tokens"] == 3
    assert event.payload["completion_tokens"] == 2
