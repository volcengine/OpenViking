# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory-context semantic enqueue deduplication (#769)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openviking.pyagfs import get_binding_client
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
        uri=directory_uri,
        context_type="resource",
        generation_trigger="content_write",
    )
    file_work = SemanticMsg(
        uri=directory_uri,
        context_type="resource",
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
