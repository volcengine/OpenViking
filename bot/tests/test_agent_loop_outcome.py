import asyncio
import base64
import copy
import json
import random
import struct
import zlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.context import ContextBuilder
from vikingbot.agent.loop import AgentLoop, loop_tools
from vikingbot.agent.loop import loop as loop_module
from vikingbot.agent.tools.base import MultimodalToolResult, Tool
from vikingbot.agent.tools.registry import ToolExecutionResult
from vikingbot.bus.events import InboundMessage, OutboundEventType
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import AgentsConfig, Config, SessionKey
from vikingbot.hooks.builtins.openviking_hooks import OpenVikingCompactHook
from vikingbot.hooks.manager import HookManager
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


def test_agents_config_preserves_independent_deprecated_session_budgets():
    legacy_only = AgentsConfig(
        commit_token_threshold=200000,
        session_context_token_budget=3000,
        commit_retained_message_token_budget=6000,
    )

    assert legacy_only.context_window_tokens == 128000
    assert legacy_only.async_compress_ratio == 0.7
    assert legacy_only.effective_session_context_token_budget == 3000
    assert legacy_only.effective_commit_token_threshold == 200000
    assert legacy_only.retained_token_ratio == 6000 / 128000
    assert legacy_only.commit_token_threshold == 200000
    assert legacy_only.session_context_token_budget == 3000
    assert legacy_only.commit_retained_message_token_budget == 6000
    from vikingbot.openviking_mount.session_budget import session_budget

    assert session_budget(legacy_only) == (128000, 3000, 200000)

    valid_legacy = AgentsConfig(
        commit_token_threshold=64000,
        session_context_token_budget=96000,
    )
    assert valid_legacy.context_window_tokens == 128000
    assert valid_legacy.effective_session_context_token_budget == 96000
    assert valid_legacy.async_compress_ratio == 0.5

    config = AgentsConfig(
        context_window_tokens=1000,
        async_compress_ratio=0.7,
        commit_token_threshold=200000,
        session_context_token_budget=3000,
    )
    assert config.effective_session_context_token_budget == 1000
    assert config.async_compress_ratio == 0.7
    assert config.effective_commit_token_threshold == 700


class _BlockingFirstProvider(_RecordingProvider):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()

    async def chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if len(self.calls) == 1:
            self.started.set()
            await asyncio.Future()
        return LLMResponse(content="ok")


@pytest.fixture
def make_loop(temp_dir, monkeypatch):
    """Construct a real loop with external integrations stubbed for these tests."""
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(loop_module, "SubagentManager", _FakeSubagentManager)

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


@pytest.fixture
def legacy_commit(monkeypatch):
    """Keep the real async HookManager/compact-hook path; stub only the OV client."""
    commit = AsyncMock(return_value={"success": True})
    client = SimpleNamespace(commit=commit, session_owner_user_id=lambda: None)
    monkeypatch.setattr(
        OpenVikingCompactHook, "_get_client", AsyncMock(return_value=(client, False))
    )
    manager = HookManager()
    manager.register_path(["vikingbot.hooks.builtins.openviking_hooks.hooks"])
    monkeypatch.setattr(loop_module.hook_manager, "execute_hooks", manager.execute_hooks)
    return commit


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
    def __init__(
        self,
        content=None,
        *,
        preserve_complete_result=False,
        legacy_outcome=False,
    ):
        self.content = content
        self.preserve_complete_result = preserve_complete_result
        self.legacy_outcome = legacy_outcome

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
        result = MultimodalToolResult(text=label, content=content)
        if self.legacy_outcome:
            return SimpleNamespace(result=result, effective_params=params, skill_uris=())
        return ToolExecutionResult(
            result=result,
            effective_params=params,
            preserve_complete_result=self.preserve_complete_result,
        )


@pytest.fixture
def large_png_url():
    # A reproducible, valid PNG whose encoded payload exceeds the text budget
    # while its decoded size remains well below the existing media byte limit.
    def chunk(kind, body):
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    pixels = random.Random(0).randbytes(300 * 300 * 3)
    rows = b"".join(b"\0" + pixels[offset : offset + 900] for offset in range(0, len(pixels), 900))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 300, 300, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    assert 240_000 < len(png) < loop_module.MAX_INLINE_TOOL_RESULT_MEDIA_BYTES
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["user", "tool"])
@pytest.mark.parametrize("budget", [None, 240_000])
async def test_agent_loop_accepts_large_png_with_text_budget(
    make_loop, large_png_url, source, budget
):
    image = {"type": "image_url", "image_url": {"url": large_png_url}}
    user: dict = {"role": "user", "content": "Describe the image."}
    if source == "user":
        user["content"] = [{"type": "text", "text": "Describe the image."}, image]
    provider = _MediaProvider([[_image_call("image")]] if source == "tool" else [])
    loop = make_loop(provider=provider, max_iterations=2)
    final, *_ = await loop._run_agent_loop(
        messages=[user],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="large-png"),
        publish_events=False,
        tool_registry=_ImageRegistry([{"type": "text", "text": "Image source."}, image]),
        context_compact_budget=budget,
    )
    assert final == "done"
    assert len(provider.calls) == (2 if source == "tool" else 1)
    sent = provider.calls[-1]
    media_message = next(message for message in sent if message["role"] == source)
    assert image in media_message["content"]
    assert loop_module._inline_media_bytes(sent) < loop_module.MAX_INLINE_TOOL_RESULT_MEDIA_BYTES
    assert loop_module._compact_msg_chars(sent) < 240_000


@pytest.mark.parametrize("image_type", ["image_url", "input_image"])
def test_text_budget_counts_multimodal_text_but_not_base64(large_png_url, image_type):
    image = {
        "type": image_type,
        "image_url": {"url": large_png_url} if image_type == "image_url" else large_png_url,
    }
    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "x" * 250_000}, image],
        }
    ]
    original = copy.deepcopy(messages)
    size = loop_module._compact_msg_chars(messages)
    assert 250_000 <= size < 251_000
    assert messages == original


@pytest.mark.asyncio
async def test_final_fallback_bounds_context_even_without_compaction_setting(make_loop):
    provider = _MediaProvider([[_image_call("call-1", "source " * 1_000)]])
    loop = make_loop(provider=provider, max_iterations=1)
    task = {"role": "user", "content": "Original task " + "x" * 239_000}
    final, *_ = await loop._run_agent_loop(
        messages=[task],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="budget-fallback"),
        publish_events=False,
        tool_registry=_ImageRegistry(),
    )
    assert final == "done"
    assert len(provider.calls) == 2
    assert all(loop_module._compact_msg_chars(messages) <= 240_000 for messages in provider.calls)
    assert task in provider.calls[-1]


class _SteerProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def chat(self, messages, tools=None, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        if len(self.calls) == 1:
            return LLMResponse(
                content="checking",
                tool_calls=[
                    ToolCallRequest(
                        id="call-1",
                        name="wait_for_steer",
                        arguments={},
                        tokens=1,
                    )
                ],
            )
        return LLMResponse(content="steered final")

    def get_default_model(self) -> str:
        return "fake-model"


class _BlockingSteerRegistry:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def get_definitions(self, **kwargs):
        return [
            {
                "type": "function",
                "function": {
                    "name": "wait_for_steer",
                    "description": "Wait until the test releases the tool.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    async def execute(self, tool_name, arguments, **kwargs):
        self.started.set()
        await self.release.wait()
        return "tool result"


async def _consume_outbound_event(
    bus: MessageBus, event_type: OutboundEventType, timeout: float = 2
) -> object:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for outbound event {event_type}")
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=remaining)
        if outbound.event_type == event_type:
            return outbound


async def _consume_outbound_until_response(bus: MessageBus, timeout: float = 2) -> list[object]:
    events = []
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for response event")
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=remaining)
        events.append(outbound)
        if outbound.event_type == OutboundEventType.RESPONSE:
            return events


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
async def test_context_build_messages_can_skip_system_prompt(temp_dir: Path):
    context = ContextBuilder(workspace=temp_dir / "workspace", eval=True)

    messages = await context.build_messages(
        history=[],
        current_message="steer with image",
        media=[{"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="steer-context"),
        ov_tools_enable=False,
        include_system=False,
    )

    assert [message["role"] for message in messages] == ["user", "user"]
    assert messages[-1]["content"] == [
        {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
        {"type": "text", "text": "steer with image"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["disabled", "native", "chat", "compile"])
async def test_agent_loop_gates_multimodal_result_by_provider(make_loop, monkeypatch, scope):
    content = [
        {"type": "text", "text": "Source: viking://resources/image.png"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,aW1hZ2U="},
        },
    ]

    provider: LLMProvider
    if scope in {"chat", "compile"}:
        from vikingbot.compile.service import _CompileProvider
        from vikingbot.integrations.langfuse import LangfuseClient
        from vikingbot.providers.vlm_adapter import VLMProviderAdapter

        from openviking.models.vlm import VLMFactory
        from openviking.models.vlm.base import ToolCall, VLMResponse

        vlm = VLMFactory.create({"provider": "kimi", "model": "kimi-k2"})
        completion = AsyncMock(
            side_effect=[
                VLMResponse(tool_calls=[ToolCall("call-1", "read_image", {})]),
                VLMResponse(content="done"),
            ]
        )
        monkeypatch.setattr(vlm, "get_completion_async", completion)
        adapter = VLMProviderAdapter(vlm, "kimi-k2", langfuse_client=LangfuseClient())
        provider = (
            _CompileProvider(adapter, asyncio.Semaphore(1)) if scope == "compile" else adapter
        )
    else:
        provider = _MediaProvider([[_image_call("call-1")]], supports_media=scope == "native")
    loop = make_loop(provider=provider, max_iterations=2)

    final, _reasoning, tools_used, _usage, _iteration = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "read it"}],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="multimodal"),
        publish_events=False,
        tool_registry=_ImageRegistry(content),
    )

    assert final == "done"
    sent = (
        provider.calls[1]
        if isinstance(provider, _MediaProvider)
        else completion.await_args_list[-1].kwargs["messages"]
    )
    tool_message = next(message for message in sent if message["role"] == "tool")
    assert bool(loop_module._inline_media_bytes(sent)) == (scope in {"native", "compile"})
    if scope == "native":
        assert tool_message["content"] == content
    elif scope != "compile":
        assert tool_message["content"].startswith("Image resource.")
        assert "tool-result media policy" in tool_message["content"]
    assert tools_used[0]["result"] == "Image resource."


@pytest.mark.asyncio
async def test_agent_loop_honors_tool_result_preservation_policy(make_loop, monkeypatch):
    provider = _MediaProvider([[_image_call("call-1")]])
    loop = make_loop(provider=provider, max_iterations=2)
    preview = AsyncMock(side_effect=lambda result, *_args, **_kwargs: result)
    monkeypatch.setattr(loop, "_preview_tool_result", preview)

    final, *_ = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "read it"}],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="result-policy"),
        publish_events=False,
        tool_registry=_ImageRegistry(preserve_complete_result=True),
    )

    assert final == "done"
    assert preview.await_args.kwargs["preserve_complete"] is True


@pytest.mark.asyncio
async def test_agent_loop_accepts_legacy_detailed_outcome(make_loop, monkeypatch):
    provider = _MediaProvider([[_image_call("call-1")]])
    loop = make_loop(provider=provider, max_iterations=2)
    preview = AsyncMock(side_effect=lambda result, *_args, **_kwargs: result)
    monkeypatch.setattr(loop, "_preview_tool_result", preview)

    final, *_ = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "read it"}],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="legacy-outcome"),
        publish_events=False,
        tool_registry=_ImageRegistry(legacy_outcome=True),
    )

    assert final == "done"
    assert preview.await_args.kwargs["preserve_complete"] is False


@pytest.mark.asyncio
async def test_agent_loop_limits_media_across_parallel_tool_results(make_loop, monkeypatch):
    monkeypatch.setattr(loop_tools, "MAX_INLINE_TOOL_RESULT_MEDIA_BYTES", 5)

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
    monkeypatch.setattr(loop_tools, "MAX_INLINE_TOOL_RESULT_MEDIA_BYTES", 5)

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
async def test_agent_loop_interrupt_does_not_cancel_final_session_save(
    make_loop, temp_dir: Path, monkeypatch
):
    bus = MessageBus()
    config = Config(storage_workspace=str(temp_dir))
    loop = make_loop(bus=bus, config=config)
    session_key = SessionKey(type="cli", channel_id="default", chat_id="final-save")
    msg = InboundMessage(session_key=session_key, sender_id="user-1", content="finish cleanly")
    save_started = asyncio.Event()
    release_save = asyncio.Event()
    save_calls = 0
    original_save = loop.sessions.save

    async def fake_run_agent_loop(self, **kwargs):
        return "final answer", None, [], {"prompt_tokens": 1, "completion_tokens": 1}, 1

    async def blocking_save(session):
        nonlocal save_calls
        save_calls += 1
        if len(session.messages) >= 2 and session.messages[-1].get("role") == "assistant":
            save_started.set()
            await release_save.wait()
        await original_save(session)

    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(AgentLoop, "_run_agent_loop", fake_run_agent_loop)
    monkeypatch.setattr(loop.sessions, "save", blocking_save)
    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    task = asyncio.create_task(loop._process_message_as_active_turn(msg))
    try:
        await asyncio.wait_for(save_started.wait(), timeout=2)

        assert await loop.interrupt(session_key) is False

        release_save.set()
        response = await asyncio.wait_for(task, timeout=2)

        assert response is not None
        assert response.content == "final answer"
        session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
        assert [message["role"] for message in session.messages] == ["user", "assistant"]
        assert [message["content"] for message in session.messages] == [
            "finish cleanly",
            "final answer",
        ]
        assert save_calls == 1
    finally:
        release_save.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_agent_loop_interrupt_marks_active_and_current_queued_messages(make_loop):
    bus = MessageBus()
    provider = _BlockingFirstProvider()
    loop = make_loop(bus=bus, provider=provider)
    session_key = SessionKey(type="cli", channel_id="default", chat_id="interrupt-count")

    run_task = asyncio.create_task(loop.run())
    try:
        await bus.publish_inbound(
            InboundMessage(session_key=session_key, sender_id="user-1", content="active")
        )
        await asyncio.wait_for(provider.started.wait(), timeout=2)
        for content in ("first queued", "second queued"):
            await bus.publish_inbound(
                InboundMessage(session_key=session_key, sender_id="user-1", content=content)
            )

        assert await loop.interrupt(session_key) is True
        await bus.publish_inbound(
            InboundMessage(session_key=session_key, sender_id="user-1", content="after interrupt")
        )

        for _ in range(3):
            await _consume_outbound_event(bus, OutboundEventType.INTERRUPT)
        response = await _consume_outbound_event(bus, OutboundEventType.RESPONSE)

        session = loop.sessions.get_or_create(session_key)
        assert response.content == "ok"
        assert [message["content"] for message in session.messages] == [
            "active",
            "first queued",
            "second queued",
            "after interrupt",
            "ok",
        ]
        assert len(provider.calls) == 2
    finally:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_agent_loop_steers_same_session_input_into_next_tool_turn(make_loop, temp_dir: Path):
    bus = MessageBus()
    provider = _SteerProvider()
    tools = _BlockingSteerRegistry()
    loop = make_loop(
        bus=bus,
        provider=provider,
        config=Config(storage_workspace=str(temp_dir)),
        max_iterations=3,
    )
    loop.tools = tools
    session_key = SessionKey(type="cli", channel_id="default", chat_id="steer-session")

    run_task = asyncio.create_task(loop.run())
    try:
        await bus.publish_inbound(
            InboundMessage(session_key=session_key, sender_id="user-1", content="first request")
        )
        await asyncio.wait_for(tools.started.wait(), timeout=2)
        assert (
            loop.steer(
                InboundMessage(session_key=session_key, sender_id="user-1", content="steer update")
            )
            is True
        )

        tools.release.set()

        events = await _consume_outbound_until_response(bus)
        assert [event.event_type for event in events[:4]] == [
            OutboundEventType.ITERATION,
            OutboundEventType.TOOL_CALL,
            OutboundEventType.TOOL_RESULT,
            OutboundEventType.ITERATION,
        ]
        response = events[-1]

        assert response.content == "steered final"
        assert len(provider.calls) == 2
        second_call_user_text = "\n".join(
            str(message.get("content"))
            for message in provider.calls[1]
            if message.get("role") == "user"
        )
        assert "first request" in second_call_user_text
        assert "steer update" in second_call_user_text

        session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
        assert [message["role"] for message in session.messages] == [
            "user",
            "user",
            "assistant",
        ]
        assert [message["content"] for message in session.messages] == [
            "first request",
            "steer update",
            "steered final",
        ]
        assert bus.inbound_size == 0
    finally:
        tools.release.set()
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_agent_loop_steers_same_session_input_before_final_response(
    make_loop, temp_dir: Path, monkeypatch
):
    bus = MessageBus()
    session_key = SessionKey(type="cli", channel_id="default", chat_id="steer-final-boundary")
    provider_calls = []

    class _LateSteerProvider(_FakeProvider):
        async def chat(self, messages, tools=None, **kwargs):
            provider_calls.append(copy.deepcopy(messages))
            if len(provider_calls) == 1:
                assert (
                    loop.steer(
                        InboundMessage(
                            session_key=session_key,
                            sender_id="user-1",
                            content="late steer",
                        )
                    )
                    is True
                )
                return LLMResponse(content="first final")
            assert (
                loop.steer(
                    InboundMessage(
                        session_key=session_key,
                        sender_id="user-1",
                        content="too late",
                    )
                )
                is False
            )
            return LLMResponse(content="steered final")

    loop = make_loop(
        bus=bus,
        provider=_LateSteerProvider(),
        config=Config(storage_workspace=str(temp_dir)),
        max_iterations=2,
    )

    async def prepare_steer(steer_msg):
        return loop_module._PreparedSteer(
            msg=steer_msg,
            prompt_messages=[{"role": "user", "content": steer_msg.content}],
            newly_recalled_exp_uris=[],
            newly_recalled_exp_content="",
            auto_memory_tool={
                "tool_name": "auto_memory_search",
                "result": "recalled memory",
            },
        )

    monkeypatch.setattr(loop, "_prepare_steer_message", prepare_steer)

    run_task = asyncio.create_task(loop.run())
    try:
        await bus.publish_inbound(
            InboundMessage(session_key=session_key, sender_id="user-1", content="first request")
        )

        events = await _consume_outbound_until_response(bus)
        assert [event.event_type for event in events[:4]] == [
            OutboundEventType.ITERATION,
            OutboundEventType.ITERATION,
            OutboundEventType.TOOL_CALL,
            OutboundEventType.TOOL_RESULT,
        ]
        response = events[-1]

        assert response.content == "steered final"
        assert len(provider_calls) == 2
        final_call_user_text = "\n".join(
            str(message.get("content"))
            for message in provider_calls[1]
            if message.get("role") == "user"
        )
        assert "first request" in final_call_user_text
        assert "late steer" in final_call_user_text

        session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
        assert [message["content"] for message in session.messages] == [
            "first request",
            "first final",
            "late steer",
            "steered final",
        ]
        assert bus.inbound_size == 0
    finally:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_agent_loop_does_not_share_steers_with_concurrent_internal_loop(
    make_loop,
):
    provider = _BlockingFirstProvider()
    loop = make_loop(provider=provider, max_iterations=2)
    session_key = SessionKey(type="cli", channel_id="default", chat_id="concurrent-loop")
    active_task = asyncio.create_task(
        loop._process_message_as_active_turn(
            InboundMessage(session_key=session_key, sender_id="user-1", content="first request")
        )
    )
    try:
        await asyncio.wait_for(provider.started.wait(), timeout=2)
        assert loop.steer(
            InboundMessage(session_key=session_key, sender_id="user-1", content="steer update")
        )

        assert await loop.process_direct("internal task", session_key=session_key) == "ok"

        active = loop._active_turn
        assert active is not None
        assert active.accepting_steer is True
        assert [steer.msg.content for steer in active.accepted_steers] == ["steer update"]
    finally:
        active_task.cancel()
        await asyncio.gather(active_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_agent_loop_interrupt_preserves_steers_during_batched_prepare(make_loop, monkeypatch):
    loop = make_loop()
    session_key = SessionKey(type="cli", channel_id="default", chat_id="steer-batch-interrupt")
    first_started = asyncio.Event()
    first_finished = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def prepare(steer_msg):
        if steer_msg.content == "first steer":
            first_started.set()
            await release_first.wait()
            first_finished.set()
        else:
            second_started.set()
            await asyncio.Future()
        return loop_module._PreparedSteer(
            msg=steer_msg,
            prompt_messages=[{"role": "user", "content": steer_msg.content}],
            newly_recalled_exp_uris=[],
            newly_recalled_exp_content="",
        )

    monkeypatch.setattr(loop, "_prepare_steer_message", prepare)

    async def process(_msg, active_turn=None):
        assert loop.steer(
            InboundMessage(session_key=session_key, sender_id="user-1", content="first steer")
        )

        async def add_second_steer():
            await first_started.wait()
            assert loop.steer(
                InboundMessage(session_key=session_key, sender_id="user-1", content="second steer")
            )
            release_first.set()

        asyncio.create_task(add_second_steer())
        await loop._take_accepted_steers(active_turn)

    monkeypatch.setattr(loop, "_process_message", process)
    task = asyncio.create_task(
        loop._process_message_as_active_turn(
            InboundMessage(session_key=session_key, sender_id="user-1", content="first request")
        )
    )
    try:
        await asyncio.wait_for(second_started.wait(), timeout=2)
        await asyncio.wait_for(first_finished.wait(), timeout=2)
        await asyncio.sleep(0)
        assert await loop.interrupt(session_key)
        response = await task
        assert response is not None
        assert response.event_type == OutboundEventType.INTERRUPT
        session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
        assert [message["content"] for message in session.messages] == [
            "first request",
            "first steer",
            "second steer",
        ]
    finally:
        release_first.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_agent_loop_interrupt_preserves_active_steer_input(
    make_loop, temp_dir: Path, monkeypatch
):
    bus = MessageBus()
    provider = _SteerProvider()
    tools = _BlockingSteerRegistry()
    loop = make_loop(
        bus=bus,
        provider=provider,
        config=Config(storage_workspace=str(temp_dir)),
        max_iterations=3,
    )
    loop.tools = tools
    session_key = SessionKey(type="cli", channel_id="default", chat_id="steer-interrupt-early")
    original_build_messages = ContextBuilder.build_messages

    async def recall_experience(self, *args, **kwargs):
        messages = await original_build_messages(self, *args, **kwargs)
        self.latest_recalled_exp_uris = ["viking://agent/experience/interrupted"]
        self.latest_recalled_exp_content = "interrupted experience"
        return messages

    monkeypatch.setattr(ContextBuilder, "build_messages", recall_experience)

    run_task = asyncio.create_task(loop.run())
    try:
        await bus.publish_inbound(
            InboundMessage(session_key=session_key, sender_id="user-1", content="first request")
        )
        await asyncio.wait_for(tools.started.wait(), timeout=2)
        assert (
            loop.steer(
                InboundMessage(session_key=session_key, sender_id="user-1", content="steer update")
            )
            is True
        )
        active = loop._active_turn
        assert active is not None
        steer_task = active.accepted_steers[0].task

        assert await loop.interrupt(session_key) is True

        interrupted = await _consume_outbound_event(bus, OutboundEventType.INTERRUPT)

        assert interrupted.content == ""
        session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
        assert [message["role"] for message in session.messages] == ["user", "user"]
        assert [message["content"] for message in session.messages] == [
            "first request",
            "steer update",
        ]
        assert "recalled_exp_uris" not in session.metadata
        assert bus.inbound_size == 0
        assert active.accepted_steers == []
        assert steer_task.done()
    finally:
        tools.release.set()
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_agent_loop_error_preserves_accepted_steer_input(make_loop, monkeypatch):
    loop = make_loop()
    session_key = SessionKey(type="cli", channel_id="default", chat_id="steer-error")
    started = asyncio.Event()
    release = asyncio.Event()

    async def fail_message(_msg, active_turn=None):
        started.set()
        await release.wait()
        raise RuntimeError("processing failed")

    monkeypatch.setattr(loop, "_process_message", fail_message)
    task = asyncio.create_task(
        loop._process_message_as_active_turn(
            InboundMessage(session_key=session_key, sender_id="user-1", content="first request")
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert loop.steer(
            InboundMessage(session_key=session_key, sender_id="user-1", content="steer update")
        )
        active = loop._active_turn
        assert active is not None
        steer_task = active.accepted_steers[0].task

        release.set()
        with pytest.raises(RuntimeError, match="processing failed"):
            await task

        session = loop.sessions.get_or_create(session_key, skip_heartbeat=True)
        assert [message["content"] for message in session.messages] == [
            "first request",
            "steer update",
        ]
        assert active.accepted_steers == []
        assert steer_task.done()
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


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
            "context_window_tokens": 1,
            "async_compress_ratio": 1.0,
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
        agents={"session_context_enabled": True, "context_window_tokens": 321},
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
        agents={"session_context_enabled": True, "context_window_tokens": 321},
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
        agents={"session_context_enabled": True, "context_window_tokens": 321},
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
            "latest_archive_overview": "Earlier decisions",
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
            ],
        }
    )

    monkeypatch.setattr(AgentLoop, "_get_ov_client", AsyncMock(return_value=fake_ov_client))

    loop = make_loop(
        config=Config(
            storage_workspace=str(temp_dir),
            ov_server={"server_url": "http://127.0.0.1:1933"},
            agents={"session_context_enabled": True, "context_window_tokens": 3000},
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
    assert history[0]["content"] == "[Earlier conversation summary]\nEarlier decisions"
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
            agents={"session_context_enabled": True, "context_window_tokens": 3000},
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
    "context_window_tokens, async_compress_ratio, pending_tokens, memory_window",
    [
        pytest.param(100, 1.0, 100, 50, id="token-budget"),
        pytest.param(1000, 1.0, 0, 3, id="message-window"),
    ],
)
async def test_agent_loop_commits_before_model_at_context_limit(
    make_loop,
    temp_dir: Path,
    monkeypatch,
    context_window_tokens,
    async_compress_ratio,
    pending_tokens,
    memory_window,
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
            "context_window_tokens": context_window_tokens,
            "async_compress_ratio": async_compress_ratio,
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
    assert events[1] == ("context", "cli__default__session-precommit", context_window_tokens)
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
            "context_window_tokens": 1000,
            "async_compress_ratio": 1.0,
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


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmation", [True, False, None])
@pytest.mark.parametrize("manual", [True, False])
async def test_wm_compaction_only_clears_history_after_confirmation(
    make_loop, temp_dir, monkeypatch, confirmation, manual
):
    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://ov.local"},
    )
    loop = make_loop(config=config, memory_window=1)
    key = SessionKey(type="cli", channel_id="default", chat_id="wm-compaction")
    session = loop.sessions.get_or_create(key, skip_heartbeat=True)
    session.add_message("user", "Keep this constraint")
    session.add_message("assistant", "Recorded")
    await loop.sessions.save(session)
    original = copy.deepcopy(session.messages)
    commit_result = {"status": "accepted", "archived": True}
    if confirmation is not None:
        commit_result["effective_enable_working_memory"] = confirmation
    client = SimpleNamespace(
        session_owner_user_id=lambda: None,
        append_messages=AsyncMock(return_value={"added": 2}),
        get_session=AsyncMock(return_value={"pending_tokens": 0}),
        commit_session=AsyncMock(return_value=commit_result),
    )
    hook = OpenVikingCompactHook()
    monkeypatch.setattr(hook, "_get_client", AsyncMock(return_value=(client, False)))
    monkeypatch.setattr(loop_module.hook_manager, "execute_hooks", hook.execute)
    monkeypatch.setattr(loop, "_check_cmd_auth", lambda _: True)
    loop._summarize_compact_chunk = AsyncMock(side_effect=AssertionError("WM handles compaction"))
    if manual:
        response = await loop._process_message(
            InboundMessage(session_key=key, sender_id="user", content="/compact")
        )
        assert response is not None
        if confirmation is not True:
            assert "history was kept" in response.content
    else:
        success = await loop._submit_openviking_session_and_clear_if_committed(
            session, force_commit=True
        )
        assert success is (confirmation is True)
    assert client.commit_session.await_args.kwargs["enable_working_memory"] is True
    loop._summarize_compact_chunk.assert_not_awaited()
    persisted = loop.sessions._load(key)
    if confirmation is True:
        assert persisted.messages == []
    else:
        assert persisted.messages == original
        loop._get_ov_client = AsyncMock(side_effect=AssertionError("Keep the full local history"))
        history = await loop._build_prompt_history(session)
        assert [m["content"] for m in history] == [m["content"] for m in original]


@pytest.mark.asyncio
async def test_wm_compaction_does_not_reuse_previous_hook_success(make_loop, temp_dir, monkeypatch):
    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://ov.local"},
        hooks=[],
    )
    loop = make_loop(config=config)
    key = SessionKey(type="cli", channel_id="default", chat_id="wm-stale-success")
    session = loop.sessions.get_or_create(key, skip_heartbeat=True)
    session.add_message("user", "Keep this uncommitted message")
    session.metadata["openviking"] = {
        "last_sync_status": "success",
        "last_commit_performed": True,
    }
    await loop.sessions.save(session)
    original = copy.deepcopy(session.messages)
    monkeypatch.setattr(loop_module.hook_manager, "execute_hooks", HookManager().execute_hooks)

    response = await loop._process_message(
        InboundMessage(session_key=key, sender_id="user", content="/compact")
    )

    assert "history was kept" in response.content
    assert loop.sessions._load(key).messages == original


@pytest.mark.asyncio
@pytest.mark.parametrize("manual", [True, False])
async def test_local_compaction_preserves_history_on_failure(make_loop, temp_dir, manual):
    provider = _RecordingProvider()
    provider.chat = AsyncMock(
        side_effect=[
            RuntimeError("model unavailable") if manual else LLMResponse(content="{}"),
            LLMResponse(content="reply"),
        ]
    )
    config = Config(storage_workspace=str(temp_dir), use_local_memory=True)
    loop = make_loop(config=config, provider=provider, memory_window=3)
    key = SessionKey(type="cli", channel_id="default", chat_id="local-compaction-failure")
    session = loop.sessions.get_or_create(key, skip_heartbeat=True)
    for role, content in [("user", "constraint"), ("assistant", "recorded")] * 2:
        session.add_message(role, content)
    await loop.sessions.save(session)
    original = copy.deepcopy(session.messages)

    response = await loop._process_message(
        InboundMessage(session_key=key, sender_id="user", content="/compact" if manual else "next")
    )

    persisted = loop.sessions._load(key)
    assert persisted.messages[: len(original)] == original
    if manual:
        assert persisted.messages == original
        assert "history was kept" in response.content
    else:
        assert response.content == "reply"


@pytest.mark.asyncio
async def test_local_compaction_clears_history_after_saving_memory(make_loop, temp_dir):
    config = Config(storage_workspace=str(temp_dir), use_local_memory=True)
    provider = _RecordingProvider()
    loop = make_loop(config=config, provider=provider)
    key = SessionKey(type="cli", channel_id="default", chat_id="local-compaction-success")
    session = loop.sessions.get_or_create(key, skip_heartbeat=True)
    session.add_message("user", "Keep this constraint")
    await loop.sessions.save(session)

    async def summarize(**kwargs):
        assert loop.sessions._load(key).messages == session.messages
        assert session.messages
        return LLMResponse(
            content=json.dumps({"history_entry": "Saved constraint", "memory_update": "Constraint"})
        )

    provider.chat = AsyncMock(side_effect=summarize)
    response = await loop._process_message(
        InboundMessage(session_key=key, sender_id="user", content="/compact")
    )

    assert "Memory consolidated" in response.content
    assert loop.sessions._load(key).messages == []
    assert "Saved constraint" in (loop.workspace / "memory" / "HISTORY.md").read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "manual, concurrent_feedback", [(True, False), (False, False), (False, True)]
)
async def test_local_compaction_preserves_concurrently_saved_messages(
    make_loop, temp_dir, monkeypatch, manual, concurrent_feedback
):
    config = Config(storage_workspace=str(temp_dir), use_local_memory=True)
    provider = _RecordingProvider()
    loop = make_loop(config=config, provider=provider, memory_window=3)
    key = SessionKey(type="cli", channel_id="default", chat_id="concurrent-compaction")
    session = loop.sessions.get_or_create(key, skip_heartbeat=True)
    for i in range(4):
        session.add_message("user" if i % 2 == 0 else "assistant", f"old {i}")
    session.messages[-1].update(response_id="resp-123", timestamp="2026-04-30T00:00:00")
    session.metadata["response_outcomes"] = {"existing-response": {"outcome_label": "resolved"}}
    await loop.sessions.save(session)
    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: _FakeLangfuseClient()),
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def chat(messages, **kwargs):
        if messages[0]["content"].startswith("You are a memory consolidation agent"):
            started.set()
            await release.wait()
            return LLMResponse(content=json.dumps({"history_entry": "Saved old messages"}))
        return LLMResponse(content="reply")

    provider.chat = AsyncMock(side_effect=chat)
    task = asyncio.create_task(
        loop._process_message(
            InboundMessage(
                session_key=key,
                sender_id="user",
                content="/compact" if manual else "that did not help",
                timestamp=datetime.fromisoformat("2026-04-30T00:05:00"),
            )
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        concurrent_manager = SessionManager(config.bot_data_path)
        concurrent = concurrent_manager.get_or_create(key, skip_heartbeat=True)
        concurrent.add_message("user", "scheduled reminder")
        concurrent.add_message("assistant", "reminder delivered")
        concurrent.metadata["feedback"] = {"saved": True}
        concurrent.metadata["response_outcomes"] = {
            "existing-response": {"outcome_label": "positive_feedback"},
            "concurrent-response": {"outcome_label": "resolved"},
        }
        if concurrent_feedback:
            concurrent.metadata["response_outcomes"]["resp-123"] = {
                "outcome_label": "positive_feedback"
            }
        await concurrent_manager.save(concurrent)
    finally:
        release.set()
        response = await asyncio.wait_for(task, timeout=2)

    expected = ["scheduled reminder", "reminder delivered"]
    if not manual:
        expected = ["old 2", "old 3", *expected, "that did not help", "reply"]
    persisted = loop.sessions._load(key)
    assert [m["content"] for m in persisted.messages] == expected
    assert session.messages == persisted.messages
    assert persisted.metadata["feedback"] == {"saved": True}
    outcomes = persisted.metadata["response_outcomes"]
    assert outcomes["existing-response"]["outcome_label"] == "positive_feedback"
    assert outcomes["concurrent-response"]["outcome_label"] == "resolved"
    if manual:
        assert "Memory consolidated" in response.content
    else:
        outcome_event = await loop.bus.consume_outbound()
        assert outcome_event.event_type == OutboundEventType.RESPONSE_OUTCOME_EVALUATED
        assert outcome_event.response_id == "resp-123"
        assert outcome_event.metadata["response_outcome_evaluated"]["outcome_label"] == "reasked"
        if concurrent_feedback:
            assert outcomes["resp-123"]["outcome_label"] == "positive_feedback"
        else:
            assert outcomes["resp-123"] == outcome_event.metadata["response_outcome_evaluated"]


@pytest.mark.asyncio
async def test_local_compaction_does_not_clear_a_replaced_session(make_loop, temp_dir):
    config = Config(storage_workspace=str(temp_dir), use_local_memory=True)
    provider = _RecordingProvider()
    loop = make_loop(config=config, provider=provider)
    key = SessionKey(type="cli", channel_id="default", chat_id="replaced-compaction")
    session = loop.sessions.get_or_create(key, skip_heartbeat=True)
    session.add_message("user", "old constraint")
    await loop.sessions.save(session)

    async def summarize(**kwargs):
        def replace(current):
            current.clear()
            current.add_message("user", "new session input")

        await SessionManager(config.bot_data_path).update_session(key, replace)
        return LLMResponse(content=json.dumps({"history_entry": "Saved old constraint"}))

    provider.chat = AsyncMock(side_effect=summarize)
    response = await loop._process_message(
        InboundMessage(session_key=key, sender_id="user", content="/compact")
    )

    persisted = loop.sessions._load(key)
    assert [m["content"] for m in persisted.messages] == ["new session input"]
    assert session.messages == persisted.messages
    assert "history was kept" in response.content


@pytest.mark.asyncio
async def test_repeated_compaction_keeps_inflight_session_writes(make_loop, temp_dir):
    config = Config(storage_workspace=str(temp_dir), use_local_memory=True)
    provider = _RecordingProvider()
    provider.chat = AsyncMock(
        return_value=LLMResponse(content=json.dumps({"history_entry": "Archived messages"}))
    )
    loop = make_loop(config=config, provider=provider)
    key = SessionKey(type="cli", channel_id="default", chat_id="repeated-compaction")
    inflight = loop.sessions.get_or_create(key, skip_heartbeat=True)
    inflight.add_message("user", "old input")
    await loop.sessions.save(inflight)

    for i in range(2):
        await loop._process_message(
            InboundMessage(session_key=key, sender_id="user", content="/compact")
        )
        current = loop.sessions.get_or_create(key, skip_heartbeat=True)
        current.add_message("user", f"latest input {i}")
        await loop.sessions.save(current)

    inflight.add_message("assistant", "background task finished")
    await loop.sessions.save(inflight)

    assert [m["content"] for m in loop.sessions._load(key).messages] == [
        "latest input 1",
        "background task finished",
    ]


@pytest.mark.asyncio
async def test_local_prompt_history_honors_memory_window(make_loop):
    loop = make_loop(memory_window=100)
    session = loop.sessions.get_or_create(
        SessionKey(type="cli", channel_id="default", chat_id="local-history-window"),
        skip_heartbeat=True,
    )
    for i in range(60):
        session.add_message("user" if i % 2 == 0 else "assistant", f"message {i}")

    history = await loop._build_prompt_history(session)

    assert [m["content"] for m in history] == [m["content"] for m in session.messages]


@pytest.mark.asyncio
async def test_new_session_resets_experience_exclusions(make_loop, temp_dir):
    loop = make_loop(
        config=Config(storage_workspace=str(temp_dir), ov_server={"server_url": "http://ov.local"})
    )
    key = SessionKey(type="cli", channel_id="default", chat_id="new-experience")
    session = loop.sessions.get_or_create(key, skip_heartbeat=True)
    session.add_message("user", "[Experience Reminder] Old experience")
    session.metadata["recalled_exp_uris"] = ["viking://agent/experience/old"]
    session.metadata["response_facts"] = {"response": {"response_length": 1}}
    session.metadata["openviking"] = {"session_id": "old-session"}
    await loop.sessions.save(session)

    await loop._process_message(InboundMessage(session_key=key, sender_id="user", content="/new"))

    persisted = loop.sessions._load(key)
    assert persisted.messages == []
    assert not persisted.metadata.get("recalled_exp_uris")
    assert persisted.metadata["response_facts"] == {"response": {"response_length": 1}}
    assert persisted.metadata["openviking"]["session_id"] != "old-session"


@pytest.mark.asyncio
async def test_new_session_preserves_history_when_persistence_fails(make_loop, monkeypatch):
    loop = make_loop()
    key = SessionKey(type="cli", channel_id="default", chat_id="new-persistence-failure")
    session = loop.sessions.get_or_create(key, skip_heartbeat=True)
    session.add_message("user", "Keep this conversation")
    session.metadata["recalled_exp_uris"] = ["viking://agent/experience/kept"]
    await loop.sessions.save(session)
    original = session.clone()

    def fail_save(_session):
        raise OSError("session write failed")

    monkeypatch.setattr(loop.sessions, "_save_unlocked", fail_save)
    with pytest.raises(OSError, match="session write failed"):
        await loop._process_message(
            InboundMessage(session_key=key, sender_id="user", content="/new")
        )

    persisted = loop.sessions._load(key)
    cached = loop.sessions.get_or_create(key)
    assert persisted.messages == original.messages
    assert cached.messages == original.messages
    assert session.messages == original.messages
    assert cached.metadata == original.metadata


@pytest.mark.asyncio
async def test_remember_requires_storage(make_loop, monkeypatch):
    loop = make_loop()
    key = SessionKey(type="cli", channel_id="default", chat_id="remember-no-storage")
    session = loop.sessions.get_or_create(key, skip_heartbeat=True)
    session.add_message("user", "Remember this constraint")
    commit = AsyncMock()
    monkeypatch.setattr(loop, "_consolidate_viking_memory", commit)

    response = await loop._process_message(
        InboundMessage(session_key=key, sender_id="user", content="/remember")
    )

    assert "not enabled" in response.content
    assert len(session.messages) == 1
    commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_remember_uses_commit_result(make_loop, temp_dir, legacy_commit, success):
    loop = make_loop(
        config=Config(
            storage_workspace=str(temp_dir),
            ov_server={"server_url": "http://ov.local"},
            agents={"session_context_enabled": False},
        )
    )
    key = SessionKey(type="cli", channel_id="default", chat_id="remember-result")
    session = loop.sessions.get_or_create(key, skip_heartbeat=True)
    session.add_message("user", "Remember this constraint")
    session.metadata["openviking"] = {"last_sync_status": "success"}
    if not success:
        legacy_commit.side_effect = RuntimeError("remote commit failed")

    response = await loop._process_message(
        InboundMessage(session_key=key, sender_id="user", content="/remember")
    )

    assert ("has been submitted" in response.content) is success
    legacy_commit.assert_awaited_once()
    if not success:
        assert "Failed to submit" in response.content
    assert len(session.messages) == 1


def test_message_options_use_request_overrides_and_channel_defaults(make_loop, monkeypatch):
    loop = make_loop()
    channel = SimpleNamespace(
        profile_user_list=["profile"], memory_peer=["channel-peer"], memory_user=["channel-owner"]
    )
    monkeypatch.setattr(loop, "_get_channel_config", lambda _: channel)
    connection = {"user_id": "request-user"}
    msg = InboundMessage(
        session_key=SessionKey(type="cli", channel_id="default", chat_id="message-options"),
        sender_id="sender",
        actor_peer_id="actor",
        content="question",
        openviking_connection=connection,
        metadata={"memory_peers": [" request-peer ", "request-peer"], "memory_users": []},
    )

    options = loop._resolve_message_options(msg, ov_tools_enable=True)

    assert options.actor_peer_id == "actor"
    assert options.openviking_connection is connection
    assert options.profile_user_list == ["profile"]
    assert options.memory_peer_ids == ["request-peer"]
    assert options.memory_owner_user_ids == ["channel-owner"]
    disabled = loop._resolve_message_options(msg, ov_tools_enable=False)
    assert disabled.profile_user_list == []
    assert disabled.memory_peer_ids == ["request-peer"]
    assert disabled.memory_owner_user_ids == []


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_success", [True, False])
async def test_legacy_compaction_commits_before_local_summary(
    make_loop, temp_dir, legacy_commit, remote_success
):
    config = Config(
        storage_workspace=str(temp_dir),
        ov_server={"server_url": "http://ov.local"},
        agents={"session_context_enabled": False},
    )
    provider = _RecordingProvider()
    loop = make_loop(config=config, provider=provider)
    if not remote_success:
        legacy_commit.side_effect = RuntimeError("remote commit failed")

    async def summarize(**kwargs):
        legacy_commit.assert_awaited_once()
        return LLMResponse(content=json.dumps({"history_entry": "Saved constraint"}))

    provider.chat = AsyncMock(side_effect=summarize)
    key = SessionKey(type="cli", channel_id="default", chat_id="legacy-compaction")
    session = loop.sessions.get_or_create(key, skip_heartbeat=True)
    session.add_message("user", "Keep this constraint")
    await loop.sessions.save(session)
    original = copy.deepcopy(session.messages)

    response = await loop._process_message(
        InboundMessage(session_key=key, sender_id="user", content="/compact")
    )

    legacy_commit.assert_awaited_once()
    if remote_success:
        provider.chat.assert_awaited_once()
        assert loop.sessions._load(key).messages == []
        assert "Saved constraint" in (loop.workspace / "memory" / "HISTORY.md").read_text()
    else:
        provider.chat.assert_not_awaited()
        assert "history was kept" in response.content
        assert loop.sessions._load(key).messages == original


@pytest.mark.asyncio
@pytest.mark.parametrize("connection_tools", [None, set(), {"first"}])
async def test_loop_request_context_preserves_tool_scope_and_metadata_isolation(
    make_loop, connection_tools
):
    contexts = {}

    class RecordingTool(Tool):
        name = "record"
        description = "Record tool context"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, tool_context, **kwargs):
            contexts[self.name] = tool_context
            tool_context.channel_metadata["tool"] = self.name
            return "recorded"

    calls = [
        ToolCallRequest(id=name, name=name, arguments={}, tokens=1) for name in ("first", "second")
    ]
    provider = _MediaProvider([calls])
    loop = make_loop(provider=provider)
    for call in calls:
        tool = RecordingTool()
        tool.name = call.name
        loop.tools.register(tool)
    key = SessionKey(type="cli", channel_id="default", chat_id="tool-request-context")
    connection = {"user_id": "request-user"}
    metadata = {"message_id": "message"}

    final, *_ = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "Record both contexts"}],
        session_key=key,
        publish_events=False,
        sender_id="sender",
        actor_peer_id="actor",
        memory_peer_ids=["memory-peer"],
        memory_owner_user_ids=["memory-owner"],
        openviking_connection=connection,
        openviking_tool_names=connection_tools,
        channel_metadata=metadata,
    )

    assert final == "done"
    assert metadata == {"message_id": "message"}
    assert set(contexts) == {"first", "second"}
    for name, context in contexts.items():
        assert context.session_key == key
        assert context.sender_id == "sender"
        assert context.actor_peer_id == "actor"
        assert context.memory_peer_ids == ["memory-peer"]
        assert context.memory_owner_user_ids == ["memory-owner"]
        assert context.channel_metadata == {"message_id": "message", "tool": name}
        expected_connection = (
            connection if connection_tools is None or name in connection_tools else None
        )
        assert context.openviking_connection == expected_connection


@pytest.mark.asyncio
@pytest.mark.parametrize("activation_success", [True, False])
async def test_skill_activation_blocks_other_tools_until_next_turn(make_loop, activation_success):
    activate = ToolCallRequest(id="activate", name="activate", arguments={}, tokens=1)
    regular = ToolCallRequest(id="regular", name="regular", arguments={}, tokens=1)
    retry = ToolCallRequest(id="retry", name="regular", arguments={}, tokens=1)
    provider = _MediaProvider([[regular, activate], [retry]])
    loop = make_loop(provider=provider)
    registry = SimpleNamespace(
        get_definitions=lambda **kwargs: [
            {"type": "function", "function": {"name": name}} for name in ("activate", "regular")
        ],
        execute_detailed=AsyncMock(
            side_effect=[
                ToolExecutionResult(
                    result="activated" if activation_success else "Error: activation failed",
                    effective_params={},
                ),
                ToolExecutionResult(result="regular result", effective_params={}),
            ]
        ),
    )
    runtime = SimpleNamespace(
        is_activation_call=lambda name, args: name == "activate",
        activation_succeeded=lambda args: activation_success,
    )

    final, _reasoning, tools_used, *_ = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "Activate the Skill and use the tool"}],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="activation-order"),
        publish_events=False,
        tool_registry=registry,
        skill_runtime=runtime,
    )

    assert final == "done"
    assert [call.args[0] for call in registry.execute_detailed.await_args_list] == [
        "activate",
        "regular",
    ]
    assert [tool["tool_call_id"] for tool in tools_used] == ["regular", "activate", "retry"]
    expected_error = "SKILL_CONTEXT_UPDATED" if activation_success else "SKILL_ACTIVATION_FAILED"
    assert expected_error in tools_used[0]["result"]
    assert tools_used[2]["result"] == "regular result"
