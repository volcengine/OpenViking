# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for lock observer activity tracking."""

from unittest.mock import MagicMock

from openviking.storage.observers.lock_observer import LockObserver
from openviking.storage.transaction.lock_handle import LockHandle


def _make_handle(handle_id: str, created_at: float, last_active_at: float) -> LockHandle:
    handle = LockHandle(id=handle_id)
    handle.created_at = created_at
    handle.last_active_at = last_active_at
    handle.add_lock(f"/local/{handle_id}/.path.ovlock")
    return handle


def test_get_hanging_locks_uses_idle_time(monkeypatch):
    active_handle = _make_handle("active", created_at=0.0, last_active_at=900.0)
    stale_handle = _make_handle("stale", created_at=0.0, last_active_at=100.0)

    manager = MagicMock()
    manager.get_active_handles.return_value = {
        active_handle.id: active_handle,
        stale_handle.id: stale_handle,
    }

    monkeypatch.setattr("openviking.storage.observers.lock_observer.time.time", lambda: 1000.0)

    observer = LockObserver(manager)
    active_locks = observer.get_active_locks()
    active_info = next(lock for lock in active_locks if lock["id"] == active_handle.id)

    assert active_info["duration_seconds"] == 1000.0
    assert active_info["idle_seconds"] == 100.0
    assert [lock["id"] for lock in observer.get_hanging_locks(600)] == [stale_handle.id]


def test_health_checks_ignore_long_running_but_active_lock(monkeypatch):
    active_handle = _make_handle("active", created_at=0.0, last_active_at=950.0)

    manager = MagicMock()
    manager.get_active_handles.return_value = {active_handle.id: active_handle}

    monkeypatch.setattr("openviking.storage.observers.lock_observer.time.time", lambda: 1000.0)

    observer = LockObserver(manager)

    assert observer.is_healthy() is True
    assert observer.has_errors() is False
