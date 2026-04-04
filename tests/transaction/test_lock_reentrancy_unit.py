# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import os

import pytest

from openviking.storage.transaction.lock_context import LockContext
from openviking.storage.transaction.lock_manager import LockManager
from openviking.storage.transaction.path_lock import LOCK_FILE_NAME, PathLock


class _FakeAGFS:
    def __init__(self):
        self._dirs = {"/"}
        self._files = {}

    def stat(self, path: str):
        if path in self._dirs:
            return {"isDir": True}
        if path in self._files:
            return {"isDir": False}
        raise FileNotFoundError(path)

    def mkdir(self, path: str):
        current = ""
        for part in path.split("/"):
            if not part:
                current = "/"
                continue
            current = os.path.join(current, part) if current != "/" else f"/{part}"
            self._dirs.add(current)

    def write(self, path: str, data: bytes):
        parent = os.path.dirname(path) or "/"
        self.mkdir(parent)
        self._files[path] = data

    def read(self, path: str):
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]

    def rm(self, path: str, recursive: bool = False):
        if path in self._files:
            self._files.pop(path, None)
            return
        if path not in self._dirs:
            raise FileNotFoundError(path)
        prefix = path.rstrip("/") + "/"
        has_children = any(
            child.startswith(prefix)
            for child in self._dirs | set(self._files.keys())
            if child != path
        )
        if has_children and not recursive:
            raise OSError("directory not empty")
        for child in list(self._files.keys()):
            if child.startswith(prefix):
                self._files.pop(child, None)
        for child in sorted(self._dirs, reverse=True):
            if child == path or child.startswith(prefix):
                self._dirs.discard(child)

    def ls(self, path: str):
        if path not in self._dirs:
            raise FileNotFoundError(path)
        prefix = path.rstrip("/") + "/"
        children = {}
        for child in self._dirs:
            if not child.startswith(prefix) or child == path:
                continue
            rest = child[len(prefix) :]
            if "/" in rest or not rest:
                continue
            children[rest] = {"name": rest, "isDir": True}
        for child in self._files:
            if not child.startswith(prefix):
                continue
            rest = child[len(prefix) :]
            if "/" in rest or not rest:
                continue
            children[rest] = {"name": rest, "isDir": False}
        return list(children.values())


@pytest.mark.asyncio
async def test_path_lock_reuses_same_owner_subtree_without_overwriting_token():
    agfs = _FakeAGFS()
    agfs.mkdir("/root")
    lock = PathLock(agfs)
    handle = LockManager(agfs).create_handle()

    assert await lock.acquire_subtree("/root", handle, timeout=0.1) is True
    lock_path = f"/root/{LOCK_FILE_NAME}"
    before = agfs.read(lock_path).decode("utf-8")
    assert before.endswith(":S")

    assert await lock.acquire_point("/root", handle, timeout=0.1) is True
    after = agfs.read(lock_path).decode("utf-8")
    assert after == before
    assert after.endswith(":S")


@pytest.mark.asyncio
async def test_path_lock_reuses_ancestor_subtree_without_creating_child_lock():
    agfs = _FakeAGFS()
    agfs.mkdir("/root")
    agfs.mkdir("/root/child")
    lock = PathLock(agfs)
    handle = LockManager(agfs).create_handle()

    assert await lock.acquire_subtree("/root", handle, timeout=0.1) is True
    assert await lock.acquire_point("/root/child", handle, timeout=0.1) is True

    with pytest.raises(FileNotFoundError):
        agfs.read(f"/root/child/{LOCK_FILE_NAME}")


@pytest.mark.asyncio
async def test_lock_context_with_external_handle_keeps_outer_subtree_lock():
    agfs = _FakeAGFS()
    agfs.mkdir("/root")
    lm = LockManager(agfs=agfs, lock_timeout=0.1, lock_expire=60.0)
    lock_path = f"/root/{LOCK_FILE_NAME}"

    async with LockContext(lm, ["/root"], lock_mode="subtree") as handle:
        before = agfs.read(lock_path).decode("utf-8")
        assert before.endswith(":S")

        async with LockContext(lm, ["/root"], lock_mode="point", handle=handle):
            current = agfs.read(lock_path).decode("utf-8")
            assert current == before

        still_owned = agfs.read(lock_path).decode("utf-8")
        assert still_owned == before

    with pytest.raises(FileNotFoundError):
        agfs.read(lock_path)
