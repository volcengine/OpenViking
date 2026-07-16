import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vikingbot.agent import loop as loop_module
from vikingbot.agent.context import ContextBuilder
from vikingbot.agent.loop import AgentLoop
from vikingbot.bus.events import InboundMessage, OutboundEventType
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import AgentsConfig, Config, SessionKey
from vikingbot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class _FakeProvider(LLMProvider):
    async def chat(self, *args, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("provider.chat should not be called in no-reply outcome test")

    def get_default_model(self) -> str:
        return "fake-model"


class _FakeSubagentManager:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _RecordingProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return LLMResponse(content="ok")

    def get_default_model(self) -> str:
        return "fake-model"


class _FakeLangfuseClient:
    def __init__(self):
        self.calls = []

    def update_generation_metadata(self, response_id, metadata):
        self.calls.append((response_id, metadata))
        return metadata

    def update_response_outcome(self, response_id, outcome_label, outcome_payload):
        self.calls.append((response_id, outcome_label, outcome_payload))
        return outcome_payload


class _FakeOVClient:
    def __init__(self, *, context_payload=None, pending_tokens=None):
        self.context_payload = context_payload or {}
        self.pending_tokens = list(pending_tokens or [])
        self.context_calls = []
        self.append_calls = []
        self.commit_calls = []
        self.session_calls = []

    async def get_session_context(self, session_id, token_budget):
        self.context_calls.append((session_id, token_budget))
        return self.context_payload

    async def append_messages(
        self,
        session_id,
        messages,
        default_user_peer_id=None,
        session_user_id=None,
    ):
        self.append_calls.append(
            (session_id, list(messages), default_user_peer_id, session_user_id)
        )
        return {"session_id": session_id, "added": len(messages), "message_count": len(messages)}

    async def get_session(self, session_id, user_id=None):
        self.session_calls.append((session_id, user_id))
        next_pending_tokens = self.pending_tokens.pop(0) if self.pending_tokens else 0
        return {"session_id": session_id, "pending_tokens": next_pending_tokens}

    async def commit_session(self, session_id, keep_recent_count=0, user_id=None):
        self.commit_calls.append((session_id, keep_recent_count, user_id))
        return {"session_id": session_id, "status": "accepted"}


def test_agents_config_temperature_schema_caps_at_two():
    schema = AgentsConfig.model_json_schema()
    temperature = schema["properties"]["temperature"]

    assert temperature["default"] == 0.7
    assert temperature["minimum"] == 0.0
    assert temperature["maximum"] == 2.0


def test_agents_config_enables_subagents_by_default():
    assert AgentsConfig().subagent_enabled is True


def test_agents_config_keeps_ten_recent_openviking_messages_by_default():
    assert AgentsConfig().commit_keep_recent_count == 10


def test_agent_loop_omits_spawn_tool_when_subagents_disabled(temp_dir: Path, monkeypatch):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    bus = MessageBus()
    provider = _RecordingProvider()
    config = Config(storage_workspace=str(temp_dir), agents={"subagent_enabled": False})

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=temp_dir / "workspace",
        model=config.agents.model,
        temperature=config.agents.temperature,
        config=config,
    )

    assert "spawn" not in loop.tools.tool_names


def test_agent_loop_standalone_omits_openviking_tools(temp_dir: Path, monkeypatch):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    config = Config(storage_workspace=str(temp_dir))
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_RecordingProvider(),
        workspace=temp_dir / "workspace",
        config=config,
    )

    assert config.ov_server.server_url == ""
    assert not any(name.startswith("openviking_") for name in loop.tools.tool_names)
    session_key = SessionKey(type="cli", channel_id="default", chat_id="standalone")
    assert loop._get_ov_tools_enable(session_key) is False


@pytest.mark.asyncio
async def test_context_prompt_omits_subagent_capability_when_disabled(temp_dir: Path):
    context = ContextBuilder(workspace=temp_dir / "workspace", enable_subagents=False)
    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-1")

    prompt = await context._get_identity(session_key)

    assert "Spawn subagents" not in prompt


@pytest.mark.asyncio
async def test_agent_loop_passes_configured_temperature_to_provider(temp_dir: Path, monkeypatch):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    bus = MessageBus()
    provider = _RecordingProvider()
    config = Config(storage_workspace=str(temp_dir), agents={"temperature": 0.2})
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=temp_dir / "workspace",
        model=config.agents.model,
        temperature=config.agents.temperature,
        config=config,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-1")
    response, _, _ = await loop._chat_with_stream_events(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        session_key=session_key,
        publish_events=False,
    )

    assert response.content == "ok"
    assert provider.calls[0][1]["temperature"] == 0.2
    assert loop.subagents.kwargs["temperature"] == 0.2


@pytest.mark.asyncio
async def test_agent_loop_makes_final_no_tool_call_when_iteration_limit_reached(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    class _ToolLimitProvider(LLMProvider):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def chat(self, messages, tools=None, **kwargs):
            self.calls.append(
                {
                    "messages": [dict(message) for message in messages],
                    "tools": list(tools or []),
                    "kwargs": kwargs,
                }
            )
            if len(self.calls) == 1:
                return LLMResponse(
                    content="Let me check these sources.",
                    tool_calls=[
                        ToolCallRequest(
                            id="call-1",
                            name="lookup_fact",
                            arguments={"query": "current facts 1"},
                            tokens=3,
                        ),
                        ToolCallRequest(
                            id="call-2",
                            name="lookup_fact",
                            arguments={"query": "current facts 2"},
                            tokens=3,
                        ),
                        ToolCallRequest(
                            id="call-3",
                            name="lookup_fact",
                            arguments={"query": "current facts 3"},
                            tokens=3,
                        ),
                    ],
                    usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                )
            return LLMResponse(
                content="final answer from gathered tool results",
                usage={"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
            )

        def get_default_model(self) -> str:
            return "fake-model"

    class _ToolRegistry:
        def __init__(self):
            self.execute_calls = []

        def get_definitions(self, **kwargs):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup_fact",
                        "description": "Lookup fact",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

        async def execute(self, tool_name, arguments, **kwargs):
            self.execute_calls.append((tool_name, arguments, kwargs))
            return "tool result: useful context"

    provider = _ToolLimitProvider()
    tools = _ToolRegistry()
    bus = MessageBus()
    config = Config(storage_workspace=str(temp_dir))
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=temp_dir / "workspace",
        config=config,
        max_iterations=1,
    )
    loop.tools = tools

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-limit")
    captured_turns = []
    final_content, _reasoning, tools_used, token_usage, iteration = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "please answer with lookup"}],
        session_key=session_key,
        publish_events=False,
        captured_turns=captured_turns,
    )

    assert final_content == "final answer from gathered tool results"
    assert iteration == 1
    assert len(provider.calls) == 2
    assert provider.calls[0]["tools"]
    assert provider.calls[1]["tools"] == []
    assert provider.calls[1]["messages"][-1]["content"].startswith(
        "Tool-use iteration limit reached."
    )
    assert any(
        message.get("content") == "tool result: useful context"
        for message in provider.calls[1]["messages"]
    )
    assert len(tools.execute_calls) == 3
    assert tools.execute_calls[0][:2] == ("lookup_fact", {"query": "current facts 1"})
    assert [tool["tool_name"] for tool in tools_used] == [
        "lookup_fact",
        "lookup_fact",
        "lookup_fact",
    ]
    assert len(captured_turns) == 1
    assert captured_turns[0]["content"] == "Let me check these sources."
    assert [tool["tool_call_id"] for tool in captured_turns[0]["tool_calls"]] == [
        "call-1",
        "call-2",
        "call-3",
    ]
    assert [tool["result"] for tool in captured_turns[0]["tool_calls"]] == [
        "tool result: useful context",
        "tool result: useful context",
        "tool result: useful context",
    ]
    assert token_usage == {"prompt_tokens": 17, "completion_tokens": 7, "total_tokens": 24}


@pytest.mark.asyncio
async def test_agent_loop_evaluates_previous_response_outcome_before_openviking_precommit_clear(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    async def fake_run_agent_loop(self, **kwargs):
        return "final answer", None, [], {"prompt_tokens": 1, "completion_tokens": 1}, 1

    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(AgentLoop, "_run_agent_loop", fake_run_agent_loop)
    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    bus = MessageBus()
    config = Config(
        storage_workspace=str(temp_dir),
        agents={
            "session_context_enabled": True,
            "commit_token_threshold": 1,
            "commit_keep_recent_count": 0,
        },
    )
    loop = AgentLoop(
        bus=bus,
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=config,
    )

    async def fake_precommit(session, msg):
        session.clear()
        await loop.sessions.save(session)

    monkeypatch.setattr(loop, "_maybe_commit_openviking_before_turn", fake_precommit)

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-precommit-clear")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message(
        "assistant",
        "hello",
        sender_id="user-1",
        response_id="resp-123",
        timestamp="2026-04-30T00:00:00",
    )
    await loop.sessions.save(session)

    await loop._process_message(
        InboundMessage(
            session_key=session_key,
            sender_id="user-1",
            content="that did not help",
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        )
    )

    outcome_event = await bus.consume_outbound()
    assert outcome_event.event_type == OutboundEventType.RESPONSE_OUTCOME_EVALUATED
    assert outcome_event.response_id == "resp-123"
    persisted_session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    assert persisted_session.metadata["response_outcomes"]["resp-123"]["outcome_label"] == "reasked"


@pytest.mark.asyncio
async def test_agent_loop_build_prompt_history_uses_ov_context_plus_unsynced_tail(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    fake_ov_client = _FakeOVClient(
        context_payload={
            "latest_archive_overview": "Earlier summary",
            "messages": [
                {"role": "user", "content": "OV user turn"},
                {"role": "assistant", "parts": [{"type": "text", "text": "OV assistant turn"}]},
            ],
        }
    )

    async def fake_get_ov_client(self, session_key, openviking_connection=None, actor_peer_id=None):
        del session_key, openviking_connection, actor_peer_id
        return fake_ov_client

    monkeypatch.setattr(AgentLoop, "_get_ov_client", fake_get_ov_client)

    bus = MessageBus()
    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://127.0.0.1:1933"},
        agents={"session_context_enabled": True, "session_context_token_budget": 321},
    )
    loop = AgentLoop(
        bus=bus,
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=config,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-ov-history")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message("user", "local synced user")
    session.add_message("assistant", "local synced assistant")
    session.add_message("user", "local unsynced user")
    session.add_message("assistant", "local unsynced assistant")
    session.metadata["openviking"] = {
        "session_id": "ov-session-1",
        "last_synced_local_index": 1,
    }

    history = await loop._build_prompt_history(session)

    assert fake_ov_client.context_calls == [("ov-session-1", 321)]
    assert [message["content"] for message in history] == [
        "[Earlier conversation summary]\nEarlier summary",
        "OV user turn",
        "OV assistant turn",
        "local unsynced user",
        "local unsynced assistant",
    ]


@pytest.mark.asyncio
async def test_agent_loop_build_prompt_history_skips_tail_when_sync_cursor_is_past_local_messages(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    fake_ov_client = _FakeOVClient(
        context_payload={"messages": [{"role": "user", "content": "OV user turn"}]}
    )

    async def fake_get_ov_client(self, session_key, openviking_connection=None, actor_peer_id=None):
        del self, session_key, openviking_connection, actor_peer_id
        return fake_ov_client

    monkeypatch.setattr(AgentLoop, "_get_ov_client", fake_get_ov_client)

    bus = MessageBus()
    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://127.0.0.1:1933"},
        agents={"session_context_enabled": True, "session_context_token_budget": 321},
    )
    loop = AgentLoop(
        bus=bus,
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=config,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-ov-cursor-past")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message("user", "local user")
    session.add_message("assistant", "local assistant")
    session.metadata["openviking"] = {
        "session_id": "ov-session-1",
        "last_synced_local_index": 20,
    }

    history = await loop._build_prompt_history(session)

    assert [message["content"] for message in history] == ["OV user turn"]


@pytest.mark.asyncio
async def test_agent_loop_build_prompt_history_enforces_token_budget_for_live_tool_outputs(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    tool_output = "x" * 10_000
    fake_ov_client = _FakeOVClient(
        context_payload={
            "messages": [
                {"role": "user", "parts": [{"type": "text", "text": "original query"}]},
                *[
                    {
                        "role": "assistant",
                        "parts": [
                            {"type": "text", "text": f"turn {index}"},
                            {"type": "tool", "tool_output": tool_output},
                        ],
                    }
                    for index in range(10)
                ],
                {"role": "assistant", "parts": [{"type": "text", "text": "final answer"}]},
            ]
        }
    )

    async def fake_get_ov_client(self, session_key, openviking_connection=None, actor_peer_id=None):
        del self, session_key, openviking_connection, actor_peer_id
        return fake_ov_client

    monkeypatch.setattr(AgentLoop, "_get_ov_client", fake_get_ov_client)

    loop = AgentLoop(
        bus=MessageBus(),
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=Config(
            storage_workspace=str(temp_dir),
            ov_server={"server_url": "http://127.0.0.1:1933"},
            agents={"session_context_enabled": True, "session_context_token_budget": 3000},
        ),
    )
    session = loop.sessions.get_or_create(
        SessionKey(type="cli", channel_id="default", chat_id="session-large-tools"),
        skip_heartbeat=True,
    )
    session.metadata["openviking"] = {
        "session_id": "ov-session-large-tools",
        "last_synced_local_index": -1,
    }

    history = await loop._build_prompt_history(session)

    assert fake_ov_client.context_calls == [("ov-session-large-tools", 3000)]
    assert sum(loop._history_message_tokens(message) for message in history) <= 3000
    assert history[-1]["content"] == "final answer"
    assert sum(str(message.get("content", "")).count("x") for message in history) < 100_000
    assert any("History truncated" in str(message.get("content", "")) for message in history)


@pytest.mark.asyncio
async def test_agent_loop_submits_openviking_session_through_compact_hook(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    calls = []

    async def fake_execute_hooks(context, **kwargs):
        calls.append((context, kwargs))
        session = kwargs["session"]
        session.metadata.setdefault("openviking", {})["last_sync_status"] = "success"
        return kwargs

    monkeypatch.setattr(loop_module.hook_manager, "execute_hooks", fake_execute_hooks)

    bus = MessageBus()
    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://127.0.0.1:1933"},
        agents={"session_context_enabled": True},
    )
    loop = AgentLoop(
        bus=bus,
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=config,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-ov-sync")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message("user", "Need syncing", sender_id="user-1")
    session.add_message("assistant", "Synced reply", sender_id="user-1")

    success = await loop._submit_openviking_session(session)

    assert success is True
    assert len(calls) == 1
    context, kwargs = calls[0]
    assert context.event_type == "message.compact"
    assert context.session_id == session_key.safe_name()
    assert kwargs == {"session": session, "force_commit": False}


@pytest.mark.asyncio
async def test_agent_loop_commits_openviking_before_model_when_pending_tokens_reach_threshold(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    events = []

    async def fake_execute_hooks(context, **kwargs):
        events.append(
            (
                "hook",
                kwargs["force_commit"],
                kwargs.get("keep_recent_count"),
                kwargs.get("commit_message_threshold"),
            )
        )
        session = kwargs["session"]
        state = session.metadata.setdefault("openviking", {})
        state["last_sync_status"] = "success"
        state["last_pending_tokens"] = 0
        state["last_commit_performed"] = bool(kwargs["force_commit"])
        if not kwargs["force_commit"]:
            state["last_synced_local_index"] = len(session.messages) - 1
        return kwargs

    async def fake_get_ov_client(self, session_key, openviking_connection=None, actor_peer_id=None):
        del self, session_key, openviking_connection, actor_peer_id

        class _Client:
            async def get_session_context(self, session_id, token_budget):
                events.append(("context", session_id, token_budget))
                return {"messages": []}

            async def close(self):
                return None

        return _Client()

    async def fake_run_agent_loop(self, **kwargs):
        events.append(("model", [message.get("content") for message in kwargs["messages"]]))
        return "final answer", None, [], {"prompt_tokens": 1, "completion_tokens": 1}, 1

    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(loop_module.hook_manager, "execute_hooks", fake_execute_hooks)
    monkeypatch.setattr(AgentLoop, "_get_ov_client", fake_get_ov_client)
    monkeypatch.setattr(AgentLoop, "_run_agent_loop", fake_run_agent_loop)
    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    bus = MessageBus()
    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://127.0.0.1:1933"},
        agents={
            "session_context_enabled": True,
            "session_context_token_budget": 321,
            "commit_token_threshold": 100,
            "commit_keep_recent_count": 2,
        },
    )
    loop = AgentLoop(
        bus=bus,
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=config,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-precommit")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message("user", "old user", sender_id="user-1")
    session.add_message("assistant", "old assistant", sender_id="user-1")
    session.metadata["openviking"] = {
        "session_id": session_key.safe_name(),
        "last_synced_local_index": 1,
        "last_pending_tokens": 100,
    }
    await loop.sessions.save(session)

    response = await loop._process_message(
        InboundMessage(
            session_key=session_key,
            sender_id="user-1",
            content="new question",
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        )
    )

    assert response is not None
    assert response.content == "final answer"
    assert events[0] == ("hook", True, 2, loop.memory_window)
    assert events[1] == ("context", "cli__default__session-precommit", 321)
    assert events[2][0] == "model"
    assert events[-1] == ("hook", False, None, loop.memory_window)
    persisted_session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    assert [message["content"] for message in persisted_session.messages] == [
        "new question",
        "final answer",
    ]
    assert persisted_session.metadata["openviking"]["last_synced_local_index"] == 1


@pytest.mark.asyncio
async def test_agent_loop_commits_openviking_before_model_when_memory_window_reached(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    events = []

    async def fake_execute_hooks(context, **kwargs):
        events.append(
            (
                "hook",
                kwargs["force_commit"],
                kwargs.get("keep_recent_count"),
                kwargs.get("commit_message_threshold"),
            )
        )
        session = kwargs["session"]
        session.metadata.setdefault("openviking", {})["last_sync_status"] = "success"
        session.metadata["openviking"]["last_pending_tokens"] = 0
        session.metadata["openviking"]["last_commit_local_index"] = len(session.messages) - 1
        session.metadata["openviking"]["last_commit_performed"] = bool(kwargs["force_commit"])
        return kwargs

    async def fake_get_ov_client(self, session_key, openviking_connection=None, actor_peer_id=None):
        del self, session_key, openviking_connection, actor_peer_id

        class _Client:
            async def get_session_context(self, session_id, token_budget):
                events.append(("context", session_id, token_budget))
                return {"messages": []}

            async def close(self):
                return None

        return _Client()

    async def fake_run_agent_loop(self, **kwargs):
        events.append(("model", [message.get("content") for message in kwargs["messages"]]))
        return "final answer", None, [], {"prompt_tokens": 1, "completion_tokens": 1}, 1

    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(loop_module.hook_manager, "execute_hooks", fake_execute_hooks)
    monkeypatch.setattr(AgentLoop, "_get_ov_client", fake_get_ov_client)
    monkeypatch.setattr(AgentLoop, "_run_agent_loop", fake_run_agent_loop)
    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    bus = MessageBus()
    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://127.0.0.1:1933"},
        agents={
            "session_context_enabled": True,
            "session_context_token_budget": 321,
            "commit_token_threshold": 1000,
            "commit_keep_recent_count": 2,
        },
    )
    loop = AgentLoop(
        bus=bus,
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=config,
        memory_window=3,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-window-precommit")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message("user", "old user", sender_id="user-1")
    session.add_message("assistant", "old assistant", sender_id="user-1")
    session.metadata["openviking"] = {
        "session_id": session_key.safe_name(),
        "last_synced_local_index": 1,
        "last_pending_tokens": 0,
        "last_commit_local_index": -1,
    }
    await loop.sessions.save(session)

    response = await loop._process_message(
        InboundMessage(
            session_key=session_key,
            sender_id="user-1",
            content="new question",
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        )
    )

    assert response is not None
    assert response.content == "final answer"
    assert events[0] == ("hook", True, 2, 3)
    assert events[-1] == ("hook", False, None, 3)
    persisted_session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    assert [message["content"] for message in persisted_session.messages] == [
        "new question",
        "final answer",
    ]


@pytest.mark.asyncio
async def test_agent_loop_does_not_precommit_again_after_memory_window_commit(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    calls = []

    async def fake_execute_hooks(context, **kwargs):
        calls.append(kwargs)
        session = kwargs["session"]
        session.metadata.setdefault("openviking", {})["last_sync_status"] = "success"
        return kwargs

    monkeypatch.setattr(loop_module.hook_manager, "execute_hooks", fake_execute_hooks)

    bus = MessageBus()
    config = Config(
        storage_workspace=str(temp_dir),
        agents={
            "session_context_enabled": True,
            "commit_token_threshold": 1000,
            "commit_keep_recent_count": 2,
        },
    )
    loop = AgentLoop(
        bus=bus,
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=config,
        memory_window=3,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-window-no-repeat")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message("user", "old user", sender_id="user-1")
    session.add_message("assistant", "old assistant", sender_id="user-1")
    session.metadata["openviking"] = {
        "session_id": session_key.safe_name(),
        "last_pending_tokens": 0,
        "last_commit_local_index": 1,
    }

    await loop._maybe_commit_openviking_before_turn(
        session,
        InboundMessage(
            session_key=session_key,
            sender_id="user-1",
            content="new question",
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        ),
    )

    assert calls == []


@pytest.mark.asyncio
async def test_agent_loop_post_turn_passes_memory_window_threshold(temp_dir: Path, monkeypatch):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    calls = []

    async def fake_execute_hooks(context, **kwargs):
        calls.append(kwargs)
        session = kwargs["session"]
        session.metadata.setdefault("openviking", {})["last_sync_status"] = "success"
        return kwargs

    async def fake_run_agent_loop(self, **kwargs):
        return "final answer", None, [], {"prompt_tokens": 1, "completion_tokens": 1}, 1

    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(loop_module.hook_manager, "execute_hooks", fake_execute_hooks)
    monkeypatch.setattr(AgentLoop, "_run_agent_loop", fake_run_agent_loop)
    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    bus = MessageBus()
    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://127.0.0.1:1933"},
        agents={"session_context_enabled": True},
    )
    loop = AgentLoop(
        bus=bus,
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=config,
        memory_window=3,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-post-window")
    await loop._process_message(
        InboundMessage(
            session_key=session_key,
            sender_id="user-1",
            content="new question",
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        )
    )

    assert calls[-1]["force_commit"] is False
    assert calls[-1]["commit_message_threshold"] == 3


@pytest.mark.asyncio
async def test_agent_loop_post_turn_clears_local_session_after_openviking_commit(
    temp_dir: Path, monkeypatch
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    calls = []

    async def fake_execute_hooks(context, **kwargs):
        calls.append(kwargs)
        session = kwargs["session"]
        state = session.metadata.setdefault("openviking", {})
        state["last_sync_status"] = "success"
        state["last_commit_performed"] = True
        state["last_synced_local_index"] = len(session.messages) - 1
        state["last_commit_local_index"] = len(session.messages) - 1
        return kwargs

    async def fake_run_agent_loop(self, **kwargs):
        return "final answer", None, [], {"prompt_tokens": 1, "completion_tokens": 1}, 1

    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(loop_module.hook_manager, "execute_hooks", fake_execute_hooks)
    monkeypatch.setattr(AgentLoop, "_run_agent_loop", fake_run_agent_loop)
    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    bus = MessageBus()
    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://127.0.0.1:1933"},
        agents={"session_context_enabled": True},
    )
    loop = AgentLoop(
        bus=bus,
        provider=_FakeProvider(),
        workspace=temp_dir / "workspace",
        config=config,
        memory_window=3,
    )

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-post-clear")
    await loop._process_message(
        InboundMessage(
            session_key=session_key,
            sender_id="user-1",
            content="new question",
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        )
    )

    persisted_session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    assert calls[-1]["commit_message_threshold"] == 3
    assert persisted_session.messages == []
    assert persisted_session.metadata["openviking"]["session_id"] == session_key.safe_name()
    assert persisted_session.metadata["openviking"]["last_synced_local_index"] == -1
    assert persisted_session.metadata["openviking"]["last_commit_local_index"] == -1
