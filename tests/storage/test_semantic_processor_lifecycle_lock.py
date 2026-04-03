# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest

from openviking.storage.queuefs.semantic_dag import DagStats
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


class _FakeHandle:
    def __init__(self, handle_id: str):
        self.id = handle_id


class _FakeLockManager:
    def __init__(self):
        self._handles = {"lock-1": _FakeHandle("lock-1")}
        self.release_calls = []

    def get_handle(self, handle_id: str):
        return self._handles.get(handle_id)

    async def release(self, handle):
        self.release_calls.append(handle.id)
        self._handles.pop(handle.id, None)

    def create_handle(self):
        handle = _FakeHandle("new-lock")
        self._handles[handle.id] = handle
        return handle

    async def acquire_subtree(self, handle, lock_path):
        del handle, lock_path
        return True


class _FakeVikingFS:
    async def exists(self, uri, ctx=None):
        del uri, ctx
        return False

    def _uri_to_path(self, uri, ctx=None):
        del ctx
        return f"/fake/{uri.replace('://', '/').strip('/')}"


@pytest.mark.asyncio
async def test_semantic_processor_does_not_release_lock_owned_by_dag(monkeypatch):
    processor = SemanticProcessor()
    lock_manager = _FakeLockManager()

    class _FakeDagExecutor:
        def __init__(self, **kwargs):
            self.lifecycle_lock_handle_id = kwargs.get("lifecycle_lock_handle_id", "")

        async def run(self, root_uri):
            assert root_uri == "viking://resources/demo"
            assert self.lifecycle_lock_handle_id == "lock-1"

        def get_stats(self):
            return DagStats()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        lambda **kwargs: _FakeDagExecutor(**kwargs),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: lock_manager,
    )

    await processor.on_dequeue(
        SemanticMsg(
            uri="viking://resources/demo",
            context_type="resource",
            recursive=False,
            lifecycle_lock_handle_id="lock-1",
        ).to_dict()
    )

    assert lock_manager.release_calls == []
