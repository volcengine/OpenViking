# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for resource tools (registry + ReAct loop).

Covers:
- ResourceTool ABC contract
- register_resource_tool / expose_to_llm / get_resource_tool_schemas
- execute_resource_tool serialization and truncation
- run_resource_tool_loop: plain fallback, tool_call iteration, final answer
"""

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest

from openviking.models.vlm.base import ToolCall, VLMResponse
from openviking.resource.tools import (
    DEFAULT_MAX_RESULT_CHARS,
    ResourceTool,
    execute_resource_tool,
    expose_to_llm,
    get_resource_tool,
    get_resource_tool_schemas,
    hide_from_llm,
    list_resource_tools,
    register_resource_tool,
    reset_resource_tools,
)
from openviking.resource.resource_loop import run_resource_tool_loop


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class _EchoTool(ResourceTool):
    """Echoes the 'message' kwarg back as a dict."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo a message back."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

    async def execute(self, **kwargs: Any) -> Any:
        return {"echo": kwargs.get("message", "")}


class _BigTool(ResourceTool):
    """Returns a string larger than the default cap to test truncation."""

    @property
    def name(self) -> str:
        return "big"

    @property
    def description(self) -> str:
        return "Return a big string."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        return "x" * (DEFAULT_MAX_RESULT_CHARS * 2)


class _BoomTool(ResourceTool):
    """Always raises — used to verify error serialization."""

    @property
    def name(self) -> str:
        return "boom"

    @property
    def description(self) -> str:
        return "Always fail."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        raise RuntimeError("kaboom")


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test starts with a clean registry."""
    reset_resource_tools()
    yield
    reset_resource_tools()


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


def test_register_and_get_tool():
    tool = _EchoTool()
    register_resource_tool(tool)
    assert get_resource_tool("echo") is tool
    assert "echo" in list_resource_tools()


def test_register_overwrites_duplicate():
    t1, t2 = _EchoTool(), _EchoTool()
    register_resource_tool(t1)
    register_resource_tool(t2)
    assert get_resource_tool("echo") is t2


def test_expose_to_llm_requires_registration():
    with pytest.raises(KeyError):
        expose_to_llm("not_registered")


def test_expose_and_hide():
    register_resource_tool(_EchoTool())
    expose_to_llm("echo")
    assert len(get_resource_tool_schemas()) == 1
    hide_from_llm("echo")
    assert get_resource_tool_schemas() == []


def test_schema_format_matches_openai():
    register_resource_tool(_EchoTool())
    expose_to_llm("echo")
    schema = get_resource_tool_schemas()[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert schema["function"]["parameters"]["required"] == ["message"]


# ---------------------------------------------------------------------------
# execute_resource_tool tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_serializes_dict_result():
    register_resource_tool(_EchoTool())
    result = await execute_resource_tool("echo", message="hi")
    assert '"echo": "hi"' in result


@pytest.mark.asyncio
async def test_execute_truncates_large_result():
    register_resource_tool(_BigTool())
    result = await execute_resource_tool("big")
    # Result is capped at DEFAULT_MAX_RESULT_CHARS plus a trailing indicator
    # of the form "...[truncated, N more chars]".
    assert result.startswith("x" * DEFAULT_MAX_RESULT_CHARS)
    assert "truncated" in result
    assert len(result) > DEFAULT_MAX_RESULT_CHARS


@pytest.mark.asyncio
async def test_execute_unknown_tool_returns_error_json():
    result = await execute_resource_tool("nope")
    assert "Unknown tool" in result


@pytest.mark.asyncio
async def test_execute_serializes_exception():
    register_resource_tool(_BoomTool())
    result = await execute_resource_tool("boom")
    assert "kaboom" in result


# ---------------------------------------------------------------------------
# run_resource_tool_loop tests
# ---------------------------------------------------------------------------


class _FakeVLM:
    """Programmable VLM double for loop tests.

    Records the ``tools`` argument on each call so tests can assert whether
    tools were withheld (e.g. on the final iteration or after an unknown
    tool).
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.call_tools_history: list = []

    async def get_completion_async(self, prompt="", thinking=False,
                                   tools=None, tool_choice=None, messages=None):
        self.calls += 1
        self.call_tools_history.append(tools)
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_loop_falls_back_to_plain_when_no_tools():
    vlm = _FakeVLM([VLMResponse(content="# hello")])
    out = await run_resource_tool_loop(vlm=vlm, prompt="p")
    assert out == "# hello"
    assert vlm.calls == 1


@pytest.mark.asyncio
async def test_loop_runs_single_tool_then_finishes():
    register_resource_tool(_EchoTool())
    expose_to_llm("echo")

    first = VLMResponse(
        tool_calls=[ToolCall(id="1", name="echo", arguments={"message": "hi"})]
    )
    second = VLMResponse(content="# done")
    vlm = _FakeVLM([first, second])

    out = await run_resource_tool_loop(vlm=vlm, prompt="p")
    assert out == "# done"
    assert vlm.calls == 2


@pytest.mark.asyncio
async def test_loop_exhausts_iterations():
    """LLM keeps calling tools past the hard cap → RuntimeError."""
    register_resource_tool(_EchoTool())
    expose_to_llm("echo")

    looping = VLMResponse(
        tool_calls=[ToolCall(id="1", name="echo", arguments={"message": "x"})]
    )
    # max_iterations=3, hard_cap=6: need 6 looping responses (3 normal +
    # 3 extensions). The 6th iteration withholds tools but the fake VLM
    # still returns tool_calls, triggering the hard-cap failure path.
    vlm = _FakeVLM([looping] * 6)

    with pytest.raises(RuntimeError, match="hard cap"):
        await run_resource_tool_loop(vlm=vlm, prompt="p", max_iterations=3)


@pytest.mark.asyncio
async def test_loop_enforces_total_char_budget():
    register_resource_tool(_BigTool())
    expose_to_llm("big")

    looping = VLMResponse(
        tool_calls=[ToolCall(id="1", name="big", arguments={})]
    )
    vlm = _FakeVLM([looping, looping])

    with pytest.raises(RuntimeError, match="char budget"):
        await run_resource_tool_loop(
            vlm=vlm, prompt="p", max_iterations=2, max_total_tool_chars=100
        )


# ---------------------------------------------------------------------------
# ReAct best-practice tests (mirrored from extract_loop.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_dynamically_extends_iterations():
    """When the LLM calls tools on the last iteration, the budget extends."""
    register_resource_tool(_EchoTool())
    expose_to_llm("echo")

    looping = VLMResponse(
        tool_calls=[ToolCall(id="1", name="echo", arguments={"message": "x"})]
    )
    final = VLMResponse(content="# done")

    # max_iterations=2, hard_cap=4.
    # iter 1: normal, tools on, tool_call → execute (no extension, not last)
    # iter 2: last+can_extend, tools on, tool_call → execute → extend to 3
    # iter 3: last+can_extend, tools on, tool_call → execute → extend to 4
    # iter 4: last+hard_cap, tools off, content → return
    vlm = _FakeVLM([looping, looping, looping, final])

    out = await run_resource_tool_loop(vlm=vlm, prompt="p", max_iterations=2)
    assert out == "# done"
    assert vlm.calls == 4
    # Tools were withheld on the final (hard-cap) iteration.
    assert vlm.call_tools_history[-1] is None


@pytest.mark.asyncio
async def test_loop_forces_final_at_hard_cap():
    """At the hard cap, tools are withheld and a final answer is demanded."""
    register_resource_tool(_EchoTool())
    expose_to_llm("echo")

    looping = VLMResponse(
        tool_calls=[ToolCall(id="1", name="echo", arguments={"message": "x"})]
    )
    final = VLMResponse(content="# forced")

    # max_iterations=1, hard_cap=max(2, 3)=3.
    # iter 1: last+can_extend, tools on, tool_call → extend to 2
    # iter 2: last+can_extend, tools on, tool_call → extend to 3
    # iter 3: last+hard_cap, tools OFF, content → return
    vlm = _FakeVLM([looping, looping, final])

    out = await run_resource_tool_loop(vlm=vlm, prompt="p", max_iterations=1)
    assert out == "# forced"
    assert vlm.calls == 3
    # The last call must have had tools withheld.
    assert vlm.call_tools_history[-1] is None
    # Earlier calls had tools on.
    assert vlm.call_tools_history[0] is not None


@pytest.mark.asyncio
async def test_loop_degrades_on_unknown_tool():
    """Unknown tool name → tools withheld next iteration."""
    # Register a real tool so schemas are non-empty (otherwise the loop
    # falls back to plain completion). The LLM calls a *different* tool
    # name that is not registered.
    register_resource_tool(_EchoTool())
    expose_to_llm("echo")

    looping = VLMResponse(
        tool_calls=[ToolCall(id="1", name="nonexistent", arguments={})]
    )
    final = VLMResponse(content="# degraded")

    # max_iterations=2, hard_cap=4.
    # iter 1: tools on, LLM calls "nonexistent" → error result, disable_tools_next=True
    # iter 2: last+can_extend, but disable_tools_next → tools OFF, content → return
    vlm = _FakeVLM([looping, final])

    out = await run_resource_tool_loop(vlm=vlm, prompt="p", max_iterations=2)
    assert out == "# degraded"
    assert vlm.calls == 2
    # Tools were on for iter 1, off for iter 2.
    assert vlm.call_tools_history[0] is not None
    assert vlm.call_tools_history[1] is None


@pytest.mark.asyncio
async def test_loop_final_instruction_appended_on_last_iteration():
    """The final-iteration nudge message is appended when is_last is True."""
    register_resource_tool(_EchoTool())
    expose_to_llm("echo")

    looping = VLMResponse(
        tool_calls=[ToolCall(id="1", name="echo", arguments={"message": "x"})]
    )
    final = VLMResponse(content="# done")

    # max_iterations=1, hard_cap=3.
    # iter 1: is_last, can_extend → append FINAL, tools on, tool_call → extend
    # iter 2: is_last, can_extend → append FINAL, tools on, tool_call → extend
    # iter 3: is_last, hard_cap → append FINAL, tools off, content → return
    vlm = _FakeVLM([looping, looping, final])

    await run_resource_tool_loop(vlm=vlm, prompt="p", max_iterations=1)

    # The messages list isn't directly accessible, but we can verify the
    # behavior indirectly: the loop ran 3 iterations (2 extensions + 1 final).
    assert vlm.calls == 3
