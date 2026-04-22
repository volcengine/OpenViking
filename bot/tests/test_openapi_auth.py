# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for OpenAPI HTTP auth requirements."""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.openapi import OpenAPIChannel, OpenAPIChannelConfig
from vikingbot.channels.openapi_models import ChatResponse
from vikingbot.config.schema import BotChannelConfig


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def message_bus():
    return MessageBus()


def _make_client(channel: OpenAPIChannel) -> TestClient:
    app = FastAPI()
    app.include_router(channel.get_router(), prefix="/bot/v1")
    return TestClient(app)


class TestOpenAPIAuth:
    def test_health_remains_available_without_api_key(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        client = _make_client(channel)

        response = client.get("/bot/v1/health")

        assert response.status_code == 200

    def test_chat_accepts_requests_when_api_key_not_configured(self, message_bus, temp_workspace, monkeypatch):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        async def fake_handle_chat(request):
            return ChatResponse(
                session_id=request.session_id or "default", message="ok", events=None
            )

        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post("/bot/v1/chat", json={"message": "hello"})

        assert response.status_code == 200
        assert response.json()["message"] == "ok"

    def test_chat_accepts_request_with_configured_valid_api_key(
        self, message_bus, temp_workspace, monkeypatch
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=SimpleNamespace(gateway=SimpleNamespace(token="secret123")),
        )

        async def fake_handle_chat(request):
            return ChatResponse(
                session_id=request.session_id or "default", message="ok", events=None
            )

        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            headers={"X-Gateway-Token": "secret123"},
            json={"message": "hello"},
        )

        assert response.status_code == 200
        assert response.json()["message"] == "ok"

    def test_chat_rejects_when_non_localhost_and_token_not_configured(
        self, message_bus, temp_workspace
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=SimpleNamespace(gateway=SimpleNamespace(host="0.0.0.0", token="")),
        )
        client = _make_client(channel)

        response = client.post("/bot/v1/chat", json={"message": "hello"})

        assert response.status_code == 503
        assert response.json()["detail"] == "OpenAPI gateway token is required when host is non-localhost"

    def test_bot_channel_accepts_requests_without_channel_api_key(
        self, message_bus, temp_workspace, monkeypatch
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        channel._bot_configs["alpha"] = BotChannelConfig(id="alpha", api_key="")

        async def fake_handle_bot_chat(channel_id, request):
            return ChatResponse(
                session_id=request.session_id or "default", message=f"ok:{channel_id}"
            )

        monkeypatch.setattr(channel, "_handle_bot_chat", fake_handle_bot_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat/channel",
            json={"message": "hello", "channel_id": "alpha"},
        )

        assert response.status_code == 200
        assert response.json()["message"] == "ok:alpha"

    def test_bot_channel_requires_global_gateway_token_when_configured(
        self, message_bus, temp_workspace, monkeypatch
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=SimpleNamespace(gateway=SimpleNamespace(token="secret123")),
        )
        channel._bot_configs["alpha"] = BotChannelConfig(id="alpha", api_key="bot-secret")

        async def fake_handle_bot_chat(channel_id, request):
            return ChatResponse(
                session_id=request.session_id or "default", message=f"ok:{channel_id}"
            )

        monkeypatch.setattr(channel, "_handle_bot_chat", fake_handle_bot_chat)
        client = _make_client(channel)

        unauthorized = client.post(
            "/bot/v1/chat/channel",
            json={"message": "hello", "channel_id": "alpha"},
        )
        assert unauthorized.status_code == 401
        assert unauthorized.json()["detail"] == "X-Gateway-Token header required"

        authorized = client.post(
            "/bot/v1/chat/channel",
            headers={"X-Gateway-Token": "secret123"},
            json={"message": "hello", "channel_id": "alpha"},
        )
        assert authorized.status_code == 200
        assert authorized.json()["message"] == "ok:alpha"
