# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for cross-loop teardown of OwnedLockLease (issue #2515).

The lease's refresh task is bound to the event loop that constructed the lease.
When ``close()``/``handoff()`` runs from a *different* loop (e.g. an embedding
completion callback that falls back to the current loop), tearing the task down
must not ``await`` a future attached to another loop, which would raise
``RuntimeError: ... got Future ... attached to a different loop``.
"""

import asyncio
import threading
from contextlib import suppress
from unittest.mock import MagicMock

from openviking.storage.transaction.lock_lease import OwnedLockLease
from openviking.storage.transaction.lock_manager import LockManager


class _LoopThread:
    """Run an asyncio loop in a dedicated thread (acts as 'loop A')."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self) -> None:
        self._thread.start()

    def run_coro(self, coro):
        """Schedule a coroutine on loop A and block (from caller thread) for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=5)

    def stop(self, *, close: bool = True) -> None:
        # Cancel and drain any task still pending on loop A (e.g. a sleeping
        # refresh loop) before closing it, so it is not later reported as
        # "Task was destroyed but it is pending".
        if self.loop.is_running():

            async def _shutdown() -> None:
                pending = [
                    t for t in asyncio.all_tasks(self.loop) if t is not asyncio.current_task()
                ]
                for t in pending:
                    t.cancel()
                for t in pending:
                    with suppress(asyncio.CancelledError):
                        await t

            with suppress(RuntimeError):
                asyncio.run_coroutine_threadsafe(_shutdown(), self.loop).result(timeout=5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        if close and not self.loop.is_closed():
            self.loop.close()


def _make_lease_on(loop_thread: _LoopThread) -> OwnedLockLease:
    """Build an OwnedLockLease whose _refresh_task is bound to loop A.

    A large ``lock_expire`` keeps the refresh loop sleeping (interval ≈ expire/2)
    so it never performs real I/O against the mock agfs before we cancel it.
    """
    manager = LockManager(agfs=MagicMock(), lock_expire=10_000.0)
    handle = manager.create_handle()
    handle.add_lock("/local/default/x/.path.ovlock")  # non-empty locks -> __init__ starts refresh

    async def _build() -> OwnedLockLease:
        # Constructed inside loop A -> _refresh_task binds to loop A.
        return OwnedLockLease(manager, handle)

    return loop_thread.run_coro(_build())


async def test_close_from_other_loop_does_not_raise_cross_loop_error():
    """close() from loop B must tear down a loop-A-bound refresh task cleanly."""
    loop_a = _LoopThread()
    loop_a.start()
    try:
        lease = _make_lease_on(loop_a)
        # Sanity: the refresh task really is bound to loop A, not the test loop.
        assert lease._refresh_task is not None
        assert lease._refresh_task.get_loop() is loop_a.loop
        assert lease._refresh_task.get_loop() is not asyncio.get_running_loop()

        # Regression assertion: must NOT raise cross-loop RuntimeError.
        await lease.close()

        assert lease._refresh_task is None
        # Give loop A a tick to process the scheduled cancel.
        await asyncio.sleep(0.05)
    finally:
        loop_a.stop()


async def test_handoff_from_other_loop_does_not_raise_cross_loop_error():
    """handoff() shares _stop_refresh() with close(); same cross-loop guarantee."""
    loop_a = _LoopThread()
    loop_a.start()
    try:
        lease = _make_lease_on(loop_a)
        await lease.handoff()  # must not raise
        assert lease._refresh_task is None
    finally:
        loop_a.stop()


async def test_close_when_owner_loop_already_closed():
    """If loop A is gone before close(), teardown still succeeds without raising
    'Event loop is closed' from call_soon_threadsafe."""
    loop_a = _LoopThread()
    loop_a.start()
    lease = _make_lease_on(loop_a)
    loop_a.stop(close=True)  # owner loop closed -> task_loop.is_closed() is True
    await lease.close()  # must hit the 'else: drop it' branch cleanly
    assert lease._refresh_task is None


async def test_close_same_loop_still_awaits_and_cancels():
    """Same-loop close() keeps original behavior: task is cancelled and awaited."""
    manager = LockManager(agfs=MagicMock(), lock_expire=10_000.0)
    handle = manager.create_handle()
    handle.add_lock("/local/default/y/.path.ovlock")
    lease = OwnedLockLease(manager, handle)  # task bound to the test loop (loop B)
    task = lease._refresh_task
    assert task is not None
    assert task.get_loop() is asyncio.get_running_loop()

    await lease.close()

    assert lease._refresh_task is None
    assert task.cancelled() or task.done()
