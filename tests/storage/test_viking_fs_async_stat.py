# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

import openviking.storage.transaction as transaction_module
from openviking.storage.viking_fs import VikingFS


class _StatAGFS:
    def stat(self, path):
        return {"name": path.rsplit("/", 1)[-1], "isDir": False}


class _AsyncOnlyLockManager:
    def __init__(self):
        self.paths = []

    async def is_path_locked_async(self, path, ignore_stale=True):
        self.paths.append((path, ignore_stale))
        return True

    def is_path_locked(self, path, ignore_stale=True):
        raise AssertionError("stat() should use the async lock lookup")


@pytest.mark.asyncio
async def test_stat_uses_async_lock_lookup(monkeypatch):
    lock_manager = _AsyncOnlyLockManager()
    monkeypatch.setattr(transaction_module, "get_lock_manager", lambda: lock_manager)

    fs = VikingFS(agfs=_StatAGFS())
    result = await fs.stat("viking://resources/file.txt")

    assert result["isLocked"] is True
    assert lock_manager.paths == [("/local/default/resources/file.txt", True)]
