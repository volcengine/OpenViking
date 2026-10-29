"""The embedded gateway owns /gateway and starts before deletion recovery."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.testclient import TestClient, WebSocketDenialResponse

from openviking.crypto.encryptor import MAGIC, FileEncryptor
from openviking.server import gateway_host
from openviking_cli.utils.config.encryption_config import EncryptionConfig
from openviking_gateway.config import EmbeddedGatewayConfig


def openviking_config(gateway, encryption=None):
    return SimpleNamespace(
        gateway=gateway,
        encryption=encryption or EncryptionConfig(),
        oauth=SimpleNamespace(enabled=False),
    )


@pytest.mark.parametrize("host,expected", [("0.0.0.0", "127.0.0.1"), ("::", "[::1]")])
def test_embedded_gateway_targets_loopback_without_version_gate(host, expected, monkeypatch):
    monkeypatch.setattr(
        "openviking_gateway.app.create_app", lambda config, **kwargs: {"config": config, **kwargs}
    )
    options = gateway_host.create_embedded_gateway(
        openviking_config(EmbeddedGatewayConfig(enabled=True)),
        SimpleNamespace(host=host, port=1987, public_base_url=None),
        lambda: None,
    )
    assert options["openviking_url"] == f"http://{expected}:1987"
    assert options["config"].min_server_version == "0"
    assert options["encryptor"] is None


async def test_embedded_gateway_uses_server_encryption(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "openviking_gateway.app.create_app", lambda config, **kwargs: {"config": config, **kwargs}
    )
    encryption = EncryptionConfig(enabled=True, local={"key_file": str(tmp_path / "master.key")})
    options = gateway_host.create_embedded_gateway(
        openviking_config(EmbeddedGatewayConfig(enabled=True), encryption),
        SimpleNamespace(host="127.0.0.1", port=1987, public_base_url=None),
        lambda: None,
    )
    assert isinstance(options["encryptor"], FileEncryptor)


def test_embedded_gateway_falls_back_to_server_public_url(monkeypatch):
    monkeypatch.setattr(
        "openviking_gateway.app.create_app", lambda config, **kwargs: {"config": config, **kwargs}
    )
    monkeypatch.delenv("OPENVIKING_PUBLIC_BASE_URL", raising=False)
    server = SimpleNamespace(host="0.0.0.0", port=1933, public_base_url="https://ov.example.com/")

    def public_url(gateway):
        options = gateway_host.create_embedded_gateway(
            openviking_config(gateway), server, lambda: None
        )
        return options["config"].public_url

    assert public_url(EmbeddedGatewayConfig(enabled=True)) == "https://ov.example.com"
    monkeypatch.setenv("OPENVIKING_PUBLIC_BASE_URL", "https://edge.example.com")
    assert public_url(EmbeddedGatewayConfig(enabled=True)) == "https://edge.example.com"
    own = EmbeddedGatewayConfig(enabled=True, public_url="https://gw.example.com")
    assert public_url(own) == "https://gw.example.com"


@pytest.fixture
async def model_server():
    from aiohttp import web

    app = web.Application()

    async def chat(request):
        assert request.headers["Authorization"] == "Bearer secret"
        assert (await request.json())["model"] == "gpt"
        return web.json_response(
            {
                "id": "chat-1",
                "choices": [
                    {"message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
                ],
            }
        )

    seen = []

    async def other(request):
        seen.append((request.method, request.path, request.query_string))
        return web.json_response({"ok": True})

    app.router.add_post("/v1/chat/completions", chat)
    app.router.add_route("*", "/v1/{tail:.*}", other)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield SimpleNamespace(
            url=f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1", seen=seen
        )
    finally:
        await runner.cleanup()


async def test_server_lifespan_routes_defaults_and_cors(tmp_path, monkeypatch, model_server):
    from openviking.metrics import global_api
    from openviking.observability import usage_audit
    from openviking.server import app as server_module
    from openviking.server.config import ServerConfig
    from openviking.server.mcp_endpoint import mcp
    from openviking_cli.utils.config.vlm_config import VLMConfig
    from openviking_gateway.client import VikingClient

    gateway_config = EmbeddedGatewayConfig(enabled=True, storage_path=str(tmp_path / "gateway"))
    encryption = EncryptionConfig(enabled=True, local={"key_file": str(tmp_path / "master.key")})
    # FastMCP's session manager is single-use; keep this lifespan isolated from other tests.
    monkeypatch.setattr(mcp, "_session_manager", None)
    monkeypatch.setattr(
        server_module,
        "get_openviking_config",
        lambda: openviking_config(gateway_config, encryption),
    )
    monkeypatch.setattr(global_api, "init_metrics_from_server_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(global_api, "shutdown_metrics_async", AsyncMock())
    monkeypatch.setattr(usage_audit, "init_usage_audit_from_server_config", AsyncMock())
    monkeypatch.setattr(usage_audit, "shutdown_usage_audit", AsyncMock())
    # Calls back to OV are represented by its public health response, including dev identity rules.
    # A source checkout reports a dev version, which must not trip the minimum-version gate.
    monkeypatch.setattr(
        VikingClient,
        "request",
        AsyncMock(return_value={"status": "ok", "auth_mode": "dev", "version": "0.1.dev1"}),
    )
    service = SimpleNamespace(
        vlm_resolver=SimpleNamespace(
            effective_vlm_config=AsyncMock(
                return_value=VLMConfig(
                    model="gpt", provider="openai", api_key="secret", api_base=model_server.url
                )
            )
        )
    )
    initialized = []
    monkeypatch.setattr(
        server_module,
        "get_task_tracker",
        lambda: SimpleNamespace(start_cleanup_loop=lambda: None, stop_cleanup_loop=lambda: None),
    )

    async def initialize(app, _service, config):
        # The gateway is running before deletion setup receives it.
        assert await app.state.gateway_app.state.management.list("default", "upstreams") == []
        initialized.append(True)
        await server_module._initialize_auth_plugin(app, service, config)

    monkeypatch.setattr(server_module, "_initialize_runtime_state", initialize)
    from openviking.server import body_dump_middleware

    dumped = []

    async def dump(request, call_next):
        dumped.append(request.url.path)
        return await call_next(request)

    monkeypatch.setattr(
        body_dump_middleware, "create_dump_http_body_middleware", lambda **kwargs: dump
    )
    server_config = ServerConfig(host="127.0.0.1", port=1999)
    server_config.observability.dump_body.enabled = True
    app = server_module.create_app(server_config, service=service)
    assert app.state.gateway_app.state.openviking_url == "http://127.0.0.1:1999"
    async with app.router.lifespan_context(app):
        assert initialized == [True]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            access = await client.get("/gateway/admin/access")
            assert access.status_code == 200
            assert access.json()["can_import_vlm"] is True
            assert access.json()["role"] == "root"
            upstreams = (await client.get("/gateway/admin/upstreams")).json()
            assert len(upstreams) == 1
            policy = await app.state.gateway_app.state.management.get(
                "default", "policies", "default"
            )
            assert policy["name"] == "Default"
            created = await client.post(
                "/gateway/admin/keys",
                json={"name": "test", "policy_id": "default", "upstream_ids": [upstreams[0]["id"]]},
            )
            assert created.status_code == 200, created.text
            key = created.json()["key"]
            bearer = {"Authorization": "Bearer " + key}
            response = await client.get("/gateway/v1/models", headers=bearer)
            assert response.status_code == 200, response.text
            assert response.json()["data"][0]["id"] == "gpt"
            # The server no longer claims the model-API prefixes at its root.
            for old in ("/v1/models", "/api/v3/models", "/api/v1/admin/gateway/access"):
                assert (await client.get(old, headers=bearer)).status_code == 404, old
            await app.state.gateway_app.state.management.save(
                "default",
                "policies",
                "default",
                {
                    **policy,
                    "capture": False,
                    "recall": False,
                    "profile": False,
                    "gateway_tools": False,
                },
            )
            # Both model discovery and chat pass through the server to the embedded gateway.
            response = await client.post(
                "/gateway/v1/chat/completions",
                headers={"Authorization": "Bearer " + key},
                json={"model": "gpt", "messages": [{"role": "user", "content": "hello"}]},
            )
            assert response.status_code == 200, response.text
            assert response.json()["choices"][0]["message"]["content"] == "hello"
            # Other methods, the remaining path and the query string reach the upstream unchanged.
            response = await client.patch("/gateway/v1/files/f-1?purpose=batch", headers=bearer)
            assert response.status_code == 200, response.text
            assert model_server.seen == [("PATCH", "/v1/files/f-1", "purpose=batch")]
            assert (await client.get("/admin")).status_code == 404
            assert "service" not in (await client.get("/health")).json()
            preflight = await client.options(
                "/gateway/v1/chat/completions",
                headers={
                    "Origin": "https://studio.example",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert preflight.status_code == 200
            assert preflight.headers["access-control-allow-origin"] == "https://studio.example"
            assert dumped == [
                "/v1/models",
                "/api/v3/models",
                "/api/v1/admin/gateway/access",
                "/admin",
                "/health",
            ]
        # The gateway refuses Responses WebSocket upgrades itself.
        for path in ("/gateway/v1/responses", "/gateway/api/v3/responses"):
            with pytest.raises(WebSocketDenialResponse) as exc:
                with TestClient(app).websocket_connect(path):
                    pass
            assert exc.value.status_code == 426
            assert exc.value.headers["upgrade"] == "HTTP/1.1"
    # Gateway objects are stored with the server's encryption.
    with sqlite3.connect(tmp_path / "gateway" / "management.sqlite3") as db:
        values = [row[0] for row in db.execute("SELECT value FROM objects")]
    assert values and all(value.startswith(MAGIC) for value in values)
