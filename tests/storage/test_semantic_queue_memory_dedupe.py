# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory-context semantic enqueue deduplication (#769)."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.queuefs.semantic_queue import SemanticQueue


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
            peer_id="p1",
        )
        r1 = await q.enqueue(msg)
        r2 = await q.enqueue(
            SemanticMsg(
                uri="viking://user/default/memories/entities",
                context_type="memory",
                account_id="acc",
                user_id="u1",
                peer_id="p1",
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
async def test_coalesced_semantic_messages_share_one_queue_trigger():
    mock_agfs = MagicMock()
    with patch.object(NamedQueue, "enqueue", new_callable=AsyncMock) as named_enqueue:
        named_enqueue.return_value = "queued-id"
        q = SemanticQueue(mock_agfs, "/queue", "semantic")
        coalesce_key = f"resource|acc|u|p|viking://resources/docs/{uuid4().hex}"
        first = SemanticMsg(
            uri="viking://resources/docs",
            context_type="resource",
            coalesce_key=coalesce_key,
            changes={"modified": ["a.md"]},
        )
        second = SemanticMsg(
            uri="viking://resources/docs",
            context_type="resource",
            coalesce_key=first.coalesce_key,
            changes={"modified": ["b.md"], "deleted": ["a.md"]},
        )

        assert await q.enqueue(first) == "queued-id"
        assert await q.enqueue(second) == "queued-id"
        assert named_enqueue.call_count == 1

        third = SemanticMsg(
            uri="viking://resources/docs",
            context_type="resource",
            coalesce_key=first.coalesce_key,
            changes={"modified": ["c.md"]},
        )
        processed = []

        async def process(msg, lock):
            del lock
            processed.append(msg)
            if len(processed) == 1:
                assert await q.enqueue(third) == "queued-id"

        processor = SemanticProcessor()
        with patch.object(processor, "_process_semantic_message", new=process):
            await processor.on_dequeue({"data": first.to_json()})

        assert named_enqueue.call_count == 1
        assert processed[0].changes == {
            "modified": ["b.md"],
            "deleted": ["a.md"],
        }
        assert {event["id"] for event in processed[0]._coalesced_events} == {
            first.id,
            second.id,
        }
        assert processed[1].changes == {"modified": ["c.md"]}


class _FakePathLock:
    """Mock for _async_agfs pathlock operations."""

    def __init__(self):
        self.acquired_batches = []
        self.release_calls = []

    async def pathlock_acquire_exact_batch(self, paths):
        self.acquired_batches.append(paths)
        return {"id": "lock-1"}

    async def pathlock_release(self, lease):
        self.release_calls.append(lease["id"])


class _FakeVikingFS:
    def __init__(self, pathlock=None):
        self._async_agfs = pathlock or _FakePathLock()
        self.writes = []

    def _uri_to_path(self, uri, ctx=None):
        del ctx
        return f"/fake/{uri.replace('://', '/').strip('/')}"

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        del ctx, lease_ref
        self.writes.append((uri, content))


class _FakeMemoryDirFS:
    async def ls(self, uri, node_limit=None, ctx=None):
        del uri, node_limit, ctx
        return [
            {"name": "first.md", "isDir": False},
            {"name": "second.md", "isDir": False},
        ]


@pytest.mark.asyncio
async def test_memory_semantic_write_uses_sidecar_lock(monkeypatch):
    pathlock = _FakePathLock()
    viking_fs = _FakeVikingFS(pathlock)
    processor = SemanticProcessor()
    msg = SemanticMsg(
        uri="viking://user/default/memories/preferences",
        context_type="memory",
    )

    wrote = await processor._write_memory_directory_semantics(
        msg=msg,
        viking_fs=viking_fs,
        dir_uri=msg.uri,
        overview="overview",
        abstract="abstract",
        ctx=None,
    )

    assert wrote
    assert pathlock.acquired_batches == [
        [
            "/fake/viking/user/default/memories/preferences/.overview.md",
            "/fake/viking/user/default/memories/preferences/.abstract.md",
        ]
    ]
    assert viking_fs.writes == [
        ("viking://user/default/memories/preferences/.overview.md", "overview"),
        ("viking://user/default/memories/preferences/.abstract.md", "abstract"),
    ]


@pytest.mark.asyncio
async def test_memory_directory_summarizes_all_uncached_files(monkeypatch):
    processor = SemanticProcessor(max_concurrent_llm=4)
    summaries = []

    async def generate_file_summary(file_path, llm_sem=None, ctx=None):
        del llm_sem, ctx
        name = file_path.rsplit("/", 1)[-1]
        return {"name": name, "summary": f"summary:{name}"}

    async def generate_overview(dir_uri, file_summaries, children_abstracts, llm_sem=None):
        del dir_uri, children_abstracts, llm_sem
        summaries.extend(file_summaries)
        return "overview"

    async def write_semantics(**kwargs):
        del kwargs
        return True

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeMemoryDirFS(),
    )
    monkeypatch.setattr(processor, "_generate_single_file_summary", generate_file_summary)
    monkeypatch.setattr(processor, "_generate_overview", generate_overview)
    monkeypatch.setattr(
        processor,
        "_normalize_overview_generation",
        lambda overview: (overview, "abstract"),
    )
    monkeypatch.setattr(processor, "_write_memory_directory_semantics", write_semantics)

    await processor._process_memory_directory(
        SemanticMsg(
            uri="viking://user/default/memories/preferences",
            context_type="memory",
            skip_vectorization=True,
        )
    )

    assert [item["name"] for item in summaries] == ["first.md", "second.md"]


@pytest.mark.asyncio
async def test_memory_directory_vectorizes_changed_files_with_generated_summary(monkeypatch):
    processor = SemanticProcessor(max_concurrent_llm=4)
    dir_uri = "viking://user/default/memories/preferences"
    changed_uri = f"{dir_uri}/first.md"
    captured_file_vectorize = []
    captured_directory_vectorize = []

    async def generate_file_summary(file_path, llm_sem=None, ctx=None):
        del llm_sem, ctx
        name = file_path.rsplit("/", 1)[-1]
        return {"name": name, "summary": f"summary:{name}", "content": "raw content"}

    async def generate_overview(dir_uri, file_summaries, children_abstracts, llm_sem=None):
        del dir_uri, children_abstracts, llm_sem
        assert len(captured_file_vectorize) == 1
        assert all("content" not in summary for summary in file_summaries)
        return "overview"

    async def write_semantics(**kwargs):
        del kwargs
        return True

    async def vectorize_single_file(**kwargs):
        captured_file_vectorize.append(kwargs)

    async def vectorize_directory(**kwargs):
        captured_directory_vectorize.append(kwargs)

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeMemoryDirFS(),
    )
    monkeypatch.setattr(processor, "_generate_single_file_summary", generate_file_summary)
    monkeypatch.setattr(processor, "_generate_overview", generate_overview)
    monkeypatch.setattr(
        processor,
        "_normalize_overview_generation",
        lambda overview: (overview, "abstract"),
    )
    monkeypatch.setattr(processor, "_write_memory_directory_semantics", write_semantics)
    monkeypatch.setattr(processor, "_vectorize_single_file", vectorize_single_file)
    monkeypatch.setattr(processor, "_vectorize_directory", vectorize_directory)

    await processor._process_memory_directory(
        SemanticMsg(
            uri=dir_uri,
            context_type="memory",
            changes={"modified": [changed_uri]},
        )
    )

    assert len(captured_file_vectorize) == 1
    assert captured_file_vectorize[0]["parent_uri"] == dir_uri
    assert captured_file_vectorize[0]["context_type"] == "memory"
    assert captured_file_vectorize[0]["file_path"] == changed_uri
    assert captured_file_vectorize[0]["summary_dict"] == {
        "name": "first.md",
        "summary": "summary:first.md",
        "content": "raw content",
    }
    assert captured_file_vectorize[0]["preserve_existing_created_at"] is True
    assert len(captured_directory_vectorize) == 1
    assert captured_directory_vectorize[0]["uri"] == dir_uri
