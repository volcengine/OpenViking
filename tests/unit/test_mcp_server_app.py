from types import SimpleNamespace

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server.mcp_endpoint import _IdentityASGIMiddleware


def test_create_mcp_app_applies_streamable_http_options(monkeypatch):
    captured = {}

    async def downstream(scope, receive, send):
        del scope, receive, send

    def fake_streamable_http_app(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(routes=[SimpleNamespace(app=downstream)])

    monkeypatch.setattr(mcp_endpoint.mcp, "streamable_http_app", fake_streamable_http_app)

    app = mcp_endpoint.create_mcp_app()

    assert isinstance(app, _IdentityASGIMiddleware)
    assert app.app is downstream
    assert captured["stateless_http"] is True
    assert captured["transport_security"].enable_dns_rebinding_protection is False
