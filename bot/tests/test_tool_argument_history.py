"""Preserve valid provider JSON across the tool execution/history boundary."""

import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.context import ContextBuilder
from vikingbot.agent.loop.loop_tools import LoopToolMixin
from vikingbot.providers.base import ToolCallRequest, build_stream_response
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM


def test_wire_metadata_does_not_change_legacy_tool_equality():
    expected = ToolCallRequest("call", "read", {"path": "a.md"}, 0)
    actual = ToolCallRequest("call", "read", {"path": "a.md"}, 0, raw_arguments='{"path":"a.md"}')
    assert actual == expected


async def execute_turn(response):
    tools = NS(execute=AsyncMock(return_value="ok"))
    context = NS(
        add_assistant_message=lambda *a, **kw: ContextBuilder.add_assistant_message(None, *a, **kw),
        add_tool_result=lambda messages, *args: messages,
    )
    loop = NS(
        context=context, sandbox_manager=None, _preview_tool_result=AsyncMock(return_value="ok")
    )
    request = NS(
        session_key=NS(safe_name=lambda: "synthetic"),
        skill_runtime=None,
        openviking_connection=None,
        sender_id=None,
        actor_peer_id=None,
        memory_peer_ids=None,
        memory_owner_user_ids=None,
        channel_metadata=None,
    )
    messages, _ = await LoopToolMixin._execute_tool_turn(
        loop,
        response,
        [],
        request,
        active_tools=tools,
        visible_tool_names={"read"},
        scoped_openviking_tools=None,
        tools_used=[],
        publish_events=False,
        captured_turns=[],
        agent_id="synthetic",
        iteration=1,
    )
    tools.execute.assert_awaited_once()
    assert tools.execute.await_args.args[1] == response.tool_calls[0].arguments
    return messages[0]["tool_calls"][0]["function"]["arguments"]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", [OpenAIVLM, LiteLLMVLMProvider, VolcEngineVLM])
async def test_backend_adapter_and_tool_history_preserve_wire_json(backend):
    raw = '{ "title" : "example", "path":"a.md" }'
    model = backend({"model": "synthetic", "provider": "openai"})
    sdk = NS(
        choices=[
            NS(
                finish_reason="tool_calls",
                message=NS(
                    content=None,
                    reasoning_content=None,
                    tool_calls=[NS(id="call", function=NS(name="read", arguments=raw))],
                ),
            )
        ],
        usage=None,
    )
    parsed = model._build_vlm_response(sdk, has_tools=True)
    adapter = VLMProviderAdapter(model, "synthetic", langfuse_client=NS(enabled=False))
    replay = await execute_turn(adapter._convert_response(parsed))
    assert replay == raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw,args,preserve",
    [
        ('{ "path" : "a.md" }', {"path": "a.md"}, True),
        ('{"path":"a.md",}', {"path": "a.md"}, False),
        ('{"path":"a.md","path":"b.md"}', {"path": "b.md"}, False),
        ('{"value":1}', {"value": True}, False),
        ('{"value":NaN}', {"value": 0}, False),
        ('{"path":"different"}', {"path": "a.md"}, False),
        ('{"path":"a.md"', {"raw": '{"path":"a.md"'}, False),
    ],
)
async def test_stream_history_replays_only_strict_unchanged_arguments(raw, args, preserve):
    response = build_stream_response(
        content="",
        reasoning_content="",
        finish_reason="tool_calls",
        raw_tool_calls={0: {"id": "call", "name": "read", "arguments": raw}},
    )
    # A parser/validator may change arguments before tool execution.
    response.tool_calls[0].arguments = args
    replay = await execute_turn(response)
    assert (replay == raw) is preserve
    assert json.loads(replay) == args
