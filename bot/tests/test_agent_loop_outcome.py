from datetime import datetime
from pathlib import Path

import pytest
from vikingbot.agent.loop import AgentLoop
from vikingbot.bus.events import InboundMessage, OutboundEventType
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import Config, SessionKey
from vikingbot.heartbeat.service import HEARTBEAT_METADATA_KEY
from vikingbot.providers.base import LLMProvider


class _FakeProvider(LLMProvider):
    async def chat(self, *args, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("provider.chat should not be called in no-reply outcome test")

    def get_default_model(self) -> str:
        return "fake-model"


class _FakeSubagentManager:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.mark.asyncio
async def test_agent_loop_evaluates_previous_response_outcome_before_new_user_turn(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    bus = MessageBus()
    config = Config(storage_workspace=str(temp_dir))
    loop = AgentLoop(
        bus=bus,
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=config,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-1")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message(
        "assistant",
        "hello",
        sender_id="user-1",
        response_id="resp-123",
        timestamp="2026-04-30T00:00:00",
    )
    await loop.sessions.save(session)

    response = await loop._process_message(
        InboundMessage(
            session_key=session_key,
            sender_id="user-1",
            content="that did not help",
            need_reply=False,
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        )
    )

    assert response is not None
    assert response.event_type == OutboundEventType.NO_REPLY
    assert bus.outbound_size == 1

    outcome_event = await bus.consume_outbound()
    assert outcome_event.event_type == OutboundEventType.RESPONSE_OUTCOME_EVALUATED
    assert outcome_event.response_id == "resp-123"
    assert outcome_event.metadata["response_outcome_evaluated"]["outcome_label"] == "reasked"
    assert outcome_event.metadata["response_outcome_evaluated"]["reask_within_10m"] is True

    persisted_session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    assert persisted_session.metadata["response_outcomes"]["resp-123"]["outcome_label"] == "reasked"


@pytest.mark.asyncio
async def test_agent_loop_ignores_heartbeat_when_evaluating_previous_response_outcome(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    bus = MessageBus()
    config = Config(storage_workspace=str(temp_dir))
    loop = AgentLoop(
        bus=bus,
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=config,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-1")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=False)
    session.add_message(
        "assistant",
        "hello",
        sender_id="user-1",
        response_id="resp-123",
        timestamp="2026-04-30T00:00:00",
    )
    await loop.sessions.save(session)

    response = await loop._process_message(
        InboundMessage(
            session_key=session_key,
            sender_id="user-1",
            content="Read HEARTBEAT.md if needed",
            need_reply=False,
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
            metadata={HEARTBEAT_METADATA_KEY: True},
        )
    )

    assert response is not None
    assert response.event_type == OutboundEventType.NO_REPLY
    assert bus.outbound_size == 0

    persisted_session = loop.sessions.get_or_create(session_key, skip_heartbeat=False)
    assert "response_outcomes" not in persisted_session.metadata
