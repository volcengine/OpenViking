"""Tests for SandboxManager guarding concurrent sandbox creation with a lock.

Two concurrent get_sandbox() calls for the same session_key must create only a
single backend instance; without the creation lock both callers pass the
"not in cache" check before either finishes and two backends leak.
"""

import asyncio
from types import SimpleNamespace

import pytest

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

    # No hang, and the lock coalesced each cycle into exactly one creation.
    assert create_count == 20


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


async def test_cancelled_start_stops_partial_backend(tmp_path):
    """Exercise the real _create_sandbox cancellation path: if the task is
    cancelled while instance.start() is pending, the partially-created
    backend must be stopped, CancelledError must propagate, nothing may be
    cached, and the creation lock must be released."""
    manager = _make_manager()
    manager.workspace = tmp_path
    events = []

    class _FakeBackend:
        def __init__(self, sandbox_config, workspace_id, workspace):
            self.workspace_id = workspace_id

        async def start(self):
            events.append("start")
            await asyncio.Event().wait()  # block until cancelled

        async def stop(self):
            events.append("stop")

    manager._backend_cls = _FakeBackend

    task = asyncio.create_task(manager.get_sandbox(None))
    for _ in range(100):
        if "start" in events:
            break
        await asyncio.sleep(0.01)
    assert "start" in events

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert events == ["start", "stop"]
    assert manager._sandboxes == {}
    assert not manager._creation_lock.locked()


async def test_concurrent_cleanup_session_stops_once():
    """Two concurrent cleanup_session calls for the same key must stop the
    backend exactly once and must not raise KeyError: the entry is popped
    from the cache before the (awaitable) stop() call."""
    manager = _make_manager()
    stop_count = 0

    async def _stop():
        nonlocal stop_count
        stop_count += 1
        await asyncio.sleep(0.02)  # keep the stop pending so the calls interleave

    manager._sandboxes["shared"] = SimpleNamespace(stop=_stop)

    await asyncio.gather(
        manager.cleanup_session(None),
        manager.cleanup_session(None),
    )

    assert stop_count == 1
    assert manager._sandboxes == {}


async def test_cleanup_all_with_creation_interleaved():
    """cleanup_all pops entries one at a time, so a creation that lands while
    a stop() is awaited is also cleaned up instead of crashing iteration."""
    manager = _make_manager()
    stopped = []

    def _backend(name):
        async def _stop():
            stopped.append(name)
            await asyncio.sleep(0.01)
        return SimpleNamespace(stop=_stop)

    manager._sandboxes["a"] = _backend("a")
    manager._sandboxes["b"] = _backend("b")

    async def _add_during_cleanup():
        await asyncio.sleep(0.005)  # land while the first stop() is pending
        manager._sandboxes["c"] = _backend("c")

    await asyncio.gather(manager.cleanup_all(), _add_during_cleanup())

    assert sorted(stopped) == ["a", "b", "c"]
    assert manager._sandboxes == {}
