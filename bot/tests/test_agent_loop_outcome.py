import json
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


class _ClassifierProvider(_FakeProvider):
    def __init__(self, classifier_response: str):
        self.classifier_response = classifier_response
        self.calls = []

    async def chat(self, *args, **kwargs):
        from vikingbot.providers.base import LLMResponse

        self.calls.append({"args": args, "kwargs": kwargs})
        return LLMResponse(content=self.classifier_response)


class _FakeSubagentManager:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeLangfuseClient:
    def __init__(self):
        self.calls = []
        self.outcome_calls = []

    def update_generation_metadata(self, response_id, metadata):
        self.calls.append((response_id, metadata))
        return metadata

    def update_response_outcome(self, response_id, outcome_label, payload):
        self.outcome_calls.append((response_id, outcome_label, payload))
        return payload


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
            content="why is it still failing?",
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
async def test_agent_loop_records_negative_natural_language_feedback(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

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
            content="这完全没帮助",
            need_reply=False,
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        )
    )

    assert response is not None
    assert response.event_type == OutboundEventType.NO_REPLY
    assert bus.outbound_size == 2

    feedback_event = await bus.consume_outbound()
    outcome_event = await bus.consume_outbound()
    assert feedback_event.event_type == OutboundEventType.FEEDBACK_SUBMITTED
    assert feedback_event.response_id == "resp-123"
    assert feedback_event.metadata["feedback_submitted"]["feedback_type"] == "thumb_down"
    assert feedback_event.metadata["feedback_submitted"]["feedback_reason"] == "natural_language"

    assert outcome_event.event_type == OutboundEventType.RESPONSE_OUTCOME_EVALUATED
    assert outcome_event.metadata["response_outcome_evaluated"]["outcome_label"] == "negative_feedback"

    persisted_session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    assert persisted_session.metadata["feedback_events"][0]["feedback_type"] == "thumb_down"
    assert persisted_session.metadata["response_outcomes"]["resp-123"]["outcome_label"] == "negative_feedback"
    assert fake_langfuse.outcome_calls[0][1] == "negative_feedback"


@pytest.mark.asyncio
async def test_agent_loop_does_not_record_feedback_for_plain_follow_up_question(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

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
            content="为什么还是不行，下一步怎么做？",
            need_reply=False,
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        )
    )

    assert response is not None
    assert response.event_type == OutboundEventType.NO_REPLY
    assert bus.outbound_size == 1

    outcome_event = await bus.consume_outbound()
    assert outcome_event.event_type == OutboundEventType.RESPONSE_OUTCOME_EVALUATED
    assert outcome_event.metadata["response_outcome_evaluated"]["outcome_label"] == "reasked"

    persisted_session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    assert persisted_session.metadata.get("feedback_events") in (None, [])


@pytest.mark.asyncio
async def test_agent_loop_uses_llm_fallback_for_ambiguous_positive_feedback(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    provider = _ClassifierProvider(
        '{"is_feedback": true, "sentiment": "positive", "confidence": 0.93}'
    )
    bus = MessageBus()
    config = Config(storage_workspace=str(temp_dir))
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=temp_dir / "workspace",
        config=config,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-1")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message(
        "assistant",
        "给你一个修复步骤",
        sender_id="user-1",
        response_id="resp-123",
        timestamp="2026-04-30T00:00:00",
    )
    await loop.sessions.save(session)

    response = await loop._process_message(
        InboundMessage(
            session_key=session_key,
            sender_id="user-1",
            content="先这样吧",
            need_reply=False,
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        )
    )

    assert response is not None
    assert bus.outbound_size == 2
    assert len(provider.calls) == 1

    feedback_event = await bus.consume_outbound()
    outcome_event = await bus.consume_outbound()
    assert feedback_event.metadata["feedback_submitted"]["feedback_type"] == "thumb_up"
    assert feedback_event.metadata["feedback_submitted"]["feedback_reason"] == "natural_language_llm"
    assert outcome_event.metadata["response_outcome_evaluated"]["outcome_label"] == "positive_feedback"


@pytest.mark.asyncio
async def test_agent_loop_skips_llm_feedback_when_confidence_too_low(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    provider = _ClassifierProvider(
        '{"is_feedback": true, "sentiment": "negative", "confidence": 0.41}'
    )
    bus = MessageBus()
    config = Config(storage_workspace=str(temp_dir))
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=temp_dir / "workspace",
        config=config,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-1")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message(
        "assistant",
        "给你一个修复步骤",
        sender_id="user-1",
        response_id="resp-123",
        timestamp="2026-04-30T00:00:00",
    )
    await loop.sessions.save(session)

    response = await loop._process_message(
        InboundMessage(
            session_key=session_key,
            sender_id="user-1",
            content="差点意思",
            need_reply=False,
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        )
    )

    assert response is not None
    assert bus.outbound_size == 1
    assert len(provider.calls) == 1

    outcome_event = await bus.consume_outbound()
    assert outcome_event.metadata["response_outcome_evaluated"]["outcome_label"] == "reasked"

    persisted_session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    assert persisted_session.metadata.get("feedback_events") in (None, [])


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


@pytest.mark.asyncio
async def test_agent_loop_emits_normalized_response_completed_payload(temp_dir: Path, monkeypatch):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    async def fake_run_agent_loop(self, **kwargs):
        return (
            "final answer",
            None,
            [{"tool_name": "search_docs"}, {"tool_name": "fetch_page"}],
            {"prompt_tokens": 12, "completion_tokens": 8},
            3,
        )

    monkeypatch.setattr(AgentLoop, "_run_agent_loop", fake_run_agent_loop)

    bus = MessageBus()
    config = Config(storage_workspace=str(temp_dir))
    loop = AgentLoop(
        bus=bus,
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=config,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-1")
    response = await loop._process_message(
        InboundMessage(
            session_key=session_key,
            sender_id="user-1",
            content="please help",
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        )
    )

    assert response is not None
    assert response.content == "final answer"
    assert response.response_id is not None
    assert bus.outbound_size == 1

    completed_event = await bus.consume_outbound()
    assert completed_event.event_type == OutboundEventType.RESPONSE_COMPLETED
    payload = completed_event.metadata["response_completed"]
    assert payload["response_id"] == response.response_id
    assert payload["session_id"] == "cli__default__session-1"
    assert payload["channel"] == "cli__default"
    assert payload["session_type"] == "cli"
    assert payload["user_id"] == "user-1"
    assert payload["prompt_tokens"] == 12
    assert payload["completion_tokens"] == 8
    assert payload["total_tokens"] == 20
    assert payload["iteration_count"] == 3
    assert payload["tool_count"] == 2
    assert payload["tools_used_names"] == ["search_docs", "fetch_page"]
    assert payload["response_length"] == len("final answer")
    assert payload["has_reasoning"] is False
    assert payload["time_cost_ms"] >= 0
    assert payload["created_at"]
    assert fake_langfuse.calls == [(response.response_id, payload)]

    session_path = temp_dir / "bot" / "sessions" / "cli__default__session-1.jsonl"
    metadata = json.loads(session_path.read_text().splitlines()[0])
    assert metadata["metadata"]["response_facts"][response.response_id] == payload
