# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory-context semantic enqueue deduplication (#769)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openviking.pyagfs import get_binding_client
from openviking.storage.abstract_overview import (
    AbstractOverviewWriteResult,
    parse_abstract_overview,
)
from openviking.storage.queuefs.semantic_msg import SemanticMsg, build_semantic_coalesce_key
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.queuefs.semantic_queue import SemanticQueue, is_semantic_msg_stale


def _processor(**kwargs):
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=SimpleNamespace()))
    return SemanticProcessor(vlm_resolver=resolver, **kwargs)


@pytest.fixture
def native_queue():
    client_class, _ = get_binding_client()
    client = client_class(config={"service_name": f"semantic-test-{uuid4().hex}"})
    client.mount("queuefs", "/queue", {"backend": "memory"})
    yield SemanticQueue(client, "/queue", "semantic")
    client.close()


@pytest.mark.asyncio
async def test_memory_semantic_enqueue_deduped_within_window(native_queue):
    first = SemanticMsg(
        uri="viking://user/default/memories/entities", context_type="memory", account_id="acc"
    )
    await native_queue.enqueue(first)
    duplicate = SemanticMsg(uri=first.uri, context_type="memory", account_id="acc")
    assert await native_queue.enqueue(duplicate) == "deduplicated"

    for uri, context_type in (
        ("viking://user/default/memories/patterns", "memory"),
        ("viking://resources/docs", "resource"),
        ("viking://resources/docs", "resource"),
    ):
        msg = SemanticMsg(uri=uri, context_type=context_type, account_id="acc")
        await native_queue.enqueue(msg)

    delivered = []
    for _ in range(4):
        msg = await native_queue.dequeue()
        assert msg is not None
        delivered.append((msg.context_type, msg.uri))
    assert delivered == [
        ("memory", first.uri),
        ("memory", "viking://user/default/memories/patterns"),
        ("resource", "viking://resources/docs"),
        ("resource", "viking://resources/docs"),
    ]
    assert await native_queue.dequeue() is None


@pytest.mark.asyncio
async def test_worker_without_lock_does_not_dedupe_a_memory_retry(monkeypatch, native_queue):
    from openviking.storage.queuefs.semantic_work import SemanticMessageWork

    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: SimpleNamespace(SEMANTIC="semantic", get_queue=lambda _: native_queue),
    )
    msg = SemanticMsg(
        uri="viking://user/default/memories/entities",
        context_type="memory",
        account_id="acc",
        user_id="u1",
        peer_id="p1",
    )
    await native_queue.enqueue(msg)
    original = await native_queue.dequeue()
    assert original is not None

    await SemanticMessageWork(_processor(), original, None).reenqueue()

    retry = await native_queue.dequeue()
    assert retry is not None
    assert (retry.id, retry.uri, retry.account_id) == (msg.id, msg.uri, msg.account_id)
    assert await native_queue.dequeue() is None


@pytest.mark.asyncio
async def test_write_and_ingest_directory_refreshes_coalesce(monkeypatch, native_queue):
    q = native_queue
    directory_uri = f"viking://resources/docs/{uuid4().hex}"
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: SimpleNamespace(SEMANTIC="semantic", get_queue=lambda *a, **kw: q),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: object()
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.plan_abstract_overview_refresh",
        AsyncMock(return_value=SimpleNamespace(action=FreshnessAction.REFRESH_NOW)),
    )
    source = SemanticMsg(
        uri=directory_uri, context_type="resource", generation_trigger="content_write",
    )
    file_work = SemanticMsg(
        uri=directory_uri, context_type="resource",
        coalesce_key=build_semantic_coalesce_key(context_type="resource", uri=directory_uri),
    )
    await q.enqueue(file_work)
    processor = _processor()
    await processor._enqueue_directory_refresh(
        source, directory_uri, changes={"modified": [f"{directory_uri}/a.md"]}
    )
    source.generation_trigger = "resource_ingest"
    await processor._enqueue_parent_refresh(
        source, f"{directory_uri}/imported", l0_body_changed=True
    )
    await q.dequeue()  # File work has its own key.
    first, second = await q.dequeue(), await q.dequeue()
    assert first.coalesce_key == second.coalesce_key != ""
    assert file_work.coalesce_version == 1
    assert first.coalesce_version == 2
    assert second.coalesce_version == 3
    assert await is_semantic_msg_stale(first, agfs=q._async_agfs)
    assert not await is_semantic_msg_stale(second, agfs=q._async_agfs)
    assert not await is_semantic_msg_stale(file_work, agfs=q._async_agfs)


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


def _patch_semantic_config(monkeypatch, *, overview_sample_limit=32):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=overview_sample_limit)),
    )


@pytest.mark.asyncio
async def test_stale_memory_semantic_write_is_success(monkeypatch, native_queue):
    pathlock = _FakePathLock()
    viking_fs = _FakeVikingFS(pathlock)
    processor = _processor()
    coalesce_key = f"memory|acc|u|p|viking://user/default/memories/preferences/{uuid4().hex}"

    q = native_queue
    pathlock.revision_latest = q._async_agfs.revision_latest
    first = SemanticMsg(
        uri="viking://user/default/memories/preferences",
        context_type="memory", coalesce_key=coalesce_key,
    )
    latest = SemanticMsg(
        uri="viking://user/default/memories/preferences",
        context_type="memory", coalesce_key=coalesce_key,
    )
    await q.enqueue(first)
    await q.enqueue(latest)

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
    assert pathlock.acquired_batches == [
        [
            "/fake/viking/user/default/memories/preferences/.overview.md",
            "/fake/viking/user/default/memories/preferences/.abstract.md",
        ]
    ]
    assert [uri for uri, _ in viking_fs.writes] == [
        "viking://user/default/memories/preferences/.overview.md",
        "viking://user/default/memories/preferences/.abstract.md",
    ]
    assert [parse_abstract_overview(raw).body.strip() for _, raw in viking_fs.writes] == [
        "latest overview",
        "latest abstract",
    ]


@pytest.mark.asyncio
async def test_memory_directory_summarizes_all_files_but_samples_overview(monkeypatch):
    processor = _processor(max_concurrent_llm=4)
    summaries = []
    summarized_files = []
    active = peak = 0
    file_names = [f"file-{index:03}.md" for index in range(40)]
    fake_fs = _FakeMemoryDirFS()
    monkeypatch.setattr(
        fake_fs, "ls", AsyncMock(return_value=[{"name": name} for name in file_names])
    )

    async def generate_file_summary(file_path, llm_sem=None, ctx=None):
        del llm_sem, ctx
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        name = file_path.rsplit("/", 1)[-1]
        summarized_files.append(name)
        return {"name": name, "summary": f"summary:{name}"}

    async def generate_overview(
        dir_uri, file_summaries, children_abstracts, llm_sem=None, **kwargs
    ):
        del dir_uri, children_abstracts, llm_sem, kwargs
        summaries.extend(file_summaries)
        return "overview"

    async def write_semantics(**kwargs):
        assert kwargs["total_entries"] == 40
        assert kwargs["sampled_entries"] == 32
        return AbstractOverviewWriteResult(
            wrote=True, overview_body_changed=True, abstract_body_changed=True
        )

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )
    _patch_semantic_config(monkeypatch)
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

    assert sorted(summarized_files) == file_names
    assert peak == 4
    assert len(summaries) == 32
    assert summaries[0]["name"] == file_names[0]
    assert summaries[-1]["name"] == file_names[-1]


@pytest.mark.asyncio
async def test_memory_directory_vectorizes_changed_files_with_generated_summary(monkeypatch):
    processor = _processor(max_concurrent_llm=4)
    dir_uri = "viking://user/default/memories/preferences"
    changed_uri = f"{dir_uri}/first.md"
    captured_file_vectorize = []
    captured_directory_vectorize = []

    async def generate_file_summary(file_path, llm_sem=None, ctx=None):
        del llm_sem, ctx
        name = file_path.rsplit("/", 1)[-1]
        return {"name": name, "summary": f"summary:{name}", "content": "raw content"}

    async def generate_overview(
        dir_uri, file_summaries, children_abstracts, llm_sem=None, **kwargs
    ):
        del dir_uri, children_abstracts, llm_sem, kwargs
        assert len(captured_file_vectorize) == 1
        assert all("content" not in summary for summary in file_summaries)
        return "overview"

    async def write_semantics(**kwargs):
        del kwargs
        return AbstractOverviewWriteResult(
            wrote=True, overview_body_changed=True, abstract_body_changed=True
        )

    async def vectorize_single_file(**kwargs):
        captured_file_vectorize.append(kwargs)

    async def vectorize_directory(**kwargs):
        captured_directory_vectorize.append(kwargs)

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeMemoryDirFS(),
    )
    _patch_semantic_config(monkeypatch)
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


@pytest.mark.asyncio
async def test_memory_directory_skips_vectorization_when_visible_semantics_are_unchanged(
    monkeypatch,
):
    processor = _processor(max_concurrent_llm=4)
    vectorize_directory = AsyncMock()
    vectorize_file = AsyncMock()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeMemoryDirFS(),
    )
    _patch_semantic_config(monkeypatch)
    monkeypatch.setattr(
        processor,
        "_generate_single_file_summary",
        AsyncMock(return_value={"name": "file.md", "summary": "summary"}),
    )
    monkeypatch.setattr(processor, "_generate_overview", AsyncMock(return_value="overview"))
    monkeypatch.setattr(
        processor, "_normalize_overview_generation", lambda overview: (overview, "abstract")
    )
    monkeypatch.setattr(
        processor,
        "_write_memory_directory_semantics",
        AsyncMock(return_value=AbstractOverviewWriteResult(wrote=True)),
    )
    monkeypatch.setattr(processor, "_vectorize_single_file", vectorize_file)
    monkeypatch.setattr(processor, "_vectorize_directory", vectorize_directory)

    await processor._process_memory_directory(
        SemanticMsg(
            uri="viking://user/default/memories/preferences",
            context_type="memory",
        )
    )

    assert vectorize_file.await_count == 2
    vectorize_directory.assert_not_awaited()
