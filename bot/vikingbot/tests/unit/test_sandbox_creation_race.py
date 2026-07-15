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


async def test_concurrent_create_and_cleanup_does_not_deadlock():
    """Concurrent get_sandbox and cleanup_session for the same key must not
    deadlock.  cleanup_session never acquires _creation_lock, so both paths
    should complete even when interleaved aggressively."""
    manager = _make_manager()
    create_count = 0
    stop_count = 0

    async def _create(workspace_id):
        nonlocal create_count
        create_count += 1
        await asyncio.sleep(0.02)
        backend = SimpleNamespace(workspace_id=workspace_id)
        backend.stop = _make_stop()
        return backend

    def _make_stop():
        async def _stop():
            nonlocal stop_count
            stop_count += 1
        return _stop

    manager._create_sandbox = _create

    # Interleave 20 create + cleanup cycles.  If _creation_lock were
    # acquired by cleanup_session this would hang.
    for i in range(20):
        manager._sandboxes.clear()

        await asyncio.gather(
            manager.get_sandbox(None),
            manager.get_sandbox(None),
            manager.cleanup_session(None),
            manager.cleanup_session(None),
        )

    # Sanity: no exception means no deadlock
    assert True


async def test_lock_released_after_create_is_cancelled():
    """If _create_sandbox is cancelled while holding the lock, the lock must
    be released so subsequent callers can proceed."""
    manager = _make_manager()
    attempt = 0

    async def _cancellable_create(workspace_id):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            await asyncio.sleep(0.1)  # let the test cancel us
            raise asyncio.CancelledError("simulated cancel")
        return SimpleNamespace(workspace_id=workspace_id)

    manager._create_sandbox = _cancellable_create

    async def _first_creator():
        try:
            await manager._get_or_create_sandbox(None)
        except asyncio.CancelledError:
            pass

    canceller = asyncio.create_task(_first_creator())
    await asyncio.sleep(0.02)  # let it enter the lock
    canceller.cancel()
    await canceller  # wait for cancellation to be processed

    # Now a second caller should succeed -- the lock was released.
    result = await manager.get_sandbox(None)
    assert result is not None
    assert attempt >= 2  # second attempt succeeded
    assert manager._sandboxes == {"shared": result}
