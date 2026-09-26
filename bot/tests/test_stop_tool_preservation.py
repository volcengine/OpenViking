"""Tests for stop-tool empty-reply preservation in AgentLoop._run_agent_loop.

When the model returns empty final content, that empty answer must be preserved
(instead of replaced by a filler string) whenever a stop tool was used ANYWHERE
in the turn -- not only when the stop tool happened to be the LAST tool in a
batch. The fix replaces a ``tools_used[-1]`` positional check with ``any(...)``
over the whole ``tools_used`` list, and also guards the post-loop filler
fallback (the second ``final_content`` guard near the end of the method) against
overwriting a legitimate stop-tool empty reply.

Coverage comes in three layers: (1) source assertions that the real
implementation uses the position-independent ``any(...)`` predicate at BOTH
guard sites, (2) behavioral checks on an equivalent predicate covering all
batch positions and edge cases, and (3) end-to-end tests that drive the real
``_run_agent_loop`` with a fake provider and tool registry to prove the empty
stop-tool reply survives both guards (and that the filler still applies when
no stop tool ran).
"""

import inspect

from vikingbot.agent.loop import AgentLoop
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import Config, SessionKey
from vikingbot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


def _stop_reply_preserved(final_content, tools_used, stop_tools):
    """Mirror of the fixed guard: preserve an empty reply if any stop tool ran."""
    return final_content == "" and any(
        t.get("tool_name") in stop_tools for t in tools_used
    )


# ---------------------------------------------------------------------------
# Source-code assertions: verify the real implementation uses the correct
# position-independent ``any(...)`` pattern at every relevant guard site.
# ---------------------------------------------------------------------------


def test_source_uses_position_independent_any_check():
    """The line-978 guard must use ``any(...)``, not positional indexing."""
    src = inspect.getsource(AgentLoop._run_agent_loop)
    assert 'any(t.get("tool_name") in stop_tools for t in tools_used)' in src
    # The old, batch-position-sensitive form must be gone.
    assert 'tools_used[-1].get("tool_name") in stop_tools' not in src


def test_source_guards_post_loop_filler_with_stop_tool_check():
    """The line-1019 filler fallback must also respect stop-tool usage.

    Even after the primary guard at line 978 preserves the empty content, a
    second ``final_content`` check near the end of the method unconditionally
    replaces an empty or None ``final_content`` with a filler message.  That
    second guard must skip the replacement when any stop tool was used,
    otherwise the stop-tool empty reply is always clobbered.
    """
    src = inspect.getsource(AgentLoop._run_agent_loop)
    # The second guard must also reference stop_tools to avoid overwriting
    # a legitimate stop-tool empty reply.
    assert 'not any(t.get("tool_name") in stop_tools for t in tools_used)' in src, (
        "The post-loop filler guard (near the end of _run_agent_loop) must "
        "check stop_tools before replacing empty final_content."
    )


# ---------------------------------------------------------------------------
# Predicate behavioral tests: cover every position in a multi-tool batch and
# all relevant edge cases.
# ---------------------------------------------------------------------------

STOP_TOOLS = {"finish"}


def test_stop_tool_first_in_batch():
    """Stop tool is the very first entry in a multi-tool batch."""
    tools_used = [
        {"tool_name": "finish"},
        {"tool_name": "search"},
        {"tool_name": "read_file"},
    ]
    assert _stop_reply_preserved("", tools_used, STOP_TOOLS) is True


def test_stop_tool_middle_in_batch():
    """Stop tool is surrounded by other tools."""
    tools_used = [
        {"tool_name": "search"},
        {"tool_name": "finish"},
        {"tool_name": "read_file"},
    ]
    assert _stop_reply_preserved("", tools_used, STOP_TOOLS) is True


def test_stop_tool_last_in_batch():
    """Stop tool is the last entry (the case the original positional check
    happened to cover by accident)."""
    tools_used = [
        {"tool_name": "search"},
        {"tool_name": "read_file"},
        {"tool_name": "finish"},
    ]
    assert _stop_reply_preserved("", tools_used, STOP_TOOLS) is True


def test_stop_tool_only_tool():
    """Batch contains exactly one tool and it is the stop tool."""
    tools_used = [{"tool_name": "finish"}]
    assert _stop_reply_preserved("", tools_used, STOP_TOOLS) is True


def test_multiple_stop_tools_in_batch():
    """Batch contains more than one stop tool (should still detect)."""
    tools_used = [
        {"tool_name": "finish"},
        {"tool_name": "search"},
        {"tool_name": "finish"},
    ]
    assert _stop_reply_preserved("", tools_used, STOP_TOOLS) is True


def test_empty_reply_not_preserved_without_stop_tool():
    """No stop tool anywhere in the batch -- empty reply should NOT be preserved."""
    tools_used = [{"tool_name": "search"}, {"tool_name": "read_file"}]
    assert _stop_reply_preserved("", tools_used, STOP_TOOLS) is False


def test_empty_reply_not_preserved_with_empty_tools_used():
    """tools_used is empty (e.g. the loop had no tool-call iterations)."""
    assert _stop_reply_preserved("", [], STOP_TOOLS) is False


def test_non_empty_final_content_not_preserved_even_with_stop_tool():
    """When final_content is not empty, the guard is irrelevant and should
    return False regardless of stop tools."""
    tools_used = [
        {"tool_name": "finish"},
        {"tool_name": "search"},
    ]
    assert _stop_reply_preserved("Actual reply text", tools_used, STOP_TOOLS) is False


def test_stop_tool_with_different_stop_set():
    """Stop-tool names are drawn from the caller-provided set, not hard-coded."""
    custom_stop = {"terminate", "abort"}
    tools_used = [
        {"tool_name": "search"},
        {"tool_name": "terminate"},
    ]
    assert _stop_reply_preserved("", tools_used, custom_stop) is True


def test_empty_stop_set_never_preserves():
    """An empty stop_tools set means no tool counts as a stop tool."""
    tools_used = [{"tool_name": "finish"}, {"tool_name": "search"}]
    assert _stop_reply_preserved("", tools_used, set()) is False


# ---------------------------------------------------------------------------
# End-to-end tests: drive the real ``_run_agent_loop`` with a fake provider
# and tool registry, exercising both guards on the actual code path.
# ---------------------------------------------------------------------------


class _FakeSubagentManager:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _ScriptedProvider(LLMProvider):
    """Return one scripted response, then fail loudly if called again."""

    def __init__(self, first_response: LLMResponse):
        super().__init__()
        self.calls = 0
        self._first_response = first_response

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return self._first_response
        return LLMResponse(content="UNEXPECTED SECOND MODEL CALL")

    def get_default_model(self) -> str:
        return "fake-model"


class _EchoToolRegistry:
    """Executes any tool by echoing its name; records execution order."""

    def __init__(self, tool_names):
        self._tool_names = list(tool_names)
        self.execute_calls = []

    def get_definitions(self, **kwargs):
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": name,
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for name in self._tool_names
        ]

    async def execute(self, tool_name, arguments, **kwargs):
        self.execute_calls.append(tool_name)
        return f"{tool_name} result"


def _make_loop(tmp_path, monkeypatch, provider, tool_names, max_iterations=3):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path / "workspace",
        config=Config(storage_workspace=str(tmp_path)),
        max_iterations=max_iterations,
    )
    loop.tools = _EchoToolRegistry(tool_names)
    return loop


async def test_e2e_stop_tool_first_in_batch_preserves_empty_reply(tmp_path, monkeypatch):
    """A stop tool that is NOT last in a multi-tool batch must still yield ``""``.

    Before the fix this returned the post-loop filler string: the first guard
    only looked at ``tools_used[-1]`` and the second guard replaced the empty
    reply unconditionally.
    """
    batch = ["done", "search", "read_file"]
    provider = _ScriptedProvider(
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(id=f"call-{i}", name=name, arguments={}, tokens=1)
                for i, name in enumerate(batch)
            ],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )
    )
    loop = _make_loop(tmp_path, monkeypatch, provider, batch)

    final_content, _reasoning, tools_used, _tokens, iteration = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "finish up"}],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="stop-batch"),
        publish_events=False,
        stop_tool_names=["done"],
    )

    assert final_content == ""  # preserved -- not replaced by a filler string
    assert provider.calls == 1  # the loop stopped right after the stop-tool batch
    assert iteration == 1
    assert [t["tool_name"] for t in tools_used] == batch


async def test_e2e_empty_reply_without_stop_tool_still_gets_filler(tmp_path, monkeypatch):
    """With stop tools configured but unused, the filler fallback must survive.

    Guards must only skip the filler when a stop tool actually ran; an empty
    plain-text reply with no tool usage keeps the pre-existing filler behavior.
    """
    provider = _ScriptedProvider(LLMResponse(content=""))
    loop = _make_loop(tmp_path, monkeypatch, provider, ["done"])

    final_content, _reasoning, tools_used, _tokens, _iteration = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "say nothing"}],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="no-stop"),
        publish_events=False,
        stop_tool_names=["done"],
    )

    assert tools_used == []
    assert final_content == "I've completed processing but have no response to give."
