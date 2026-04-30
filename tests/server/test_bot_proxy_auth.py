# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for bot proxy endpoint auth enforcement."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import openviking.server.routers.bot as bot_router_module
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
