"""Coalescing follows committed queue work, including retries and cancellation."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs import get_binding_client
from openviking.storage.queuefs.queue_middleware import QueueMiddleware
from openviking.storage.queuefs.semantic_msg import SemanticMsg, build_semantic_coalesce_key
from openviking.storage.queuefs.semantic_queue import (
    SemanticQueue,
    is_semantic_msg_stale,
    semantic_revision_key,
)


@pytest.fixture
def queue(tmp_path):
    client_class, _ = get_binding_client()
    client = client_class(
        config={
            "service_name": "semantic-revisions",
            "coordination": {
                "provider": "sqlite",
                "sqlite_path": str(tmp_path / "coordination.db"),
            },
        }
    )
    client.mount("queuefs", "/queue", {"backend": "memory"})
    yield SemanticQueue(client, "/queue", "semantic")
    client.close()


def message():
    uri = "viking://resources/docs"
    return SemanticMsg(
        uri=uri,
        context_type="resource",
        coalesce_key=build_semantic_coalesce_key(context_type="resource", uri=uri),
    )


async def latest(queue, msg):
    return await queue._async_agfs.revision_latest(semantic_revision_key(msg.coalesce_key))


@pytest.mark.asyncio
async def test_failed_enqueue_never_supersedes_persisted_work(queue, monkeypatch):
    first, failed, newer = message(), message(), message()
    await queue.enqueue(first)
    with monkeypatch.context() as patch:
        patch.setattr(queue._async_agfs, "write", AsyncMock(side_effect=OSError("disk full")))
        with pytest.raises(OSError, match="disk full"):
            await queue.enqueue(failed)
    assert await latest(queue, first) == first.coalesce_version == 1
    assert not await is_semantic_msg_stale(first, agfs=queue._async_agfs)
    await queue.enqueue(newer)
    assert newer.coalesce_version == 3
    retry = SemanticMsg.from_dict(failed.to_dict())
    await queue.enqueue(retry, deduplicate_memory=False)
    assert retry.coalesce_version == 2
    assert await latest(queue, retry) == 3
    assert await is_semantic_msg_stale(retry, agfs=queue._async_agfs)


@pytest.mark.asyncio
async def test_out_of_order_persistence_cannot_regress_publication(queue, monkeypatch):
    await queue._ensure_initialized()
    first, second = message(), message()
    entered, finish = asyncio.Event(), asyncio.Event()
    write = queue._async_agfs.write

    async def delayed_write(path, payload):
        if json.loads(payload)["coalesce_version"] == 1:
            entered.set()
            await finish.wait()
        return await write(path, payload)

    monkeypatch.setattr(queue._async_agfs, "write", delayed_write)
    pending = asyncio.create_task(queue.enqueue(first))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await queue.enqueue(second)
        assert await latest(queue, second) == 2
    finally:
        finish.set()
        await pending
    assert await latest(queue, first) == 2
    assert await is_semantic_msg_stale(first, agfs=queue._async_agfs)


@pytest.mark.asyncio
async def test_publication_and_lookup_failure_preserve_work(queue, monkeypatch):
    first, second = message(), message()
    await queue.enqueue(first)
    with monkeypatch.context() as patch:
        patch.setattr(
            queue._async_agfs, "revision_publish", AsyncMock(side_effect=OSError("offline"))
        )
        assert await queue.enqueue(second)
    assert await latest(queue, first) == 1
    assert not await is_semantic_msg_stale(first, agfs=queue._async_agfs)
    assert (await queue.dequeue()).id == first.id
    assert (await queue.dequeue()).id == second.id
    monkeypatch.setattr(
        queue._async_agfs, "revision_latest", AsyncMock(side_effect=OSError("offline"))
    )
    assert not await is_semantic_msg_stale(first, agfs=queue._async_agfs)


@pytest.mark.asyncio
async def test_post_commit_middleware_failure_keeps_publication(queue):
    class FailAfterCommit(QueueMiddleware):
        async def enqueue(self, ctx, call_next):
            await call_next(ctx)
            assert ctx.committed
            raise RuntimeError("post-commit failure")

    queue = SemanticQueue(queue._agfs, "/queue", "semantic", middlewares=[FailAfterCommit()])
    msg = message()
    with pytest.raises(RuntimeError, match="post-commit"):
        await queue.enqueue(msg)
    assert await latest(queue, msg) == 1
    assert (await queue.dequeue()).id == msg.id


@pytest.mark.asyncio
async def test_cancellation_settles_persistence_and_publication(queue, monkeypatch):
    await queue._ensure_initialized()
    msg = message()
    persisted, finish = asyncio.Event(), asyncio.Event()
    write = queue._async_agfs.write

    async def delayed_receipt(path, payload):
        receipt = await write(path, payload)
        persisted.set()
        await finish.wait()
        return receipt

    monkeypatch.setattr(queue._async_agfs, "write", delayed_receipt)
    pending = asyncio.create_task(queue.enqueue(msg))
    await asyncio.wait_for(persisted.wait(), 2)
    pending.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert await latest(queue, msg) == 1
    assert (await queue.dequeue()).id == msg.id
