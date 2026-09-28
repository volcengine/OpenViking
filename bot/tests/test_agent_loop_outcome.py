import copy
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from vikingbot.agent import loop as loop_module
from vikingbot.agent.context import ContextBuilder
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.tools.base import MultimodalToolResult
from vikingbot.agent.tools.registry import ToolExecutionResult
from vikingbot.bus.events import InboundMessage, OutboundEventType
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import Config, SessionKey
from vikingbot.openviking_mount.session_state import make_openviking_storage_session_id
from vikingbot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from vikingbot.session.manager import SessionManager


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
    def __init__(self, *, context_payload):
        self.context_payload = context_payload
        self.context_calls = []

    async def get_session_context(self, session_id, token_budget):
        self.context_calls.append((session_id, token_budget))
        return self.context_payload


@pytest.fixture
def make_loop(temp_dir, monkeypatch):
    """Construct a real loop with external integrations stubbed for these tests."""
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    def create(*, config=None, provider=None, bus=None, register_tools=False, **kwargs):
        with monkeypatch.context() as setup:
            if not register_tools:
                setup.setattr(AgentLoop, "_register_default_tools", lambda self: None)
            return AgentLoop(
                bus=bus if bus is not None else MessageBus(),
                provider=provider if provider is not None else _FakeProvider(),
                workspace=temp_dir / "workspace",
                config=config if config is not None else Config(storage_workspace=str(temp_dir)),
                **kwargs,
            )

    return create


def _image_call(call_id, label=None):
    return ToolCallRequest(
        id=call_id, name="read_image", arguments={"label": label} if label else {}, tokens=1
    )


class _MediaProvider(_FakeProvider):
    def __init__(self, rounds, *, supports_media=True):
        super().__init__()
        self.calls = []
        self.responses = iter(
            [LLMResponse(content=None, tool_calls=calls) for calls in rounds]
            + [LLMResponse(content="done")]
        )
        self.supports_media = supports_media

    async def chat(self, messages, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        return next(self.responses)

    def supports_tool_result_media(self, model=None):
        return self.supports_media


class _ImageRegistry:
    def __init__(self, content=None):
        self.content = content

    def get_definitions(self, **kwargs):
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_image",
                    "description": "Read an image",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    async def execute_detailed(self, name, params, **kwargs):
        label = params.get("label", "Image resource.")
        content = (
            self.content
            if self.content is not None
            else [
                {"type": "text", "text": label},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,ZGF0YQ=="}},
            ]
        )
        return ToolExecutionResult(
            result=MultimodalToolResult(text=label, content=content), effective_params=params
        )


def test_context_keeps_multimodal_tool_result_on_tool_message(temp_dir: Path):
    context = ContextBuilder(workspace=temp_dir / "workspace")
    content = [
        {"type": "text", "text": "Source: viking://resources/image.png"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,aW1hZ2U="},
        },
    ]
    messages = context.add_tool_result(
        [],
        "call-1",
        "openviking_multi_read",
        MultimodalToolResult(text="Image resource.", content=content),
    )

    assert messages == [
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "openviking_multi_read",
            "content": content,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("supports_media", [False, True])
async def test_agent_loop_gates_multimodal_result_by_provider(make_loop, supports_media: bool):
    content = [
        {"type": "text", "text": "Source: viking://resources/image.png"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,aW1hZ2U="},
        },
    ]

    provider = _MediaProvider([[_image_call("call-1")]], supports_media=supports_media)
    loop = make_loop(provider=provider, max_iterations=2)

    final, _reasoning, tools_used, _usage, _iteration = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "read it"}],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="multimodal"),
        publish_events=False,
        tool_registry=_ImageRegistry(content),
    )

    assert final == "done"
    tool_message = next(message for message in provider.calls[1] if message["role"] == "tool")
    if supports_media:
        assert tool_message["content"] == content
    else:
        assert tool_message["content"].startswith("Image resource.")
        assert "does not support media in tool results" in tool_message["content"]
    assert tools_used[0]["result"] == "Image resource."


@pytest.mark.asyncio
async def test_agent_loop_limits_media_across_parallel_tool_results(make_loop, monkeypatch):
    monkeypatch.setattr(loop_module, "MAX_INLINE_TOOL_RESULT_MEDIA_BYTES", 5)

    provider = _MediaProvider([[_image_call("call-1", "first"), _image_call("call-2", "second")]])
    loop = make_loop(provider=provider, max_iterations=2)

    final, *_ = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "read both"}],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="media-budget"),
        publish_events=False,
        tool_registry=_ImageRegistry(),
    )

    assert final == "done"
    tool_messages = [message for message in provider.calls[1] if message["role"] == "tool"]
    assert isinstance(tool_messages[0]["content"], list)
    assert isinstance(tool_messages[1]["content"], str)
    assert "make this model request exceed" in tool_messages[1]["content"]


@pytest.mark.asyncio
async def test_agent_loop_prefers_new_media_across_consecutive_tool_rounds(make_loop, monkeypatch):
    monkeypatch.setattr(loop_module, "MAX_INLINE_TOOL_RESULT_MEDIA_BYTES", 5)

    provider = _MediaProvider(
        [[_image_call("call-1", "image-1")], [_image_call("call-2", "image-2")]]
    )
    loop = make_loop(provider=provider, max_iterations=3)

    final, *_ = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "read two images in sequence"}],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="media-rounds"),
        publish_events=False,
        tool_registry=_ImageRegistry(),
    )

    assert final == "done"
    first_round_tool = next(message for message in provider.calls[1] if message["role"] == "tool")
    assert isinstance(first_round_tool["content"], list)

    tool_messages = [message for message in provider.calls[-1] if message["role"] == "tool"]
    assert isinstance(tool_messages[0]["content"], str)
    assert "earlier tool result was omitted" in tool_messages[0]["content"]
    assert isinstance(tool_messages[1]["content"], list)


def test_agent_loop_omits_spawn_tool_when_subagents_disabled(make_loop, temp_dir: Path):
    bus = MessageBus()
    provider = _RecordingProvider()
    config = Config(storage_workspace=str(temp_dir), agents={"subagent_enabled": False})

    loop = make_loop(
        bus=bus,
        provider=provider,
        model=config.agents.model,
        temperature=config.agents.temperature,
        config=config,
        register_tools=True,
    )

    assert "spawn" not in loop.tools.tool_names


def test_agent_loop_standalone_omits_openviking_tools(make_loop, temp_dir: Path):
    config = Config(storage_workspace=str(temp_dir))
    loop = make_loop(provider=_RecordingProvider(), config=config, register_tools=True)

    assert config.ov_server.server_url == ""
    assert not any(name.startswith("openviking_") for name in loop.tools.tool_names)
    session_key = SessionKey(type="cli", channel_id="default", chat_id="standalone")
    assert loop._get_ov_tools_enable(session_key) is False


@pytest.mark.asyncio
async def test_agent_loop_passes_configured_temperature_to_provider(make_loop, temp_dir: Path):
    bus = MessageBus()
    provider = _RecordingProvider()
    config = Config(storage_workspace=str(temp_dir), agents={"temperature": 0.2})
    loop = make_loop(
        bus=bus,
        provider=provider,
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
    make_loop, temp_dir: Path
):
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
    loop = make_loop(bus=bus, provider=provider, config=config, max_iterations=1)
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
    assert {
        key: token_usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    } == {"prompt_tokens": 17, "completion_tokens": 7, "total_tokens": 24}


@pytest.mark.asyncio
async def test_agent_loop_evaluates_previous_response_outcome_before_openviking_precommit_clear(
    make_loop, temp_dir: Path, monkeypatch
):
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
    loop = make_loop(bus=bus, config=config)

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
    persisted_session = SessionManager(config.bot_data_path).get_or_create(
        session_key, skip_heartbeat=True
    )
    assert persisted_session.metadata["response_outcomes"]["resp-123"]["outcome_label"] == "reasked"


@pytest.mark.asyncio
async def test_agent_loop_build_prompt_history_uses_ov_context_plus_unsynced_tail(
    make_loop, temp_dir: Path, monkeypatch
):
    fake_ov_client = _FakeOVClient(
        context_payload={
            "latest_archive_overview": "Earlier summary",
            "messages": [
                {"role": "user", "content": "OV user turn"},
                {"role": "assistant", "parts": [{"type": "text", "text": "OV assistant turn"}]},
            ],
        }
    )

    monkeypatch.setattr(AgentLoop, "_get_ov_client", AsyncMock(return_value=fake_ov_client))

    bus = MessageBus()
    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://127.0.0.1:1933"},
        agents={"session_context_enabled": True, "session_context_token_budget": 321},
    )
    loop = make_loop(bus=bus, config=config)

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
async def test_agent_loop_build_prompt_history_falls_back_to_loaded_local_history_when_ov_empty(
    make_loop, temp_dir: Path, monkeypatch
):
    fake_ov_client = _FakeOVClient(context_payload={"messages": []})

    monkeypatch.setattr(AgentLoop, "_get_ov_client", AsyncMock(return_value=fake_ov_client))

    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://127.0.0.1:1933"},
        agents={"session_context_enabled": True, "session_context_token_budget": 321},
    )
    loop = make_loop(config=config)

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-local-fallback")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message("user", "persisted user")
    session.add_message("assistant", "persisted assistant")
    session.add_message("user", "current question")
    session.metadata["openviking"] = {
        "session_id": "ov-session-missing-context",
        "last_synced_local_index": 1,
    }
    await loop.sessions.save(session)

    restarted_sessions = SessionManager(config.bot_data_path)
    loaded_session = restarted_sessions.get_or_create(session_key, skip_heartbeat=True)
    history = await loop._build_prompt_history(loaded_session)

    assert fake_ov_client.context_calls == [("ov-session-missing-context", 321)]
    assert [message["content"] for message in history] == [
        "persisted user",
        "persisted assistant",
        "current question",
    ]
    assert loaded_session.metadata["openviking"]["last_synced_local_index"] == 1


@pytest.mark.asyncio
async def test_agent_loop_build_prompt_history_skips_tail_when_sync_cursor_is_past_local_messages(
    make_loop, temp_dir: Path, monkeypatch
):
    fake_ov_client = _FakeOVClient(
        context_payload={"messages": [{"role": "user", "content": "OV user turn"}]}
    )

    monkeypatch.setattr(AgentLoop, "_get_ov_client", AsyncMock(return_value=fake_ov_client))

    bus = MessageBus()
    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://127.0.0.1:1933"},
        agents={"session_context_enabled": True, "session_context_token_budget": 321},
    )
    loop = make_loop(bus=bus, config=config)

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
    make_loop, temp_dir: Path, monkeypatch
):
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

    monkeypatch.setattr(AgentLoop, "_get_ov_client", AsyncMock(return_value=fake_ov_client))

    loop = make_loop(
        config=Config(
            storage_workspace=str(temp_dir),
            ov_server={"server_url": "http://127.0.0.1:1933"},
            agents={"session_context_enabled": True, "session_context_token_budget": 3000},
        )
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
async def test_agent_loop_build_prompt_history_preserves_anchor_when_final_needs_truncation(
    make_loop, temp_dir: Path, monkeypatch
):
    fake_ov_client = _FakeOVClient(
        context_payload={
            "messages": [
                {"role": "user", "parts": [{"type": "text", "text": "u" * 400}]},
                {"role": "assistant", "parts": [{"type": "text", "text": "a" * 7600}]},
            ]
        }
    )

    monkeypatch.setattr(AgentLoop, "_get_ov_client", AsyncMock(return_value=fake_ov_client))

    loop = make_loop(
        config=Config(
            storage_workspace=str(temp_dir),
            ov_server={"server_url": "http://127.0.0.1:1933"},
            agents={"session_context_enabled": True, "session_context_token_budget": 3000},
        )
    )
    session = loop.sessions.get_or_create(
        SessionKey(type="cli", channel_id="default", chat_id="session-long-final"),
        skip_heartbeat=True,
    )
    session.metadata["openviking"] = {
        "session_id": "ov-session-long-final",
        "last_synced_local_index": -1,
    }

    history = await loop._build_prompt_history(session)

    assert fake_ov_client.context_calls == [("ov-session-long-final", 3000)]
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert history[0]["content"] == "u" * 400
    assert "History truncated" in history[1]["content"]
    assert sum(loop._history_message_tokens(message) for message in history) <= 3000


@pytest.mark.parametrize(
    "token_threshold, pending_tokens, memory_window",
    [pytest.param(100, 100, 50, id="token-budget"), pytest.param(1000, 0, 3, id="message-window")],
)
async def test_agent_loop_commits_before_model_at_context_limit(
    make_loop, temp_dir: Path, monkeypatch, token_threshold, pending_tokens, memory_window
):
    events = []

    async def fake_execute_hooks(context, **kwargs):
        events.append(
            (
                "hook",
                kwargs["force_commit"],
                kwargs.get("keep_recent_turn_count"),
                kwargs.get("commit_message_threshold"),
            )
        )
        session = kwargs["session"]
        state = session.metadata.setdefault("openviking", {})
        state["last_sync_status"] = "success"
        state["last_pending_tokens"] = 0
        state["last_commit_performed"] = bool(kwargs["force_commit"])
        state["last_commit_local_index"] = len(session.messages) - 1
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
            "commit_token_threshold": token_threshold,
            "commit_keep_recent_turn_count": 2,
        },
    )
    loop = make_loop(bus=bus, config=config, memory_window=memory_window)

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-precommit")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message("user", "old user", sender_id="user-1")
    session.add_message("assistant", "old assistant", sender_id="user-1")
    session.metadata["openviking"] = {
        "session_id": session_key.safe_name(),
        "last_synced_local_index": 1,
        "last_pending_tokens": pending_tokens,
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
    assert events[0] == ("hook", True, 2, loop.memory_window)
    assert events[1] == ("context", "cli__default__session-precommit", 321)
    assert events[2][0] == "model"
    assert events[-1] == ("hook", False, None, loop.memory_window)
    persisted_session = SessionManager(config.bot_data_path).get_or_create(
        session_key, skip_heartbeat=True
    )
    assert [message["content"] for message in persisted_session.messages] == [
        "new question",
        "final answer",
    ]
    assert persisted_session.metadata["openviking"]["last_synced_local_index"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "last_commit_index, expected_commits",
    [
        pytest.param(-1, [True], id="uncommitted-history"),
        pytest.param(1, [], id="already-committed"),
    ],
)
async def test_agent_loop_precommit_counts_messages_since_last_commit(
    make_loop, temp_dir: Path, monkeypatch, last_commit_index, expected_commits
):
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
        ov_server={"server_url": "http://127.0.0.1:1933"},
        agents={
            "session_context_enabled": True,
            "commit_token_threshold": 1000,
            "commit_keep_recent_turn_count": 2,
        },
    )
    loop = make_loop(bus=bus, config=config, memory_window=3)

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-window-no-repeat")
    session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
    session.add_message("user", "old user", sender_id="user-1")
    session.add_message("assistant", "old assistant", sender_id="user-1")
    session.metadata["openviking"] = {
        "session_id": session_key.safe_name(),
        "last_pending_tokens": 0,
        "last_commit_local_index": last_commit_index,
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

    assert [call["force_commit"] for call in calls] == expected_commits


@pytest.mark.asyncio
async def test_agent_loop_post_turn_clears_local_session_after_openviking_commit(
    make_loop, temp_dir: Path, monkeypatch
):
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
    loop = make_loop(bus=bus, config=config, memory_window=3)

    session_key = SessionKey(type="cli", channel_id="default", chat_id="session-post-clear")
    await loop._process_message(
        InboundMessage(
            session_key=session_key,
            sender_id="user-1",
            content="new question",
            timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
        )
    )

    persisted_session = SessionManager(config.bot_data_path).get_or_create(
        session_key, skip_heartbeat=True
    )
    assert calls[-1]["force_commit"] is False
    assert calls[-1]["commit_message_threshold"] == 3
    assert persisted_session.messages == []
    assert persisted_session.metadata["openviking"]["session_id"] == (
        make_openviking_storage_session_id(session_key.safe_name())
    )
    assert persisted_session.metadata["openviking"]["last_synced_local_index"] == -1
    assert persisted_session.metadata["openviking"]["last_commit_local_index"] == -1
