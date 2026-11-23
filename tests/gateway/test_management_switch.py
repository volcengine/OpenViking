"""Disabling management removes administration and Studio while keeping model traffic."""

import httpx
import pytest

from openviking_gateway.app import create_app
from openviking_gateway.config import (
    PUBLIC_OPENVIKING_URLS,
    EmbeddedGatewayConfig,
    GatewayConfig,
    StandaloneGatewayConfig,
)
from openviking_gateway.standalone import create_standalone_app

HEADERS = {"X-API-Key": "user-key"}
BODY = {"model": "model", "messages": [{"role": "user", "content": "Hello"}]}


@pytest.fixture
def gateway_config(gateway_backend):
    return {
        "management_api": False,
        "openviking_keys": True,
        "default_upstreams": {
            "chat": {
                "name": "Chat",
                "protocol": "chat",
                "base_url": gateway_backend[0] + "/v1",
                "api_key": "model-secret",
                "models": ["model"],
            }
        },
        "default_profile": {"gateway_tools": False},
    }


async def test_management_disabled_keeps_default_upstreams(running_gateway):
    app, client, admin, minted, captured, _ = running_gateway
    assert minted is None
    for method, path in (
        ("GET", "/admin/access"),
        ("GET", "/admin/upstreams"),
        ("PUT", "/admin/policies/default"),
        ("POST", "/admin/keys"),
        ("DELETE", "/admin/users/alice/data"),
        ("DELETE", "/admin/account/data"),
    ):
        response = await client.request(method, path, headers=admin)
        assert response.status_code == 404, (method, path, response.text)
    for path in ("/v1/models", "/api/v3/models", "/api/compatible/v1/models"):
        response = await client.get(path, headers=HEADERS)
        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()["data"]] == ["model"]
    response = await client.post("/v1/chat/completions", headers=HEADERS, json=BODY)
    assert response.status_code == 200, response.text
    assert captured[-1][2]["Authorization"] == "Bearer model-secret"
    assert await app.state.management.list("tenant", "upstreams") == []
    assert await app.state.management.get("tenant", "policies", "default") is None


async def test_standalone_disables_administration_and_studio(
    tmp_path, gateway_backend, gateway_config, monkeypatch
):
    (tmp_path / "index.html").write_text("<html>Studio</html>")
    monkeypatch.setenv("OPENVIKING_WEB_STUDIO_DIR", str(tmp_path))
    config = StandaloneGatewayConfig.model_validate(
        {"storage_path": str(tmp_path), "openviking_url": gateway_backend[0], **gateway_config}
    )
    app = create_standalone_app(config, studio_dir=tmp_path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client,
    ):
        for path in (
            "/gateway/admin/access",
            "/api/v1/admin/accounts",
            "/api/v1/admin/accounts/tenant/users",
            "/",
            "/studio",
            "/studio/",
            "/studio/gateway/keys",
            "/studio/assets/app.js",
        ):
            response = await client.get(path, headers={"X-API-Key": "admin-key"})
            assert response.status_code == 404, (path, response.text)
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == "openviking-gateway"
        response = await client.get("/gateway/v1/models", headers=HEADERS)
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["data"]] == ["model"]
        response = await client.post("/gateway/v1/chat/completions", headers=HEADERS, json=BODY)
        assert response.status_code == 200, response.text


async def test_embedded_management_can_be_disabled(tmp_path, gateway_backend, gateway_config):
    config = EmbeddedGatewayConfig.model_validate(
        {"enabled": True, "storage_path": str(tmp_path), **gateway_config}
    )
    app = create_app(config, openviking_url=gateway_backend[0])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, root_path="/gateway"),
            base_url="http://server/gateway",
        ) as client,
    ):
        assert (await client.get("/admin/access", headers=HEADERS)).status_code == 404
        assert (await client.get("/v1/models", headers=HEADERS)).status_code == 200


@pytest.mark.parametrize("enabled", [True, False])
def test_public_management_warning_follows_switch(tmp_path, caplog, enabled):
    config = StandaloneGatewayConfig(
        storage_path=str(tmp_path),
        openviking_url=next(iter(PUBLIC_OPENVIKING_URLS)),
        management_api=enabled,
    )
    app = create_standalone_app(config)
    warnings = app.routes[-1].app.state.warnings
    assert warnings == (["public_openviking_without_management_key"] if enabled else [])
    assert ("public_openviking_without_management_key" in caplog.text) == enabled


@pytest.mark.parametrize(
    "config_type", [GatewayConfig, EmbeddedGatewayConfig, StandaloneGatewayConfig]
)
def test_management_defaults_on_and_accepts_environment(config_type, monkeypatch):
    settings = (
        {"openviking_url": "http://ov.test"} if config_type is StandaloneGatewayConfig else {}
    )
    monkeypatch.delenv("OPENVIKING_GATEWAY_MANAGEMENT_API", raising=False)
    assert config_type.model_validate(settings).management_api is True
    monkeypatch.setenv("OPENVIKING_GATEWAY_MANAGEMENT_API", "false")
    assert config_type.model_validate({**settings, "management_api": True}).management_api is False
