from fastapi import FastAPI
from fastapi.testclient import TestClient


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
        return await http_mw(request, call_next)

    @app.get("/hello")
    async def hello():
        return {"ok": True}

    with TestClient(app) as client:
        resp = client.get("/hello")
        assert resp.status_code == 200

    root_attrs = captured["root_attrs"]
    assert root_attrs.http_route == "/hello"
    assert "GET /hello" in dummy_span.updated_names
