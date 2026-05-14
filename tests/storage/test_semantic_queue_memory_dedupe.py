# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory-context semantic enqueue deduplication (#769)."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.queuefs.semantic_queue import SemanticQueue, is_semantic_msg_stale


@pytest.mark.asyncio
async def test_memory_semantic_enqueue_deduped_within_window():
    mock_agfs = MagicMock()
    with patch.object(NamedQueue, "enqueue", new_callable=AsyncMock) as named_enqueue:
        named_enqueue.return_value = "queued-id"
        q = SemanticQueue(mock_agfs, "/queue", "semantic")
        msg = SemanticMsg(
            uri="viking://user/default/memories/entities",
            context_type="memory",
            account_id="acc",
            user_id="u1",
            agent_id="a1",
        )
        r1 = await q.enqueue(msg)
        r2 = await q.enqueue(
            SemanticMsg(
                uri="viking://user/default/memories/entities",
                context_type="memory",
                account_id="acc",
                user_id="u1",
                agent_id="a1",
            )
        )
        assert r1 == "queued-id"
        assert r2 == "deduplicated"
        assert named_enqueue.call_count == 1


@pytest.mark.asyncio
async def test_memory_semantic_enqueue_different_uri_not_deduped():
    mock_agfs = MagicMock()
    with patch.object(NamedQueue, "enqueue", new_callable=AsyncMock) as named_enqueue:
        named_enqueue.return_value = "queued-id"
        q = SemanticQueue(mock_agfs, "/queue", "semantic")
        await q.enqueue(
            SemanticMsg(
                uri="viking://user/default/memories/entities",
                context_type="memory",
            )
        )
        await q.enqueue(
            SemanticMsg(
                uri="viking://user/default/memories/patterns",
                context_type="memory",
            )
        )
        assert named_enqueue.call_count == 2


@pytest.mark.asyncio
async def test_non_memory_context_not_deduped():
    mock_agfs = MagicMock()
    with patch.object(NamedQueue, "enqueue", new_callable=AsyncMock) as named_enqueue:
        named_enqueue.return_value = "queued-id"
        q = SemanticQueue(mock_agfs, "/queue", "semantic")
        uri = "viking://resources/docs"
        await q.enqueue(SemanticMsg(uri=uri, context_type="resource"))
        await q.enqueue(SemanticMsg(uri=uri, context_type="resource"))
        assert named_enqueue.call_count == 2


@pytest.mark.asyncio
async def test_coalesced_semantic_messages_mark_old_version_stale():
    mock_agfs = MagicMock()
    with patch.object(NamedQueue, "enqueue", new_callable=AsyncMock) as named_enqueue:
        named_enqueue.return_value = "queued-id"
        q = SemanticQueue(mock_agfs, "/queue", "semantic")
        coalesce_key = f"resource|acc|u|a|viking://resources/docs/{uuid4().hex}"
        first = SemanticMsg(
            uri="viking://resources/docs",
            context_type="resource",
            coalesce_key=coalesce_key,
        )
        second = SemanticMsg(
            uri="viking://resources/docs",
            context_type="resource",
            coalesce_key=first.coalesce_key,
        )

        await q.enqueue(first)
        await q.enqueue(second)

        assert first.coalesce_version == 1
        assert second.coalesce_version == 2
        assert is_semantic_msg_stale(first)
        assert not is_semantic_msg_stale(second)


class _FakeHandle:
    def __init__(self):
        self.id = "lock-1"
        self.locks = []


class _FakeLockManager:
    def __init__(self):
        self.acquired_batches = []
        self.release_calls = []

    def create_handle(self):
        return _FakeHandle()

    def get_handle(self, handle_id):
        del handle_id
        return None

    async def acquire_exact_path_batch(self, handle, paths):
        self.acquired_batches.append(paths)
        handle.locks.extend(paths)
        return True

    async def release(self, handle):
        self.release_calls.append(handle.id)

    async def release_selected(self, handle, lock_paths):
        del handle, lock_paths


class _FakeVikingFS:
    def __init__(self):
        self.writes = []

    def _uri_to_path(self, uri, ctx=None):
        del ctx
        return f"/fake/{uri.replace('://', '/').strip('/')}"

    async def write_file(self, uri, content, ctx=None):
        del ctx
        self.writes.append((uri, content))


@pytest.mark.asyncio
async def test_stale_memory_semantic_write_is_skipped(monkeypatch):
    lock_manager = _FakeLockManager()
    viking_fs = _FakeVikingFS()
    processor = SemanticProcessor()
    coalesce_key = f"memory|acc|u|a|viking://user/default/memories/preferences/{uuid4().hex}"

    with patch.object(NamedQueue, "enqueue", new_callable=AsyncMock):
        q = SemanticQueue(MagicMock(), "/queue", "semantic")
        first = SemanticMsg(
            uri="viking://user/default/memories/preferences",
            context_type="memory",
            coalesce_key=coalesce_key,
        )
        latest = SemanticMsg(
            uri="viking://user/default/memories/preferences",
            context_type="memory",
            coalesce_key=coalesce_key,
        )
        await q.enqueue(first)
        await q.enqueue(latest)

    monkeypatch.setattr("openviking.storage.transaction.get_lock_manager", lambda: lock_manager)

    wrote_first = await processor._write_memory_directory_semantics(
        msg=first,
        viking_fs=viking_fs,
        dir_uri=first.uri,
        overview="old overview",
        abstract="old abstract",
        ctx=None,
    )
    wrote_latest = await processor._write_memory_directory_semantics(
        msg=latest,
        viking_fs=viking_fs,
        dir_uri=latest.uri,
        overview="latest overview",
        abstract="latest abstract",
        ctx=None,
    )

    assert not wrote_first
    assert wrote_latest
    assert lock_manager.acquired_batches == [
        [
            "/fake/viking/user/default/memories/preferences/.overview.md",
            "/fake/viking/user/default/memories/preferences/.abstract.md",
        ]
    ]
    assert viking_fs.writes == [
        ("viking://user/default/memories/preferences/.overview.md", "latest overview"),
        ("viking://user/default/memories/preferences/.abstract.md", "latest abstract"),
    ]
