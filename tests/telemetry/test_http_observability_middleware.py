from fastapi import FastAPI
from fastapi.testclient import TestClient

from openviking.metrics.datasources import HttpRequestLifecycleDataSource
from openviking.server.request_id import RequestIdMiddleware
from openviking.server.responses import error_response, response_from_result


class _DummySpan:
    def __init__(self) -> None:
        self.updated_names: list[str] = []

    def update_name(self, name: str) -> None:
        self.updated_names.append(name)


class _DummySpanCM:
    def __init__(self, span: _DummySpan) -> None:
        self._span = span

    def __enter__(self) -> _DummySpan:
        return self._span

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_http_observability_middleware_updates_route_template_after_routing(monkeypatch) -> None:
    """
    Ensure `http_route` is finalized after routing has occurred (post-call_next).

    Starlette/FastAPI route matching happens downstream of middleware, so reading
    `request.scope["route"]` before `call_next` may yield no route.
    """
    from openviking.observability import http_observability_middleware as mw_mod

    dummy_span = _DummySpan()

    # Avoid pulling in metrics/otel side effects in this unit test.
    monkeypatch.setattr(mw_mod, "should_skip_http_metrics", lambda request: False)
    monkeypatch.setattr(mw_mod, "apply_http_metrics_start", lambda **kwargs: None)
    monkeypatch.setattr(mw_mod, "apply_http_metrics_finalize", lambda **kwargs: None)
    monkeypatch.setattr(mw_mod, "maybe_apply_root_span_attributes", lambda *a, **k: None)
    monkeypatch.setattr(mw_mod, "maybe_apply_root_span_response", lambda *a, **k: None)
    monkeypatch.setattr(mw_mod, "maybe_apply_root_span_error", lambda *a, **k: None)

    # Force a "span" so we can validate operation_name update behavior.
    monkeypatch.setattr(
        mw_mod, "maybe_start_root_span", lambda request, root_attrs: _DummySpanCM(dummy_span)
    )

    captured = {}
    real_create = mw_mod.create_root_span_attributes

    def _capture_create_root_span_attributes(**kwargs):
        root_attrs = real_create(**kwargs)
        captured["root_attrs"] = root_attrs
        return root_attrs

    monkeypatch.setattr(mw_mod, "create_root_span_attributes", _capture_create_root_span_attributes)

    app = FastAPI()
    http_mw = mw_mod.create_http_observability_middleware()

    @app.middleware("http")
    async def _mw(request, call_next):
        request.state.request_id = "test-request"
        return await http_mw(request, call_next)

    @app.get("/hello")
    async def hello():
        return {"ok": True}

    with TestClient(app) as client:
        resp = client.get("/hello?token=top-secret")
        assert resp.status_code == 200

    root_attrs = captured["root_attrs"]
    assert root_attrs.http_route == "/hello"
    assert root_attrs.url_query is None
    assert "url.query" not in root_attrs.to_otel_attributes()
    assert "GET /hello" in dummy_span.updated_names


def test_http_observability_captures_public_error_without_reading_response_body(
    monkeypatch,
) -> None:
    from openviking.observability import http_observability_middleware as mw_mod

    completed: list[dict] = []
    monkeypatch.setattr(HttpRequestLifecycleDataSource, "set_inflight", lambda **_: None)
    monkeypatch.setattr(
        HttpRequestLifecycleDataSource,
        "record_request",
        lambda **kwargs: completed.append(kwargs),
    )
    monkeypatch.setattr(mw_mod, "maybe_start_root_span", lambda *_: None)

    app = FastAPI()
    http_mw = mw_mod.create_http_observability_middleware()

    @app.middleware("http")
    async def _mw(request, call_next):
        return await http_mw(request, call_next)

    app.add_middleware(RequestIdMiddleware)

    @app.get("/invalid")
    async def invalid():
        return error_response(
            "INVALID_ARGUMENT",
            "limit must be at most 200",
            details={"limit": 300, "api_key": "sk-not-persisted"},
        )

    @app.get("/unavailable")
    async def unavailable():
        return error_response(
            "UNAVAILABLE",
            "Parser service is unavailable",
            details={"retryable": True},
        )

    @app.get("/business-error")
    async def business_error():
        return response_from_result(
            {
                "status": "error",
                "code": "PROCESSING_ERROR",
                "message": "Resource processing failed",
                "details": {"stage": "parse"},
            }
        )

    with TestClient(app) as client:
        invalid_response = client.get("/invalid")
        unavailable_response = client.get("/unavailable")
        business_response = client.get("/business-error")

    assert invalid_response.status_code == 400
    assert invalid_response.json()["error"]["details"]["api_key"] == "sk-not-persisted"
    assert unavailable_response.status_code == 503
    assert business_response.status_code == 500
    assert len(completed) == 3
    assert completed[0]["error_code"] == "INVALID_ARGUMENT"
    assert completed[0]["error_message"] == "limit must be at most 200"
    assert completed[0]["error_details"] == {"limit": 300, "api_key": "[REDACTED]"}
    assert completed[1]["error_code"] == "UNAVAILABLE"
    assert completed[1]["error_message"] == "Parser service is unavailable"
    assert completed[1]["error_details"] == {"retryable": True}
    assert completed[2]["error_code"] == "PROCESSING_ERROR"
    assert completed[2]["error_message"] == "Resource processing failed"
    assert completed[2]["error_details"] == {"stage": "parse"}
