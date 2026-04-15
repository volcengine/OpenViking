# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for LockManager staleness tracking."""

from unittest.mock import AsyncMock, MagicMock

from openviking.storage.transaction.lock_manager import LockManager
from openviking.storage.transaction.path_lock import LockRefreshResult


async def test_refresh_lock_updates_last_active_without_changing_created_at(monkeypatch):
    lm = LockManager(agfs=MagicMock(), lock_expire=300.0)
    handle = lm.create_handle()
    handle.created_at = 100.0
    handle.last_active_at = 100.0
    handle.add_lock("/local/tx/.path.ovlock")
    lm._path_lock.collect_lost_owner_locks = lambda owner: []
    lm._path_lock.refresh = AsyncMock(
        return_value=LockRefreshResult(refreshed_paths=["/local/tx/.path.ovlock"])
    )

    monkeypatch.setattr("openviking.storage.transaction.lock_manager.time.time", lambda: 250.0)

    await lm.refresh_lock(handle)

    lm._path_lock.refresh.assert_awaited_once_with(handle)
    assert handle.created_at == 100.0
    assert handle.last_active_at == 250.0


async def test_stale_cleanup_releases_only_inactive_handles(monkeypatch):
    lm = LockManager(agfs=MagicMock(), lock_expire=300.0)

    active_handle = lm.create_handle()
    active_handle.created_at = 0.0
    active_handle.last_active_at = 200.0
    active_handle.add_lock("/local/active/.path.ovlock")

    stale_handle = lm.create_handle()
    stale_handle.created_at = 0.0
    stale_handle.last_active_at = 0.0
    stale_handle.add_lock("/local/stale/.path.ovlock")

    unlocked_handle = lm.create_handle()
    unlocked_handle.created_at = 0.0
    unlocked_handle.last_active_at = 0.0

    released = []

    async def fake_sleep(_seconds):
        lm._running = False

    async def fake_release(handle):
        released.append(handle.id)
        lm._handles.pop(handle.id, None)

    monkeypatch.setattr("openviking.storage.transaction.lock_manager.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("openviking.storage.transaction.lock_manager.time.time", lambda: 400.0)
    monkeypatch.setattr(lm, "release", fake_release)
    lm._path_lock.collect_lost_owner_locks = lambda owner: []

    lm._running = True
    await lm._stale_cleanup_loop()

    assert released == [stale_handle.id]
    assert active_handle.id in lm.get_active_handles()
    assert unlocked_handle.id not in lm.get_active_handles()


def test_get_handle_returns_none_when_lock_ownership_is_lost():
    lm = LockManager(agfs=MagicMock(), lock_expire=300.0)
    handle = lm.create_handle()
    handle.add_lock("/local/stale/.path.ovlock")
    lm._path_lock.collect_lost_owner_locks = lambda owner: list(owner.locks)

    assert lm.get_handle(handle.id) is None
    assert handle.id not in lm.get_active_handles()
