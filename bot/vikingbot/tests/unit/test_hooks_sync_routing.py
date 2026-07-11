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
