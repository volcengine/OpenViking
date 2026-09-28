# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_executor import SemanticTreeStats
from openviking.storage.queuefs.semantic_lock import SemanticLockScope
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.queuefs.semantic_work import SemanticMessageWork


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
async def test_durable_plan_waits_for_embeddings_before_releasing_handoff_lock(monkeypatch):
    from openviking.storage.context_update_plan import (
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )

    events = []
    pathlock = _FakePathLock()
    lease = {"id": "durable-plan-lock"}
    plan = SemanticPlan(
        "viking://resources/demo",
        "resource",
        SemanticTreeSnapshot((SemanticTreeEntry("", "directory", "unchanged", "aggregate"),)),
    )
    msg = SemanticMsg(
        uri=plan.root_uri,
        context_type="resource",
        telemetry_id="durable-plan",
        lock_handoff={"owner_id": "producer"},
        plan=plan,
    )

    class Tracker:
        async def wait_for_embeddings(self, telemetry_id, **kwargs):
            assert telemetry_id == msg.telemetry_id
            events.append("embeddings-settled")

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_work.get_request_wait_tracker", lambda: Tracker()
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_lock.get_viking_fs",
        lambda: _FakeVikingFS(pathlock),
    )

    work = SemanticMessageWork(SimpleNamespace(), msg, caller_lock=None)
    work.scope = SemanticLockScope(lease, _owned=True)

    await work.finish_processing(True)

    assert events == ["embeddings-settled"]
    assert pathlock.release_calls == ["durable-plan-lock"]


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
