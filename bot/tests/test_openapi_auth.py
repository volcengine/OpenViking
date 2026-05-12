# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for OpenAPI HTTP auth requirements."""

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from vikingbot.bus.events import OutboundEventType, OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.openapi import OpenAPIChannel, OpenAPIChannelConfig, PendingResponse
from vikingbot.channels.openapi_models import ChatResponse
from vikingbot.config.schema import BotChannelConfig, SessionKey


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

    def test_chat_accepts_requests_when_api_key_not_configured(
        self, message_bus, temp_workspace, monkeypatch
    ):
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
        assert (
            response.json()["detail"]
            == "OpenAPI gateway token is required when host is non-localhost"
        )

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

    @pytest.mark.asyncio
    async def test_send_tracks_response_id_in_final_openapi_response(
        self, message_bus, temp_workspace
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        pending = PendingResponse()
        channel._pending["session-1"] = pending

        await channel.send(
            OutboundMessage(
                session_key=SessionKey(type="cli", channel_id="default", chat_id="session-1"),
                content="hello",
                event_type=OutboundEventType.RESPONSE,
                response_id="resp-123",
                metadata={"relevant_memories": "memory"},
            )
        )

        assert pending.final_content == "hello"
        assert pending.response_id == "resp-123"
        assert pending.relevant_memories == "memory"
        assert len(pending.events) == 1
        assert pending.events[0]["type"] == "response"
        assert pending.events[0]["data"] == {"content": "hello", "response_id": "resp-123"}

    def test_feedback_persists_event_and_emits_feedback_submitted(
        self, message_bus, temp_workspace
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        session_key = SessionKey(type="cli", channel_id="default", chat_id="session-1")
        session = channel._session_manager.get_or_create(session_key)
        session.add_message(
            "assistant",
            "hello",
            sender_id="user-1",
            response_id="resp-123",
            timestamp="2026-04-30T00:00:00",
        )
        asyncio.run(channel._session_manager.save(session))

        client = _make_client(channel)
        response = client.post(
            "/bot/v1/feedback",
            json={
                "session_id": "session-1",
                "response_id": "resp-123",
                "feedback_type": "thumb_up",
                "feedback_text": "helpful",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["response_id"] == "resp-123"
        assert body["session_id"] == "session-1"
        assert body["feedback_type"] == "thumb_up"
        assert message_bus.outbound_size == 2

        first_outbound = asyncio.run(message_bus.consume_outbound())
        second_outbound = asyncio.run(message_bus.consume_outbound())
        assert first_outbound.event_type == OutboundEventType.RESPONSE_OUTCOME_EVALUATED
        assert first_outbound.response_id == "resp-123"
        assert (
            first_outbound.metadata["response_outcome_evaluated"]["outcome_label"]
            == "positive_feedback"
        )
        assert second_outbound.event_type == OutboundEventType.FEEDBACK_SUBMITTED
        assert second_outbound.response_id == "resp-123"
        assert second_outbound.metadata["feedback_submitted"]["feedback_type"] == "thumb_up"
        assert second_outbound.metadata["feedback_submitted"]["feedback_text"] == "helpful"

        session_path = temp_workspace / "sessions" / "cli__default__session-1.jsonl"
        lines = session_path.read_text(encoding="utf-8").splitlines()
        metadata = json.loads(lines[0])
        assert metadata["metadata"]["feedback_events"][0]["response_id"] == "resp-123"
        assert metadata["metadata"]["feedback_events"][0]["feedback_type"] == "thumb_up"
        assert (
            metadata["metadata"]["response_outcomes"]["resp-123"]["outcome_label"]
            == "positive_feedback"
        )

    def test_feedback_requires_existing_response(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/feedback",
            json={
                "session_id": "missing-session",
                "response_id": "missing-response",
                "feedback_type": "thumb_down",
            },
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Response not found"

    def test_feedback_reloads_session_after_stale_cached_miss(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        session_key = SessionKey(type="cli", channel_id="default", chat_id="session-1")

        stale_session = channel._session_manager.get_or_create(session_key)
        assert stale_session.messages == []

        writer_manager = channel._session_manager.__class__(channel._session_manager.bot_data_path)
        writer_session = writer_manager.get_or_create(session_key)
        writer_session.add_message(
            "assistant",
            "hello",
            sender_id="user-1",
            response_id="resp-123",
            timestamp="2026-04-30T00:00:00",
        )
        asyncio.run(writer_manager.save(writer_session))

        client = _make_client(channel)
        response = client.post(
            "/bot/v1/feedback",
            json={
                "session_id": "session-1",
                "response_id": "resp-123",
                "feedback_type": "thumb_up",
            },
        )

        assert response.status_code == 200
        assert response.json()["accepted"] is True

    def test_feedback_preserves_messages_written_after_stale_cache_read(
        self, message_bus, temp_workspace
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        session_key = SessionKey(type="cli", channel_id="default", chat_id="session-1")

        stale_session = channel._session_manager.get_or_create(session_key)
        stale_session.add_message(
            "assistant",
            "hello",
            sender_id="user-1",
            response_id="resp-123",
            timestamp="2026-04-30T00:00:00",
        )
        asyncio.run(channel._session_manager.save(stale_session))

        writer_manager = channel._session_manager.__class__(channel._session_manager.bot_data_path)
        writer_session = writer_manager.get_or_create(session_key)
        writer_session.add_message(
            "user",
            "follow up",
            sender_id="user-1",
            timestamp="2026-04-30T00:01:00",
        )
        writer_session.add_message(
            "assistant",
            "new reply",
            sender_id="user-1",
            response_id="resp-456",
            timestamp="2026-04-30T00:02:00",
        )
        asyncio.run(writer_manager.save(writer_session))

        stale_session.metadata["local_only"] = True

        client = _make_client(channel)
        response = client.post(
            "/bot/v1/feedback",
            json={
                "session_id": "session-1",
                "response_id": "resp-123",
                "feedback_type": "thumb_up",
            },
        )

        assert response.status_code == 200

        session_path = temp_workspace / "sessions" / "cli__default__session-1.jsonl"
        lines = session_path.read_text(encoding="utf-8").splitlines()
        metadata = json.loads(lines[0])
        messages = [json.loads(line) for line in lines[1:]]

        assert metadata["metadata"]["feedback_events"][0]["response_id"] == "resp-123"
        assert "local_only" not in metadata["metadata"]
        assert [
            message.get("response_id") for message in messages if message["role"] == "assistant"
        ] == [
            "resp-123",
            "resp-456",
        ]
        assert messages[-1]["content"] == "new reply"
