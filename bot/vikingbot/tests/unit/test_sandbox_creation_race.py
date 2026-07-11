"""Tests for SandboxManager guarding concurrent sandbox creation with a lock.

Two concurrent get_sandbox() calls for the same session_key must create only a
single backend instance; without the creation lock both callers pass the
"not in cache" check before either finishes and two backends leak.
"""

import asyncio
from types import SimpleNamespace

from vikingbot.sandbox.manager import SandboxManager


def _make_manager():
    manager = SandboxManager.__new__(SandboxManager)
    manager._sandboxes = {}
    manager._creation_lock = asyncio.Lock()
    # mode="shared" -> to_workspace_id() ignores the session_key and returns "shared".
    manager.config = SimpleNamespace(sandbox=SimpleNamespace(mode="shared"))
    return manager


async def test_concurrent_get_sandbox_creates_backend_once():
    manager = _make_manager()
    call_count = 0

    async def _slow_create(workspace_id):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)  # let the second caller reach the check
        return SimpleNamespace(workspace_id=workspace_id)

    manager._create_sandbox = _slow_create

    first, second = await asyncio.gather(
        manager.get_sandbox(None),
        manager.get_sandbox(None),
    )

    assert call_count == 1
    assert first is second
    assert manager._sandboxes == {"shared": first}
