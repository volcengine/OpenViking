# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for issue #6145: VikingBot streaming provider-recovery.

Covers the path from the agent loop through the bus to the OpenAPI SSE
stream, plus the keepalive liveness rule that replaced the terminal 300s
queue-idle cutoff.
"""

import asyncio
from types import SimpleNamespace

import pytest
from vikingbot.agent.loop import AgentLoop
from vikingbot.bus.events import OutboundEventType, OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.openapi import (
    SSE_KEEPALIVE_INTERVAL_SECONDS,
    SSE_MAX_STREAM_LIFETIME_SECONDS,
    OpenAPIChannel,
    OpenAPIChannelConfig,
    PendingResponse,
)
from vikingbot.config.schema import Config, SessionKey


@pytest.fixture
def temp_workspace(tmp_path):
    return tmp_path


@pytest.fixture
def message_bus():
    return MessageBus()


def make_recovery_outbound(session_key: SessionKey) -> OutboundMessage:
    return OutboundMessage(
        session_key=session_key,
        content="",
        event_type=OutboundEventType.PROVIDER_RECOVERY,
        metadata={"recovery": {"reason": "primary_failed", "error_kind": "rate_limit_error"}},
    )


class TestOpenAPIChannelRecoveryEvent:
    @pytest.mark.asyncio
    async def test_recovery_outbound_reaches_pending_stream_not_content(
        self, message_bus, temp_workspace
    ):
        channel = OpenAPIChannel(OpenAPIChannelConfig(), message_bus, temp_workspace)
        session_key = SessionKey(type="cli", channel_id="default", chat_id="local:session-1")
        pending = PendingResponse()
        channel._pending[session_key.chat_id] = pending

        await channel.send(make_recovery_outbound(session_key))

        # Structured, ephemeral status event on the stream...
        assert pending.events[0]["type"] == "provider_recovery"
        assert pending.events[0]["data"] == {
            "reason": "primary_failed",
            "error_kind": "rate_limit_error",
        }
        stream_event = await pending.stream_queue.get()
        assert stream_event.event.value == "provider_recovery"
        assert stream_event.data == {
            "reason": "primary_failed",
            "error_kind": "rate_limit_error",
        }
        # ...but never assistant content, and the stream stays open.
        assert pending.final_content is None
        assert not pending.event.is_set()

    @pytest.mark.asyncio
    async def test_recovery_outbound_reaches_bot_channel_pending(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(OpenAPIChannelConfig(), message_bus, temp_workspace)
        channel._bot_pending["chan"] = {}
        channel._bot_sessions = {"chan": {}}
        session_key = SessionKey(type="bot_api", channel_id="chan", chat_id="s1")
        pending = PendingResponse()
        channel._bot_pending["chan"]["s1"] = pending

        await channel.send(make_recovery_outbound(session_key))

        assert pending.events[0]["type"] == "provider_recovery"
        stream_event = await pending.stream_queue.get()
        assert stream_event.event.value == "provider_recovery"
        assert pending.final_content is None


class TestSseKeepalive:
    @pytest.mark.asyncio
    async def test_idle_stream_keeps_alive_and_survives_provider_wait(
        self, message_bus, temp_workspace, monkeypatch
    ):
        """Idle time must yield keepalive comments, not a terminal timeout."""
        import vikingbot.channels.openapi as openapi_module

        monkeypatch.setattr(openapi_module, "SSE_KEEPALIVE_INTERVAL_SECONDS", 0.02)
        monkeypatch.setattr(openapi_module, "SSE_MAX_STREAM_LIFETIME_SECONDS", 3600.0)
        channel = OpenAPIChannel(OpenAPIChannelConfig(), message_bus, temp_workspace)
        pending = PendingResponse()

        async def produce():
            # Keep the stream idle far longer than the old 300s cutoff would
            # represent (scaled here), then deliver an event.
            await asyncio.sleep(0.08)
            await pending.add_event("content_delta", "late")

        task = asyncio.create_task(produce())
        frames = []
        async for frame in channel._stream_pending_events(pending):
            frames.append(frame)
            if len(frames) >= 2:
                break
        await task
        await pending.close_stream()
        rest = [frame async for frame in channel._stream_pending_events(pending)]

        assert all(frame == ": keepalive\n\n" for frame in frames), frames
        assert any('"content_delta"' in frame for frame in rest)

    @pytest.mark.asyncio
    async def test_stream_lifetime_stays_bounded(self, message_bus, temp_workspace, monkeypatch):
        """A pending whose agent died still ends after the bounded lifetime."""
        import vikingbot.channels.openapi as openapi_module

        monkeypatch.setattr(openapi_module, "SSE_KEEPALIVE_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(openapi_module, "SSE_MAX_STREAM_LIFETIME_SECONDS", 0.05)
        channel = OpenAPIChannel(OpenAPIChannelConfig(), message_bus, temp_workspace)
        pending = PendingResponse()

        frames = [frame async for frame in channel._stream_pending_events(pending)]

        assert frames
        assert frames[-1].startswith("data: ")
        assert '"error"' in frames[-1] and "timeout" in frames[-1]
        assert frames[:-1] == [": keepalive\n\n"] * len(frames[:-1])

    def test_keepalive_cannot_expire_before_default_provider_timeout(self):
        assert SSE_MAX_STREAM_LIFETIME_SECONDS >= 600.0
        assert 0 < SSE_KEEPALIVE_INTERVAL_SECONDS < SSE_MAX_STREAM_LIFETIME_SECONDS


class TestAgentLoopPublishesRecovery:
    @pytest.mark.asyncio
    async def test_provider_recovery_event_is_published_to_bus(self, tmp_path, monkeypatch):
        monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
        monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
        monkeypatch.setattr(
            "vikingbot.agent.loop.SubagentManager", lambda **kwargs: SimpleNamespace()
        )

        bus = MessageBus()
        config = Config(storage_workspace=str(tmp_path / "data"))
        session_key = SessionKey(type="cli", channel_id="default", chat_id="s1")

        async def fake_chat_stream(**kwargs):
            from vikingbot.providers.base import LLMResponse, LLMStreamEvent

            yield LLMStreamEvent(
                type="provider_recovery",
                recovery={"reason": "primary_failed", "error_kind": "rate_limit_error"},
            )
            yield LLMStreamEvent(
                type="response", response=LLMResponse(content="done", finish_reason="stop")
            )

        provider = SimpleNamespace(
            get_default_model=lambda: "fake-model",
            chat_stream=fake_chat_stream,
        )
        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=tmp_path / "workspace",
            model=config.agents.model,
            temperature=config.agents.temperature,
            config=config,
        )

        response, streamed_content, streamed_reasoning = await loop._chat_with_stream_events(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            session_key=session_key,
            publish_events=True,
        )

        assert response.content == "done"
        assert streamed_content is False
        msg = await bus.consume_outbound()
        assert msg.event_type == OutboundEventType.PROVIDER_RECOVERY
        assert msg.content == ""
        assert msg.metadata == {
            "recovery": {"reason": "primary_failed", "error_kind": "rate_limit_error"}
        }
