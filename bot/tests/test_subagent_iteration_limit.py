"""A subagent that hits the tool-use iteration limit must still return a real
answer (parity with the main agent loop, #2810) instead of discarding all the
gathered tool work as a content-free placeholder."""

from pathlib import Path

import pytest

from vikingbot.agent import subagent as subagent_module
from vikingbot.agent.subagent import SubagentManager
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import Config, SessionKey
from vikingbot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class _FakeToolRegistry:
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
        self.execute_calls.append((tool_name, arguments))
        return "tool result: useful context"


class _ToolLimitProvider(LLMProvider):
    """Always asks for a tool call until the model is told tools are unavailable."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def chat(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": list(tools or [])})
        if tools:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id=f"call-{len(self.calls)}",
                        name="lookup_fact",
                        arguments={"query": "current facts"},
                        tokens=3,
                    )
                ],
            )
        return LLMResponse(content="final answer from gathered tool results")

    def get_default_model(self) -> str:
        return "fake-model"


@pytest.mark.asyncio
async def test_subagent_makes_final_no_tool_call_when_iteration_limit_reached(
    tmp_path: Path, monkeypatch
):
    # Keep the loop body focused on the provider/tool interaction.
    monkeypatch.setattr(subagent_module, "ToolRegistry", _FakeToolRegistry)
    monkeypatch.setattr(
        "vikingbot.agent.tools.register_subagent_tools",
        lambda **kwargs: None,
    )

    class _NoMemory:
        def __init__(self, *a, **k):
            pass

        async def get_viking_experience_context(self, *a, **k):
            return ""

    monkeypatch.setattr("vikingbot.agent.memory.MemoryStore", _NoMemory)

    provider = _ToolLimitProvider()
    config = Config(storage_workspace=str(tmp_path))
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path / "workspace",
        bus=MessageBus(),
        config=config,
        model="fake-model",
    )

    announced: dict[str, object] = {}

    async def _capture(task_id, label, task, final_result, session_key, status):
        announced["final_result"] = final_result
        announced["status"] = status

    monkeypatch.setattr(mgr, "_announce_result", _capture)

    session_key = SessionKey(type="cli", channel_id="default", chat_id="subagent-limit")
    await mgr._run_subagent("task-1", "please answer with lookup", "label", session_key)

    # The model called tools on every one of the 15 iterations, so the subagent
    # made one extra tools=[] call to obtain a real final answer.
    assert announced["status"] == "ok"
    assert announced["final_result"] == "final answer from gathered tool results"
    assert len(provider.calls) == 16
    assert provider.calls[-1]["tools"] == []
    assert provider.calls[-1]["messages"][-1]["content"].startswith(
        "Tool-use iteration limit reached."
    )
    # The gathered tool results are still in the context handed to the final call.
    assert any(
        m.get("content") == "tool result: useful context"
        for m in provider.calls[-1]["messages"]
    )
