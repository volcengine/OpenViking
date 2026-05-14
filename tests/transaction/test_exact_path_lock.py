# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ExactPathLock semantics."""

import pytest

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
