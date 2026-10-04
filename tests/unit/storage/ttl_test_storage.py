# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Minimal raw storage for snapshot lifecycle tests."""

from tests.unit.storage.test_ttl_registry import _MemoryAGFS


class MemoryAGFS(_MemoryAGFS):
    async def pathlock_acquire_exact(self, path, **kwargs):
        return await super().pathlock_acquire_exact(path)

    pathlock_acquire_tree = pathlock_acquire_exact

    async def ensure_parent_dirs(self, path, **kwargs):
        await super().ensure_parent_dirs(path)

    async def read(self, path, **kwargs):
        return await super().read(path)

    async def stat(self, path, **kwargs):
        self.stat_calls.append(path)
        if path in self.files:
            return {"isDir": False, "size": len(self.files[path])}
        if any(key.startswith(path.rstrip("/") + "/") for key in self.files):
            return {"isDir": True}
        raise FileNotFoundError(path)
