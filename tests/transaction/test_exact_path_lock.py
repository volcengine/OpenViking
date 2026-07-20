# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ExactPathLock semantics."""

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from openviking.storage.transaction import path_lock as path_lock_module
from openviking.storage.transaction.lock_handle import LockHandle
from openviking.storage.transaction.lock_manager import LockManager
from openviking.storage.transaction.path_lock import EXACT_LOCK_FILE_PREFIX, PathLockEngine


class _MemoryAgfs:
    def __init__(self):
        self.dirs = {"/"}
        self.files: dict[str, bytes] = {}

    def _parent(self, path: str) -> str:
        path = path.rstrip("/")
        if "/" not in path:
            return "/"
        parent = path.rsplit("/", 1)[0]
        return parent or "/"

    def stat(self, path: str):
        path = path.rstrip("/") or "/"
        if path in self.dirs:
            return {"name": path.rsplit("/", 1)[-1], "isDir": True}
        if path in self.files:
            return {"name": path.rsplit("/", 1)[-1], "isDir": False}
        raise FileNotFoundError(path)

    def mkdir(self, path: str):
        path = path.rstrip("/") or "/"
        parent = self._parent(path)
        if parent not in self.dirs:
            raise FileNotFoundError(parent)
        self.dirs.add(path)
        return {"message": "created"}

    def read(self, path: str):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write(self, path: str, data: bytes):
        parent = self._parent(path)
        if parent not in self.dirs:
            raise FileNotFoundError(parent)
        self.files[path] = data
        return path

    def rm(self, path: str, recursive: bool = False):
        path = path.rstrip("/") or "/"
        self.files.pop(path, None)
        if path in self.dirs:
            children = [
                item
                for item in [*self.dirs, *self.files]
                if item != path and item.startswith(path.rstrip("/") + "/")
            ]
            if children and not recursive:
                raise RuntimeError("directory not empty")
            for child in children:
                self.files.pop(child, None)
                self.dirs.discard(child)
            self.dirs.discard(path)
        return {"message": "deleted"}

    def ls(self, path: str):
        path = path.rstrip("/") or "/"
        prefix = path.rstrip("/") + "/"
        names: dict[str, bool] = {}
        for item in self.dirs:
            if item == path or not item.startswith(prefix):
                continue
            rest = item[len(prefix) :]
            if "/" not in rest:
                names[rest] = True
        for item in self.files:
            if not item.startswith(prefix):
                continue
            rest = item[len(prefix) :]
            if "/" not in rest:
                names[rest] = False
        return [{"name": name, "isDir": is_dir} for name, is_dir in names.items()]


class _ConcurrentParentCreationAgfs(_MemoryAgfs):
    def __init__(self, shared_parent: str):
        super().__init__()
        self.shared_parent = shared_parent
        self._initial_stat_barrier = threading.Barrier(2)
        self._state_lock = threading.Lock()
        self._missing_stat_count = 0
        self.mkdir_attempts = 0

    def stat(self, path: str):
        path = path.rstrip("/") or "/"
        should_report_missing = False
        if path == self.shared_parent:
            with self._state_lock:
                if self._missing_stat_count < 2:
                    self._missing_stat_count += 1
                    should_report_missing = True
            if should_report_missing:
                self._initial_stat_barrier.wait(timeout=5)
                raise FileNotFoundError(path)
        return super().stat(path)

    def mkdir(self, path: str):
        path = path.rstrip("/") or "/"
        if path != self.shared_parent:
            return super().mkdir(path)
        with self._state_lock:
            self.mkdir_attempts += 1
            if path in self.dirs:
                raise FileExistsError(path)
            parent = self._parent(path)
            if parent not in self.dirs:
                raise FileNotFoundError(parent)
            self.dirs.add(path)
        return {"message": "created"}


def _agfs_with_docs_dir() -> _MemoryAgfs:
    agfs = _MemoryAgfs()
    agfs.mkdir("/local")
    agfs.mkdir("/local/default")
    agfs.mkdir("/local/default/resources")
    agfs.mkdir("/local/default/resources/docs")
    return agfs


@pytest.mark.asyncio
async def test_exact_path_lock_allows_sibling_paths():
    agfs = _agfs_with_docs_dir()
    lock = PathLockEngine(agfs)
    first = LockHandle(id="exact-a")
    second = LockHandle(id="exact-b")

    assert await lock.acquire_exact_path("/local/default/resources/docs/a.md", first)
    assert await lock.acquire_exact_path("/local/default/resources/docs/b.md", second)

    await lock.release(first)
    await lock.release(second)


@pytest.mark.asyncio
async def test_exact_path_locks_tolerate_concurrent_parent_creation():
    shared_parent = "/local/default/resources/shared"
    agfs = _ConcurrentParentCreationAgfs(shared_parent)
    agfs.mkdir("/local")
    agfs.mkdir("/local/default")
    agfs.mkdir("/local/default/resources")
    lock = PathLockEngine(agfs)
    first = LockHandle(id="exact-a")
    second = LockHandle(id="exact-b")

    acquired = await asyncio.gather(
        lock.acquire_exact_path(f"{shared_parent}/a.md", first),
        lock.acquire_exact_path(f"{shared_parent}/b.md", second),
    )

    assert acquired == [True, True]
    assert agfs.mkdir_attempts == 2
    exact_lock_paths = [
        path
        for path in agfs.files
        if path.startswith(f"{shared_parent}/")
        and path.rsplit("/", 1)[-1].startswith(EXACT_LOCK_FILE_PREFIX)
    ]
    assert len(exact_lock_paths) == 2

    await lock.release(first)
    await lock.release(second)


@pytest.mark.asyncio
async def test_exact_path_lock_blocks_same_path_without_creating_target():
    agfs = _agfs_with_docs_dir()
    lock = PathLockEngine(agfs)
    first = LockHandle(id="exact-a")
    second = LockHandle(id="exact-a-blocked")
    target = "/local/default/resources/docs/a.md"

    assert await lock.acquire_exact_path(target, first)
    assert not await lock.acquire_exact_path(target, second, timeout=0.0)

    assert lock.is_locked(target)
    with pytest.raises(FileNotFoundError):
        agfs.stat(target)
    assert any(path.rsplit("/", 1)[-1].startswith(EXACT_LOCK_FILE_PREFIX) for path in agfs.files)

    await lock.release(first)
    assert not lock.is_locked(target)


@pytest.mark.asyncio
async def test_exact_path_lock_backs_off_to_configured_cap(monkeypatch):
    agfs = _agfs_with_docs_dir()
    lock = PathLockEngine(agfs, poll_interval=0.1, poll_max_interval=0.25)
    first = LockHandle(id="exact-first")
    second = LockHandle(id="exact-second")
    target = "/local/default/resources/docs/backoff.md"

    assert await lock.acquire_exact_path(target, first)
    lock_path = first.locks[0]
    sleep_intervals: list[float] = []

    async def fake_sleep(interval: float):
        sleep_intervals.append(interval)
        if len(sleep_intervals) == 4:
            agfs.rm(lock_path)

    monkeypatch.setattr(path_lock_module.asyncio, "sleep", fake_sleep)

    assert await lock.acquire_exact_path(target, second, timeout=10.0)
    assert sleep_intervals == [0.1, 0.2, 0.25, 0.25]

    await lock.release(second)


@pytest.mark.asyncio
async def test_exact_path_lock_backoff_does_not_sleep_past_deadline(monkeypatch):
    agfs = _agfs_with_docs_dir()
    lock = PathLockEngine(agfs, poll_interval=0.2, poll_max_interval=1.0)
    first = LockHandle(id="deadline-first")
    second = LockHandle(id="deadline-second")
    target = "/local/default/resources/docs/deadline.md"

    assert await lock.acquire_exact_path(target, first)

    class _FakeLoop:
        now = 100.0

        def time(self):
            return self.now

    fake_loop = _FakeLoop()
    sleep_intervals: list[float] = []

    async def fake_sleep(interval: float):
        sleep_intervals.append(interval)
        fake_loop.now += interval

    monkeypatch.setattr(path_lock_module.asyncio, "get_running_loop", lambda: fake_loop)
    monkeypatch.setattr(path_lock_module.asyncio, "sleep", fake_sleep)

    assert not await lock.acquire_exact_path(target, second, timeout=0.15)
    assert sleep_intervals == [pytest.approx(0.15)]


@pytest.mark.asyncio
async def test_wait_progress_log_is_rate_limited_per_path(monkeypatch):
    path_lock_module._last_wait_progress_at.clear()
    info = MagicMock()
    monkeypatch.setattr(path_lock_module.logger, "info", info)

    path_lock_module._log_wait_progress("exact:/same", "first waiter")
    path_lock_module._log_wait_progress("exact:/same", "second waiter")
    path_lock_module._log_wait_progress("exact:/other", "other path")

    assert info.call_count == 2


@pytest.mark.asyncio
async def test_exact_path_lock_conflicts_with_tree_lock():
    agfs = _agfs_with_docs_dir()
    lock = PathLockEngine(agfs)
    exact = LockHandle(id="exact-a")
    tree = LockHandle(id="tree-docs")
    target = "/local/default/resources/docs/a.md"
    docs = "/local/default/resources/docs"

    assert await lock.acquire_exact_path(target, exact)
    assert not await lock.acquire_tree(docs, tree, timeout=0.0)

    await lock.release(exact)
    assert await lock.acquire_tree(docs, tree, timeout=0.0)

    blocked = LockHandle(id="exact-blocked-by-tree")
    assert not await lock.acquire_exact_path(target, blocked, timeout=0.0)

    await lock.release(tree)


@pytest.mark.asyncio
async def test_exact_tree_batch_acquires_exact_and_tree_locks():
    agfs = _agfs_with_docs_dir()
    agfs.mkdir("/local/default/resources/docs/events")
    manager = LockManager(agfs=agfs, lock_timeout=0.0, lock_expire=300.0)
    handle = manager.create_handle()

    assert await manager.acquire_exact_tree_batch(
        handle,
        exact_paths=["/local/default/resources/docs/profile.md"],
        tree_paths=["/local/default/resources/docs/events"],
    )
    assert len(handle.locks) == 2

    blocked = manager.create_handle()
    assert not await manager.acquire_exact_tree_batch(
        blocked,
        exact_paths=["/local/default/resources/docs/profile.md"],
        tree_paths=[],
    )

    await manager.release(handle)
    await manager.release(blocked)
