# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for OpenAPI HTTP auth requirements."""

import asyncio
from datetime import datetime
import json
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from vikingbot.bus.events import OutboundMessage, ResponseCompletedEvent
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.openapi import OpenAPIChannel, OpenAPIChannelConfig
from vikingbot.channels.openapi_models import ChatResponse
from vikingbot.config.schema import BotChannelConfig, SessionKey


class FakeLangfuseRecorder:
    def __init__(self):
        self.enabled = True
        self.events = []
        self.scores = []

    def log_event(self, name, **kwargs):
        self.events.append({"name": name, **kwargs})

    def log_score(self, name, value, **kwargs):
        self.scores.append({"name": name, "value": value, **kwargs})


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
                session_id=request.session_id or "default",
                response_id="resp-123",
                message="ok",
                events=None,
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
        assert response.json()["response_id"] == "resp-123"

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
                session_id=request.session_id or "default",
                response_id="resp-bot-123",
                message=f"ok:{channel_id}",
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
        assert response.json()["response_id"] == "resp-bot-123"

    def test_feedback_rejects_requests_when_api_key_not_configured(
        self, message_bus, temp_workspace
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(api_key=""),
            message_bus,
            workspace_path=temp_workspace,
        )
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/feedback",
            json={"response_id": "resp-1", "rating": "positive"},
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "OpenAPI channel API key is not configured"

    def test_feedback_accepts_and_persists_submission(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(api_key="secret123"),
            message_bus,
            workspace_path=temp_workspace,
        )
        recorder = FakeLangfuseRecorder()
        channel._langfuse = recorder
        channel._response_index["resp-123"] = {
            "response_id": "resp-123",
            "session_id": "session-1",
            "session_key": "cli__default__session-1",
            "channel": "cli__default",
            "user_id": "user-1",
        }
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/feedback",
            headers={"X-API-Key": "secret123"},
            json={
                "response_id": "resp-123",
                "rating": "positive",
                "comment": "helpful",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["response_id"] == "resp-123"
        assert body["accepted"] is True

        feedback_lines = channel._feedback_file.read_text().strip().splitlines()
        assert len(feedback_lines) == 1

        record = json.loads(feedback_lines[0])
        assert record["event_type"] == "feedback_submitted"
        assert record["response_id"] == "resp-123"
        assert record["rating"] == "positive"
        assert record["comment"] == "helpful"
        assert record["session_id"] == "session-1"
        assert record["user_id"] == "user-1"
        assert record["session_key"] == "cli__default__session-1"

        assert len(recorder.events) == 1
        assert recorder.events[0]["name"] == "feedback_submitted"
        assert recorder.events[0]["session_id"] == "cli__default__session-1"
        assert recorder.events[0]["user_id"] == "user-1"
        assert recorder.events[0]["metadata"]["response_id"] == "resp-123"

        assert len(recorder.scores) == 1
        assert recorder.scores[0]["name"] == "user_feedback"
        assert recorder.scores[0]["value"] == 1.0
        assert recorder.scores[0]["metadata"]["response_id"] == "resp-123"

    def test_feedback_uses_persisted_response_index_after_restart(
        self, message_bus, temp_workspace
    ):
        first_channel = OpenAPIChannel(
            OpenAPIChannelConfig(api_key="secret123"),
            message_bus,
            workspace_path=temp_workspace,
        )
        session_key = SessionKey(type="cli", channel_id="default", chat_id="session-2")

        from vikingbot.channels.openapi import PendingResponse

        first_channel._pending["session-2"] = PendingResponse()

        response_msg = OutboundMessage(
            session_key=session_key,
            content="persisted response",
            response_id="resp-persisted",
            response_completed=ResponseCompletedEvent(
                response_id="resp-persisted",
                session_id=session_key.safe_name(),
                channel=session_key.channel_key(),
                user_id="user-2",
                token_usage={"total_tokens": 12},
                time_cost=1.2,
                iteration=2,
                tools_used_names=["search"],
            ),
        )

        asyncio.run(first_channel.send(response_msg))

        second_channel = OpenAPIChannel(
            OpenAPIChannelConfig(api_key="secret123"),
            message_bus,
            workspace_path=temp_workspace,
        )
        client = _make_client(second_channel)

        response = client.post(
            "/bot/v1/feedback",
            headers={"X-API-Key": "secret123"},
            json={
                "response_id": "resp-persisted",
                "rating": "negative",
                "comment": "not correct",
            },
        )

        assert response.status_code == 200
        record = json.loads(second_channel._feedback_file.read_text().strip().splitlines()[-1])
        assert record["response_id"] == "resp-persisted"
        assert record["session_id"] == "session-2"
        assert record["session_key"] == session_key.safe_name()
        assert record["user_id"] == "user-2"
        assert (
            second_channel._response_index["resp-persisted"]["event_type"] == "response_completed"
        )

    def test_response_persistence_syncs_langfuse_event(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(api_key="secret123"),
            message_bus,
            workspace_path=temp_workspace,
        )
        recorder = FakeLangfuseRecorder()
        channel._langfuse = recorder

        session_key = SessionKey(type="cli", channel_id="default", chat_id="session-sync")
        response_msg = OutboundMessage(
            session_key=session_key,
            content="synced response",
            response_id="resp-sync",
            response_completed=ResponseCompletedEvent(
                response_id="resp-sync",
                session_id=session_key.safe_name(),
                channel=session_key.channel_key(),
                user_id="user-sync",
                token_usage={"total_tokens": 7},
                time_cost=0.7,
                iteration=1,
                tools_used_names=["search"],
            ),
        )

        asyncio.run(channel._store_response(response_msg))

        response_lines = channel._responses_file.read_text().strip().splitlines()
        assert len(response_lines) == 1

        record = json.loads(response_lines[0])
        assert record["event_type"] == "response_completed"
        assert record["response_id"] == "resp-sync"
        assert record["session_key"] == session_key.safe_name()

        assert len(recorder.events) == 1
        assert recorder.events[0]["name"] == "response_completed"
        assert recorder.events[0]["session_id"] == session_key.safe_name()
        assert recorder.events[0]["user_id"] == "user-sync"
        assert recorder.events[0]["metadata"]["token_usage"] == {"total_tokens": 7}
        assert recorder.scores == []

    def test_follow_up_message_persists_outcome_evaluation(
        self, message_bus, temp_workspace, monkeypatch
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(api_key="secret123"),
            message_bus,
            workspace_path=temp_workspace,
        )
        recorder = FakeLangfuseRecorder()
        channel._langfuse = recorder
        channel._sessions["session-3"] = {
            "user_id": "user-3",
            "created_at": datetime.now(),
            "last_active": datetime.now(),
            "message_count": 1,
            "messages": [],
        }
        channel._response_index["resp-previous"] = {
            "event_type": "response_completed",
            "response_id": "resp-previous",
            "session_id": "session-3",
            "session_key": "cli__default__session-3",
            "channel": "cli__default",
            "user_id": "user-3",
            "timestamp": datetime.now().isoformat(),
        }

        async def fake_publish_inbound(msg):
            pending = channel._pending[msg.session_key.chat_id]
            pending.response_id = "resp-next"
            pending.set_final("next response")

        monkeypatch.setattr(message_bus, "publish_inbound", fake_publish_inbound)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            headers={"X-API-Key": "secret123"},
            json={
                "session_id": "session-3",
                "user_id": "user-3",
                "message": "还有一个问题",
            },
        )

        assert response.status_code == 200

        outcome_lines = channel._outcomes_file.read_text().strip().splitlines()
        assert len(outcome_lines) == 1

        record = json.loads(outcome_lines[0])
        assert record["event_type"] == "response_outcome_evaluated"
        assert record["response_id"] == "resp-previous"
        assert record["session_id"] == "session-3"
        assert record["user_id"] == "user-3"
        assert record["reask_within_window"] is True
        assert record["one_turn_resolution"] is False
        assert record["outcome_label"] == "follow_up_needed"
        assert record["session_key"] == "cli__default__session-3"

        assert len(recorder.events) == 1
        assert recorder.events[0]["name"] == "response_outcome_evaluated"
        assert recorder.events[0]["session_id"] == "cli__default__session-3"
        assert recorder.events[0]["metadata"]["response_id"] == "resp-previous"

        assert len(recorder.scores) == 1
        assert recorder.scores[0]["name"] == "reask_within_window"
        assert recorder.scores[0]["value"] == 1.0
        assert recorder.scores[0]["metadata"]["outcome_label"] == "follow_up_needed"
