# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Inline semantic outcomes and concurrent exact-file legacy chunk cleanup."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import openviking.service.reindex_executor as reindex_module
from openviking.service.reindex_executor import _ReindexRunContext
from openviking.storage.errors import LockError
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.vector_ids import vector_record_id
from openviking.utils.ingest_options import IngestOptions
from tests.server.test_reindex_memory_sources import ROOT, records, setup_memory


@pytest.mark.asyncio
@pytest.mark.parametrize("uri", [ROOT, "viking://user/bob/memories"])
async def test_memory_semantic_and_vectors_reconciles_then_queues_shared_work(monkeypatch, uri):
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, {}, {})
    executor._reindex_memory_vectors = AsyncMock()
    executor._enqueue_reindex_semantic_message = AsyncMock()
    ingest_options = IngestOptions.from_search_tags(["team=search"], mode="append")
    run = _ReindexRunContext(
        ctx=ctx,
        counters=counters,
        lease={"lease_id": "outer"},
        ingest_options=ingest_options,
    )
    await executor._reindex_memory(
        uri=uri,
        mode="semantic_and_vectors",
        run=run,
        root_is_dir=True,
        formal_entries=[],
        vector_inventory={},
    )
    executor._reindex_memory_vectors.assert_awaited_once()
    assert executor._reindex_memory_vectors.await_args.kwargs["reconcile_only"] is True
    call = executor._enqueue_reindex_semantic_message.await_args.kwargs
    assert call["root_is_dir"] is True and call["run"] is run
    assert call["msg"].uri == uri
    assert call["msg"].context_type == "memory"
    assert call["msg"].skip_vectorization is False
    assert call["msg"].use_hierarchical_aggregation is True
    assert call["msg"].ingest_options == ingest_options


@pytest.mark.asyncio
async def test_missing_memory_root_cleans_without_queuing_semantics(monkeypatch):
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, {}, {})
    executor._enqueue_reindex_semantic_message = AsyncMock()
    await executor._reindex_memory(
        uri=ROOT,
        mode="semantic_and_vectors",
        run=_ReindexRunContext(ctx=ctx, counters=counters),
        root_exists=False,
        root_is_dir=True,
        formal_entries=[],
        vector_inventory={},
    )
    executor._enqueue_reindex_semantic_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_reindex_semantic_enqueue_bypasses_memory_new_work_dedupe(monkeypatch):
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, {}, {})
    lease = {"lease_id": "outer"}
    handoff = {"owner_id": "semantic-worker"}
    fs._async_agfs = SimpleNamespace(
        pathlock_to_handoff=AsyncMock(return_value=handoff),
        pathlock_handoff=AsyncMock(),
    )
    queue = SimpleNamespace(enqueue=AsyncMock(return_value="queued"))
    monkeypatch.setattr(
        reindex_module,
        "get_service",
        lambda: SimpleNamespace(viking_fs=fs, vikingdb_manager=db),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: SimpleNamespace(
            SEMANTIC="semantic",
            get_queue=lambda *args, **kwargs: queue,
        ),
    )
    run = _ReindexRunContext(
        ctx=ctx,
        counters=counters,
        lease=lease,
        lock_root_uri=ROOT,
    )
    msg = SemanticMsg(
        uri=ROOT,
        context_type="memory",
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        peer_id=ctx.user.user_id,
        generation_trigger="reindex",
    )

    await executor._enqueue_reindex_semantic_message(msg=msg, root_is_dir=True, run=run)

    queue.enqueue.assert_awaited_once_with(msg, deduplicate_memory=False)


@pytest.mark.asyncio
class ChunkLocks:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.calls = []
        self.releases = []
        self.error = None
        self.begin = asyncio.Event()
        self.finish = None

    async def acquire(self, paths, **kwargs):
        self.calls.append((paths, kwargs))
        self.begin.set()
        if self.finish is not None:
            await self.finish.wait()
        if self.error:
            raise self.error
        await self.lock.acquire()
        return {"lease_id": "chunk-lease"}

    async def release(self, lease):
        self.releases.append(lease)
        self.lock.release()


def chunk_case(monkeypatch):
    base, chunk = ROOT + "/a.md", ROOT + "/a.md#chunk_0000"
    record_id = vector_record_id("memory_sources", chunk, 2)
    inventory = records(("base", base, 2), (record_id, chunk, 2))
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, {base: "Current memory"}, inventory)
    locks = ChunkLocks()
    fs._async_agfs = SimpleNamespace(
        pathlock_acquire_exact_batch=locks.acquire,
        pathlock_release=locks.release,
    )
    fs._uri_to_path = lambda uri, **kwargs: "/local/memory_sources/" + uri[len("viking://") :]
    stored = dict(inventory)

    async def delete(ids, **kwargs):
        for value in ids:
            stored.pop(value, None)
        return len(ids)

    db.strict_delete.side_effect = delete
    run = _ReindexRunContext(ctx=ctx, counters=counters, lease={"lease_id": "outer"})
    return SimpleNamespace(
        base=base,
        chunk=chunk,
        record_id=record_id,
        executor=executor,
        fs=fs,
        db=db,
        counters=counters,
        locks=locks,
        stored=stored,
        run=run,
    )


async def reindex(case):
    await case.executor._reindex_memory(uri=case.base, mode="vectors_only", run=case.run)


@pytest.mark.asyncio
async def test_real_chunk_created_after_initial_probe_keeps_new_vector(monkeypatch):
    case = chunk_case(monkeypatch)

    async def enqueue(**kwargs):
        case.fs.sources[case.chunk] = "New real file"
        case.stored[case.record_id] = {"id": case.record_id, "abstract": "New real vector"}

    case.executor._upsert_context.side_effect = enqueue
    await reindex(case)
    assert case.record_id in case.stored
    case.db.strict_delete.assert_not_awaited()
    assert len(case.locks.releases) == 1


@pytest.mark.asyncio
async def test_chunk_probe_and_delete_hold_same_lock_and_writer_waits(monkeypatch):
    case = chunk_case(monkeypatch)
    original_stat = case.fs.stat
    protected_probes = []

    async def stat(uri, **kwargs):
        if uri == case.chunk and case.locks.calls:
            protected_probes.append(case.locks.lock.locked())
        return await original_stat(uri, **kwargs)

    case.fs.stat = stat
    writer_started = asyncio.Event()
    writer_done = asyncio.Event()
    writer = None

    async def write():
        writer_started.set()
        async with case.locks.lock:
            case.fs.sources[case.chunk] = "Created after cleanup"
            case.stored[case.record_id] = {"id": case.record_id, "abstract": "New real vector"}
            writer_done.set()

    async def delete(ids, **kwargs):
        nonlocal writer
        assert case.locks.lock.locked()
        writer = asyncio.create_task(write())
        await writer_started.wait()
        assert not writer_done.is_set()
        for value in ids:
            case.stored.pop(value, None)
        return len(ids)

    case.db.strict_delete.side_effect = delete
    await reindex(case)
    assert writer is not None
    await asyncio.wait_for(writer, 1)
    assert protected_probes == [True]
    assert writer_done.is_set() and case.record_id in case.stored
    assert case.locks.calls[0][1]["owner_lease_ref"] == case.run.lease
    assert case.counters.deleted_records == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["acquire", "probe", "delete"])
async def test_chunk_cleanup_failure_preserves_vector_and_releases_owned_lock(monkeypatch, failure):
    case = chunk_case(monkeypatch)
    if failure == "acquire":
        case.locks.error = LockError("chunk lock unavailable")
    elif failure == "probe":

        async def enqueue(**kwargs):
            case.fs.sources[case.chunk] = RuntimeError("chunk stat unavailable")

        case.executor._upsert_context.side_effect = enqueue
    else:
        case.db.strict_delete.side_effect = RuntimeError("delete unavailable")
    with pytest.raises(LockError if failure == "acquire" else RuntimeError):
        await reindex(case)
    assert case.record_id in case.stored
    assert len(case.locks.releases) == (0 if failure == "acquire" else 1)
    assert not case.locks.lock.locked()


@pytest.mark.asyncio
async def test_cancelled_late_chunk_acquisition_releases_lease_before_return(monkeypatch):
    case = chunk_case(monkeypatch)
    case.locks.finish = asyncio.Event()
    task = asyncio.create_task(reindex(case))
    try:
        await asyncio.wait_for(case.locks.begin.wait(), 0.5)
        task.cancel()
        await asyncio.sleep(0)
        case.locks.finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(case.locks.releases) == 1
        assert not case.locks.lock.locked()
        case.db.strict_delete.assert_not_awaited()
    finally:
        case.locks.finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
