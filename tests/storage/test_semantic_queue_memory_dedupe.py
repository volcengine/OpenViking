# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for semantic retries and shared directory refresh scheduling."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openviking.pyagfs import get_binding_client
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking.storage.queuefs.semantic_msg import SemanticMsg, build_semantic_coalesce_key
from openviking.storage.queuefs.semantic_ops.directory_refresh import enqueue_directory_refresh
from openviking.storage.queuefs.semantic_ops.freshness_policy import (
    FreshnessAction,
    FreshnessDecision,
)
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.queuefs.semantic_queue import SemanticQueue, is_semantic_msg_stale
from tests.test_task_tracker import _FakeAgfs


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
async def test_worker_without_lock_reenqueues_memory_work(monkeypatch, native_queue):
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
    assert await native_queue.enqueue(msg) == "deduplicated"
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
    monkeypatch.setattr(
        "openviking.service.task_tracker._instance",
        TaskTracker(PersistentTaskStore(_FakeAgfs())),
    )
    directory_uri = f"viking://resources/docs/{uuid4().hex}"
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: SimpleNamespace(SEMANTIC="semantic", get_queue=lambda *a, **kw: q),
    )
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_ops.directory_refresh.plan_abstract_overview_refresh",
        AsyncMock(
            side_effect=[
                FreshnessDecision(FreshnessAction.MARK_PENDING, 2, 100),
                FreshnessDecision(FreshnessAction.REFRESH_NOW, 10, 100),
                FreshnessDecision(FreshnessAction.REFRESH_NOW, 11, 100),
            ]
        ),
    )
    source = SemanticMsg(
        uri=directory_uri,
        context_type="resource",
        generation_trigger="resource_ingest",
    )
    file_work = SemanticMsg(
        uri=directory_uri,
        context_type="resource",
        coalesce_key=build_semantic_coalesce_key(context_type="resource", uri=directory_uri),
    )
    await q.enqueue(file_work)
    processor = _processor()
    ctx = processor._ctx_from_semantic_msg(source)
    assert await enqueue_directory_refresh(directory_uri, 2, ctx=ctx) == (
        FreshnessAction.MARK_PENDING,
        None,
    )
    await q.dequeue()  # File work has its own key.
    assert await q.dequeue() is None

    await enqueue_directory_refresh(directory_uri, 8, ctx=ctx)
    await processor._enqueue_parent_refresh(
        source, f"{directory_uri}/imported", l0_body_changed=True
    )
    first, second = await q.dequeue(), await q.dequeue()
    assert first.coalesce_key == second.coalesce_key != ""
    assert first.coalesce_version < second.coalesce_version
    assert await is_semantic_msg_stale(first, agfs=q._async_agfs)
    assert not await is_semantic_msg_stale(second, agfs=q._async_agfs)
    assert not await is_semantic_msg_stale(file_work, agfs=q._async_agfs)
