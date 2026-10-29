# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from openviking.storage.errors import InvalidPathLockRequestError
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_executor import SemanticTreeStats
from openviking.storage.queuefs.semantic_lock import SemanticLockScope
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


def _processor():
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=SimpleNamespace()))
    return SemanticProcessor(vlm_resolver=resolver)


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
    processor = _processor()
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


@pytest.mark.asyncio
async def test_semantic_lock_reacquires_unicode_coverage_after_legacy_mapping_error(monkeypatch):
    covered_path = "/local/default/resources/agent-memory/credit-risk/信贷风险管理_1.md"
    lock_path = (
        "/local/default/resources/agent-memory/credit-risk/.exact.ovlock.1.md.14dd8bad93aaa5b0"
    )
    lease = {"id": "reacquired-lock"}
    pathlock = SimpleNamespace(
        pathlock_adopt=AsyncMock(
            side_effect=InvalidPathLockRequestError(
                f"invalid lock request: handoff coverage '{covered_path}' "
                f"does not map to lock path '{lock_path}'"
            )
        ),
        pathlock_acquire_batch=AsyncMock(return_value=lease),
        pathlock_release=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_lock.get_viking_fs",
        lambda: _FakeVikingFS(pathlock),
    )
    handoff = {
        "owner_id": "legacy-owner",
        "lock_paths": [lock_path],
        "covered_paths": [{"path": covered_path, "kind": "tree"}],
    }

    scope = await SemanticLockScope.resolve(handoff)

    assert scope.lock == lease
    pathlock.pathlock_acquire_batch.assert_awaited_once_with(handoff["covered_paths"])
    await scope.close()
    pathlock.pathlock_release.assert_awaited_once_with(lease)


def test_semantic_tree_stats_aggregate_multiple_plans_for_one_request():
    telemetry_id = "reindex-request-stats"
    SemanticProcessor._cache_tree_stats(
        telemetry_id,
        "viking://resources/one",
        SemanticTreeStats(total_nodes=2, done_nodes=2, indexed_records=2, failures=["one failed"]),
    )
    SemanticProcessor._cache_tree_stats(
        telemetry_id,
        "viking://resources/two",
        SemanticTreeStats(total_nodes=3, done_nodes=3, indexed_records=3, failures=["two failed"]),
    )

    stats = SemanticProcessor.consume_tree_stats(telemetry_id=telemetry_id)

    assert stats.total_nodes == 5
    assert stats.done_nodes == 5
    assert stats.indexed_records == 5
    assert stats.failures == ["one failed", "two failed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("context_type", ["resource", "memory", "skill"])
async def test_missing_root_is_acked_before_lock_scope_is_resolved(monkeypatch, context_type):
    """Resolving the lock for a deleted root would recreate it to hold lock metadata."""
    from openviking.service.task_store import PersistentTaskStore
    from openviking.service.task_tracker import TaskTracker
    from tests.test_task_tracker import _FakeAgfs

    monkeypatch.setattr(
        "openviking.service.task_tracker._instance", TaskTracker(PersistentTaskStore(_FakeAgfs()))
    )
    processor = _processor()
    fs = _FakeVikingFS()

    async def adopt(handoff):
        return handoff

    fs._async_agfs.pathlock_adopt = adopt

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fs,
    )

    async def fail_resolve(*args, **kwargs):
        raise AssertionError("lock scope must not be resolved for a missing root")

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        fail_resolve,
    )

    roots = {
        "resource": "viking://resources/deleted-parent",
        "memory": "viking://user/alice/memories/deleted-parent",
        "skill": "viking://user/alice/skills/deleted-parent",
    }
    msg = SemanticMsg(
        uri=roots[context_type],
        context_type=context_type,
        recursive=False,
        account_id="default",
        user_id="alice",
        changes={"deleted": [f"{roots[context_type]}/a.md"]},
        generation_trigger="content_delete",
        lock_handoff={"id": "queued-skill-lock"} if context_type == "skill" else None,
    )
    result = await processor.on_dequeue(msg.to_dict())
    assert result.outcome is ProcessOutcome.SUCCESS
    assert fs._async_agfs.release_calls == (
        ["queued-skill-lock"] if context_type == "skill" else []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("context_type", "tracked", "fail_generation"),
    [
        ("resource", True, False),
        ("skill", True, False),
        ("skill", False, False),
        ("skill", False, True),
    ],
)
async def test_shared_scheduler_drains_each_messages_embeddings_before_unlock(
    monkeypatch, context_type, tracked, fail_generation
):
    from openviking.pyagfs import AGFSSyncClientProtocol
    from openviking.server.identity import RequestContext, Role
    from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
    from openviking.service.task_store import PersistentTaskStore
    from openviking.service.task_tracker import TaskStatus, TaskTracker
    from openviking.service.task_work_index import (
        bind_task_context,
        extract_task_metadata,
    )
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexAction,
        IndexSlot,
        SemanticAction,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )
    from openviking.storage.queuefs.named_queue import NamedQueue
    from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
    from openviking_cli.session.user_id import UserIdentifier
    from tests.storage.test_semantic_executor_skip_files import _FakeVikingFS as TreeFS
    from tests.test_task_tracker import _FakeAgfs, _owner_kwargs

    root = (
        "viking://resources/demo"
        if context_type == "resource"
        else "viking://agent/skills/demo/reference"
    )
    warm_root = "viking://resources/warm"
    released = []

    class FS(TreeFS):
        async def exists(self, uri, ctx=None):
            return True

        async def pathlock_adopt(self, lease):
            return lease

        async def pathlock_release(self, lease):
            if "id" in lease:
                released.append(lease["id"])

    fs = FS({root: [], warm_root: []})
    for module in ("semantic_processor", "semantic_executor", "semantic_lock"):
        monkeypatch.setattr(f"openviking.storage.queuefs.{module}.get_viking_fs", lambda: fs)
    agfs = _FakeAgfs()
    tracker = TaskTracker(PersistentTaskStore(agfs))
    monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
    entered_embedding_drain = asyncio.Event()
    original_wait_for_work = tracker.wait_for_work

    async def wait_for_work(*args, **kwargs):
        entered_embedding_drain.set()
        await original_wait_for_work(*args, **kwargs)

    monkeypatch.setattr(tracker, "wait_for_work", wait_for_work)
    owner = _owner_kwargs("acc", "alice")
    source = await tracker.create("content_write", **owner)
    queue = NamedQueue(
        cast(AGFSSyncClientProtocol, agfs),
        "/queue",
        "Embedding",
        middlewares=[TaskWorkQueueMiddleware()],
    )
    enqueued = asyncio.Event()

    async def vectorize(uri, **kwargs):
        await queue.enqueue({"context_data": {"uri": uri, **owner}})
        enqueued.set()
        if fail_generation:
            raise RuntimeError("second embedding enqueue failed")
        return {0, 1}

    processor = _processor()
    processor._generate_overview = AsyncMock(return_value="overview")
    processor._vectorize_directory = vectorize
    ctx = RequestContext(UserIdentifier("acc", "alice"), Role(Role.USER))
    # Create the shared workers under a different Task, as happens across requests.
    with bind_task_context(source.task_id, **owner, enqueued_work=[]):
        await SemanticTreeExecutor(processor, "resource", 1, ctx, skip_vectorization=True).run(
            warm_root
        )
    await tracker.complete(source.task_id, {"ok": True}, **owner)

    plan = None
    if context_type == "resource":
        plan = SemanticPlan(
            root,
            context_type,
            SemanticTreeSnapshot(
                (
                    SemanticTreeEntry(
                        "",
                        "directory",
                        ContentState.UNCHANGED,
                        SemanticAction.AGGREGATE,
                        index_slots=(IndexSlot(0, "root-l0", action=IndexAction.UPSERT),),
                    ),
                )
            ),
        )
    msg = SemanticMsg(
        uri=root,
        context_type=context_type,
        account_id="acc",
        user_id="alice",
        recursive=False,
        propagate_to_parent=False,
        generation_trigger="parent_refresh",
        lock_handoff={"id": "message-lock"},
        plan=plan,
    )
    task = await tracker.create("content_write", **owner) if tracked else None
    task_context = (
        bind_task_context(task.task_id, **owner, enqueued_work=[], work_id="semantic")
        if task
        else nullcontext()
    )
    with task_context:
        worker = asyncio.create_task(processor.on_dequeue({"data": msg.to_json()}))
    try:
        await asyncio.wait_for(enqueued.wait(), 1)
        await asyncio.wait_for(entered_embedding_drain.wait(), 1)
        assert not worker.done()
        assert released == []
        payload = json.loads(agfs.files["/queue/Embedding/enqueue"])
        metadata = extract_task_metadata(payload)
        assert metadata is not None
        assert metadata.task_id != source.task_id
        if task:
            assert metadata.task_id == task.task_id
        await tracker.mark_work_done(
            metadata.task_id,
            metadata.work_id,
            account_id=metadata.account_id,
            user_id=metadata.user_id,
        )
        result = await asyncio.wait_for(worker, 1)
        assert result.outcome is (
            ProcessOutcome.FAILED if fail_generation else ProcessOutcome.SUCCESS
        )
        assert released == ["message-lock"]
        if not tracked:
            completed = await tracker.wait(metadata.task_id, **owner, timeout=0)
            assert completed.status is (
                TaskStatus.FAILED if fail_generation else TaskStatus.COMPLETED
            )
            if fail_generation:
                assert completed.error is not None
                assert "second embedding enqueue failed" in completed.error
        source_result = await tracker.get(source.task_id, **owner)
        assert source_result is not None
        assert source_result.status is TaskStatus.COMPLETED
    finally:
        payload = agfs.files.get("/queue/Embedding/enqueue")
        if payload:
            metadata = extract_task_metadata(json.loads(payload))
            if metadata:
                await tracker.mark_work_done(
                    metadata.task_id,
                    metadata.work_id,
                    account_id=metadata.account_id,
                    user_id=metadata.user_id,
                )
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
