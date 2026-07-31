# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


class _FakePathLock:
    """Mock for _async_agfs pathlock operations."""

    def __init__(self):
        self.release_calls: list[str] = []

    async def pathlock_release(self, lease):
        self.release_calls.append(lease["id"])


class _FakeVikingFS:
    def __init__(self, pathlock=None):
        self._async_agfs = pathlock or _FakePathLock()

    async def exists(self, uri, ctx=None):
        del uri, ctx
        return False

    async def ls(self, uri, node_limit=None, ctx=None):
        del uri, node_limit, ctx
        return []

    def _uri_to_path(self, uri, ctx=None):
        del ctx
        return f"/fake/{uri.replace('://', '/').strip('/')}"


@pytest.mark.asyncio
async def test_memory_semantic_directory_does_not_release_borrowed_lock(monkeypatch):
    processor = SemanticProcessor()
    pathlock = _FakePathLock()
    borrowed_lease = {"id": "borrowed-lock", "owned": False}

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(pathlock),
    )

    await processor._process_memory_directory(
        SemanticMsg(
            uri="viking://memory/demo",
            context_type="memory",
            recursive=False,
        ),
        lock=borrowed_lease,
    )

    assert pathlock.release_calls == []
