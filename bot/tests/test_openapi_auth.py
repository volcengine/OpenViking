# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for OpenAPI HTTP auth requirements."""

import tempfile
from pathlib import Path

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
            OpenAPIChannelConfig(api_key=""),
            message_bus,
            workspace_path=temp_workspace,
        )
        client = _make_client(channel)

        response = client.get("/bot/v1/health")

        assert response.status_code == 200

    def test_chat_rejects_requests_when_api_key_not_configured(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(api_key=""),
            message_bus,
            workspace_path=temp_workspace,
        )
        client = _make_client(channel)

        response = client.post("/bot/v1/chat", json={"message": "hello"})

        assert response.status_code == 503
        assert response.json()["detail"] == "OpenAPI channel API key is not configured"

    def test_chat_accepts_request_with_configured_valid_api_key(
        self, message_bus, temp_workspace, monkeypatch
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(api_key="secret123"),
            message_bus,
            workspace_path=temp_workspace,
        )

        async def fake_handle_chat(request):
            return ChatResponse(
                session_id=request.session_id or "default", message="ok", events=None
            )

        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            headers={"X-API-Key": "secret123"},
            json={"message": "hello"},
        )

        assert response.status_code == 200
        assert response.json()["message"] == "ok"

    def test_bot_channel_rejects_requests_when_channel_api_key_not_configured(
        self, message_bus, temp_workspace
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(api_key="gateway-secret"),
            message_bus,
            workspace_path=temp_workspace,
        )
        channel._bot_configs["alpha"] = BotChannelConfig(id="alpha", api_key="")
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat/channel",
            json={"message": "hello", "channel_id": "alpha"},
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "Bot channel 'alpha' API key is not configured"

    def test_bot_channel_accepts_request_with_valid_api_key(
        self, message_bus, temp_workspace, monkeypatch
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(api_key="gateway-secret"),
            message_bus,
            workspace_path=temp_workspace,
        )
        channel._bot_configs["alpha"] = BotChannelConfig(id="alpha", api_key="bot-secret")

        async def fake_handle_bot_chat(channel_id, request):
            return ChatResponse(
                session_id=request.session_id or "default", message=f"ok:{channel_id}"
            )

        monkeypatch.setattr(channel, "_handle_bot_chat", fake_handle_bot_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat/channel",
            headers={"X-API-Key": "bot-secret"},
            json={"message": "hello", "channel_id": "alpha"},
        )

        assert response.status_code == 200
        assert response.json()["message"] == "ok:alpha"
