# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Semantic queue deduplication contracts."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

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
async def test_directory_refresh_keeps_one_durable_trigger():
    mock_agfs = MagicMock()
    with patch.object(NamedQueue, "enqueue", new_callable=AsyncMock) as named_enqueue:
        named_enqueue.return_value = "queued-id"
        q = SemanticQueue(mock_agfs, "/queue", "semantic")
        first = SemanticMsg(
            uri="viking://resources/docs",
            context_type="resource",
            account_id="acc",
            directory_refresh_only=True,
        )
        second = SemanticMsg(
            uri="viking://resources/docs",
            context_type="resource",
            account_id="acc",
            directory_refresh_only=True,
        )

        await q.enqueue(first)
        await q.enqueue(second)

        assert named_enqueue.call_count == 1
        revision = q.claim_directory_refresh(first, "queued-id")
        events = q.finish_directory_refresh(first, revision)
        assert list(events) == [first.id, second.id]


class _FakeMemoryDirFS:
    async def ls(self, uri, node_limit=None, ctx=None):
        del uri, node_limit, ctx
        return [
            {"name": "first.md", "isDir": False},
            {"name": "second.md", "isDir": False},
        ]


@pytest.mark.asyncio
async def test_restart_keeps_one_directory_refresh_from_legacy_delete_messages():
    q = SemanticQueue(MagicMock(), "/queue", "semantic")
    messages = [
        SemanticMsg(
            uri="viking://resources/docs",
            context_type="resource",
            recursive=False,
            account_id="acc",
            changes={"deleted": [f"viking://resources/docs/{name}.md"]},
        )
        for name in ("a", "b", "c")
    ]
    q.restore_directory_refreshes(
        [
            {"id": f"queue-{index}", "data": json.dumps(msg.to_dict())}
            for index, msg in enumerate(messages)
        ]
    )

    revision = q.claim_directory_refresh(messages[0], "queue-0")
    assert q.claim_directory_refresh(messages[1], "queue-1") is None
    assert q.claim_directory_refresh(messages[2], "queue-2") is None
    events = q.finish_directory_refresh(messages[0], revision)
    assert list(events) == [msg.id for msg in messages]


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
