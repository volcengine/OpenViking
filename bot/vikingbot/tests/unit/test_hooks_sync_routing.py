"""Tests for hook is_sync routing and sync-path return threading.

OpenVikingPostCallHook.execute() is async but MUST return its mutated kwargs so
result transformations reach tool.post_call. That only happens when the hook is
routed through the sync path (is_sync=True); the async path discards returns.
"""

from vikingbot.hooks.base import Hook, HookContext
from vikingbot.hooks.builtins.openviking_hooks import OpenVikingPostCallHook
from vikingbot.hooks.manager import HookManager


def test_openviking_post_call_hook_is_sync():
    assert OpenVikingPostCallHook.is_sync is True


class _SyncEchoHook(Hook):
    name = "sync_echo"
    is_sync = True

    async def execute(self, context: HookContext, **kwargs):
        return {**kwargs, "injected_by": "sync"}


class _AsyncEchoHook(Hook):
    name = "async_echo"
    is_sync = False

    async def execute(self, context: HookContext, **kwargs):
        return {**kwargs, "injected_by": "async"}


async def test_sync_hook_return_is_threaded_back():
    manager = HookManager()
    manager._hooks["tool.post_call"].append(_SyncEchoHook())

    result = await manager.execute_hooks(HookContext(event_type="tool.post_call"), value=1)

    assert result["injected_by"] == "sync"
    assert result["value"] == 1


async def test_async_hook_return_is_discarded():
    manager = HookManager()
    manager._hooks["tool.post_call"].append(_AsyncEchoHook())

    result = await manager.execute_hooks(HookContext(event_type="tool.post_call"), value=1)

    # Async path routes through asyncio.gather and does not thread returns back.
    assert "injected_by" not in result
    assert result == {"value": 1}


class _StubbedSearchPostCallHook(OpenVikingPostCallHook):
    """OpenVikingPostCallHook with the network-backed experience search stubbed."""

    def __init__(self):
        self.search_queries = []

    async def _search_skill_experiences(
        self, workspace_id, query, config=None, openviking_connection=None
    ):
        self.search_queries.append(query)
        return "remembered experience"


def _post_call_context() -> HookContext:
    return HookContext(event_type="tool.post_call", workspace_id="ws-test")


async def test_post_call_passes_exception_result_through():
    """Tool failures arrive as Exception results; the hook must not touch them.

    On the sync path a TypeError from re.search would escalate into a
    tool-call failure, so the str guard is load-bearing here.
    """
    hook = _StubbedSearchPostCallHook()
    error = RuntimeError("tool blew up")

    out = await hook.execute(_post_call_context(), tool_name="read_file", params={}, result=error)

    assert out == {"tool_name": "read_file", "params": {}, "result": error}
    assert hook.search_queries == []


async def test_post_call_passes_non_string_result_through():
    hook = _StubbedSearchPostCallHook()
    payload = {"content": "not a string"}

    out = await hook.execute(_post_call_context(), tool_name="read_file", params={}, result=payload)

    assert out["result"] is payload
    assert hook.search_queries == []


async def test_post_call_ignores_other_tools():
    hook = _StubbedSearchPostCallHook()
    skill_md = "---\nname: web_search\n---\n"

    out = await hook.execute(
        _post_call_context(), tool_name="exec_shell", params={}, result=skill_md
    )

    assert out["result"] == skill_md
    assert hook.search_queries == []


async def test_post_call_appends_experiences_for_skill_markdown():
    hook = _StubbedSearchPostCallHook()
    skill_md = "---\nname: web_search\ndescription: Search the web for facts\n---\nUsage notes."

    out = await hook.execute(
        _post_call_context(), tool_name="read_file", params={}, result=skill_md
    )

    assert hook.search_queries == ["Search the web for facts"]
    assert out["result"].startswith(skill_md)
    assert "## Related Experiences" in out["result"]
    assert "remembered experience" in out["result"]


async def test_post_call_skips_experience_loader_skill():
    hook = _StubbedSearchPostCallHook()
    skill_md = "---\nname: experience_loader\ndescription: loads experiences\n---\n"

    out = await hook.execute(
        _post_call_context(), tool_name="read_file", params={}, result=skill_md
    )

    assert out["result"] == skill_md
    assert hook.search_queries == []
