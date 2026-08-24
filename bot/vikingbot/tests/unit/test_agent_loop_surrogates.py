# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Lone-surrogate (U+D800-U+DFFF) safety for tool results entering the agent loop (#4238)."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from vikingbot.agent.context import ContextBuilder
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.tools.registry import ToolExecutionResult
from vikingbot.config.schema import SessionKey


class _FakeResponse:
    def __init__(self, tool_calls, content=""):
        self.has_tool_calls = bool(tool_calls)
        self.tool_calls = tool_calls
        self.content = content
        self.reasoning_content = None
        self.usage = None


class _FakeProvider:
    def __init__(self, responses):
        self._responses = list(responses)

    async def chat_stream(self, **kwargs):
        yield SimpleNamespace(type="response", response=self._responses.pop(0))


class _FakeRegistry:
    def __init__(self, result, arguments):
        self._result = result
        self._arguments = arguments

    def get_definitions(self, **kwargs):
        return [
            {
                "type": "function",
                "function": {"name": "list", "parameters": {"type": "object", "properties": {}}},
            }
        ]

    async def execute_detailed(self, name, arguments, **kwargs):
        return ToolExecutionResult(result=self._result, effective_params=dict(arguments))


def _build_loop(tmp_path: Path, provider, registry) -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop.max_iterations = 2
    loop.context = ContextBuilder(tmp_path, enable_subagents=False)
    loop.provider = provider
    loop.bus = None
    loop.sandbox_manager = None
    loop.sessions = None
    loop.tools = registry
    loop.model = "fake-model"
    loop.temperature = 0.0
    return loop


@pytest.mark.asyncio
async def test_tool_result_surrogate_is_sanitized_in_message_flow(tmp_path: Path):
    surrogate_args = {"uri": "viking://resources/\ud800bad_name"}
    provider = _FakeProvider(
        [
            _FakeResponse(
                tool_calls=[
                    SimpleNamespace(
                        id="call_1", name="list", arguments=surrogate_args, tokens=None
                    )
                ]
            ),
            _FakeResponse(tool_calls=[], content="done"),
        ]
    )
    registry = _FakeRegistry(
        result="listed viking://resources/\ud800bad_name", arguments=surrogate_args
    )
    loop = _build_loop(tmp_path, provider, registry)
    key = SessionKey(type="test", channel_id="channel", chat_id="chat")
    messages: list = []
    captured_turns: list = []

    final_content, _, tools_used, _, _ = await loop._run_agent_loop(
        messages=messages,
        session_key=key,
        publish_events=False,
        ov_tools_enable=False,
        inject_write_experience=False,
        tool_registry=registry,
        captured_turns=captured_turns,
    )

    assert final_content == "done"

    # The tool result that flows back into the transcript must be surrogate-free.
    tool_messages = [m for m in messages if m.get("role") == "tool"]
    assert tool_messages, "expected a tool message in the transcript"
    content = tool_messages[0]["content"]
    assert "\ud800" not in content
    assert "�" in content

    # The persisted tool-use dict (stored into session JSONL via agent_turns)
    # must be surrogate-free as well, including the echoed arguments.
    assert len(captured_turns) == 1
    record = captured_turns[0]["tool_calls"][0]
    assert "\ud800" not in record["result"]
    assert "�" in record["result"]
    assert "\ud800" not in record["resolved_args"]["uri"]
    assert "�" in record["resolved_args"]["uri"]

    # tools_used also holds the sanitized result.
    assert "\ud800" not in tools_used[0]["result"]
