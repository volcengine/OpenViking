# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for bot proxy endpoint auth enforcement."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import openviking.server.routers.bot as bot_router_module
from openviking.server.auth.plugins import DevAuthPlugin, TrustedAuthPlugin
from openviking.server.config import ServerConfig
from openviking.server.identity import AuthMode


def test_set_bot_api_key_updates_module_state():
    bot_router_module.set_bot_api_key("gateway-secret")
    assert bot_router_module.BOT_API_KEY == "gateway-secret"

    bot_router_module.set_bot_api_key("")
    assert bot_router_module.BOT_API_KEY == ""


async def test_create_bot_proxy_client_disables_env_proxy():
    async with bot_router_module._create_bot_proxy_client() as client:
        assert isinstance(client, httpx.AsyncClient)
        assert client._trust_env is False


@pytest.mark.asyncio
async def test_feedback_proxy_forwards_request(monkeypatch):
    forwarded = {}

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.text = '{"accepted": true}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"accepted": True, "response_id": "resp-123"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers, timeout):
            forwarded["url"] = url
            forwarded["json"] = json
            forwarded["headers"] = headers
            forwarded["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(bot_router_module, "BOT_API_URL", "http://127.0.0.1:18790")
    monkeypatch.setattr(bot_router_module, "BOT_API_KEY", "gateway-secret")
    monkeypatch.setattr(bot_router_module, "_create_bot_proxy_client", lambda: FakeClient())

    app = FastAPI()
    app.state.config = SimpleNamespace(get_effective_auth_mode=lambda: AuthMode.DEV)
    app.state.auth_plugin = DevAuthPlugin()
    app.include_router(bot_router_module.router, prefix="/bot/v1")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/bot/v1/feedback",
            json={
                "session_id": "session-1",
                "response_id": "resp-123",
                "feedback_type": "thumb_up",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "response_id": "resp-123"}
    assert forwarded["url"] == "http://127.0.0.1:18790/bot/v1/feedback"
    assert forwarded["json"]["response_id"] == "resp-123"
    assert forwarded["headers"]["X-Gateway-Token"] == "gateway-secret"
    assert forwarded["timeout"] == 30.0


@pytest.mark.asyncio
async def test_chat_proxy_attaches_authenticated_openviking_connection(monkeypatch):
    forwarded = {}

    class FakeResponse:
        status_code = 200
        text = '{"session_id": "session-1", "message": "ok"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"session_id": "session-1", "message": "ok"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers, timeout):
            forwarded["url"] = url
            forwarded["json"] = json
            forwarded["headers"] = headers
            forwarded["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(bot_router_module, "BOT_API_URL", "http://127.0.0.1:18790")
    monkeypatch.setattr(bot_router_module, "BOT_API_KEY", "gateway-secret")
    monkeypatch.setattr(bot_router_module, "_create_bot_proxy_client", lambda: FakeClient())

    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="trusted", host="127.0.0.1", port=1944)
    app.state.auth_plugin = TrustedAuthPlugin()
    app.include_router(bot_router_module.router, prefix="/bot/v1")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/bot/v1/chat",
            headers={
                "X-API-Key": "active-user-key",
                "X-OpenViking-Account": "acct",
                "X-OpenViking-User": "alice",
            },
            json={"message": "hello", "user_id": "ignored-by-proxy-identity"},
        )

    assert response.status_code == 200
    assert forwarded["url"] == "http://127.0.0.1:18790/bot/v1/chat"
    assert forwarded["json"]["openviking_connection"] == {
        "api_key": "active-user-key",
        "account_id": "acct",
        "user_id": "alice",
        "agent_id": "web-playground",
        "role": "user",
        "api_key_type": "root",
        "server_url": "http://127.0.0.1:1944",
        "namespace_policy": {
            "isolate_user_scope_by_agent": False,
            "isolate_agent_scope_by_user": False,
        },
    }
    assert forwarded["headers"]["X-Gateway-Token"] == "gateway-secret"
    assert forwarded["timeout"] == 300.0


@pytest.mark.asyncio
async def test_chat_proxy_forwards_trusted_request_without_root_api_key(monkeypatch):
    forwarded = {}

    class FakeResponse:
        status_code = 200
        text = '{"session_id": "session-1", "message": "ok"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"session_id": "session-1", "message": "ok"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers, timeout):
            forwarded["url"] = url
            forwarded["json"] = json
            forwarded["headers"] = headers
            forwarded["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(bot_router_module, "BOT_API_URL", "http://127.0.0.1:18790")
    monkeypatch.setattr(bot_router_module, "BOT_API_KEY", "")
    monkeypatch.setattr(bot_router_module, "_create_bot_proxy_client", lambda: FakeClient())

    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="trusted", host="127.0.0.1", port=1955)
    app.state.auth_plugin = TrustedAuthPlugin()
    app.include_router(bot_router_module.router, prefix="/bot/v1")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/bot/v1/chat",
            headers={
                "X-OpenViking-Account": "acct",
                "X-OpenViking-User": "alice",
            },
            json={"message": "hello"},
        )

    assert response.status_code == 200
    assert forwarded["url"] == "http://127.0.0.1:18790/bot/v1/chat"
    assert "api_key" not in forwarded["json"]["openviking_connection"]
    assert forwarded["json"]["openviking_connection"] == {
        "account_id": "acct",
        "user_id": "alice",
        "agent_id": "web-playground",
        "role": "user",
        "api_key_type": "root",
        "server_url": "http://127.0.0.1:1955",
        "namespace_policy": {
            "isolate_user_scope_by_agent": False,
            "isolate_agent_scope_by_user": False,
        },
    }
    assert "X-Gateway-Token" not in forwarded["headers"]
    assert forwarded["timeout"] == 300.0
