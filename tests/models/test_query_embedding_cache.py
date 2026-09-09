# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import asyncio
from typing import List

import pytest

from openviking.models.embedder.base import (
    EmbedderBase,
    EmbedResult,
    embed_compat,
    query_embed_cache_var,
)


class CountingEmbedder(EmbedderBase):
    """Fake embedder that records every async embed and can be told to fail."""

    def __init__(self, model_name: str = "fake-model", delay: float = 0):
        super().__init__(model_name)
        self.calls: List[str] = []
        self.fail_next = 0
        self.delay = delay

    def embed(self, content, is_query: bool = False) -> EmbedResult:
        self.calls.append(str(content))
        return EmbedResult(dense_vector=[1.0])

    async def embed_async(self, content, is_query: bool = False) -> EmbedResult:
        self.calls.append(str(content))
        if self.delay:
            await asyncio.sleep(self.delay)
        else:
            await asyncio.sleep(0)
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("embed failed")
        return EmbedResult(dense_vector=[1.0])


async def test_embed_compat_reuses_same_query_text_within_request():
    embedder = CountingEmbedder()
    query_embed_cache_var.set({})

    first = await embed_compat(embedder, "hello", is_query=True)
    second = await embed_compat(embedder, "hello", is_query=True)

    assert first.dense_vector == second.dense_vector == [1.0]
    assert embedder.calls == ["hello"]


async def test_embed_compat_caches_distinct_texts_separately():
    embedder = CountingEmbedder()
    query_embed_cache_var.set({})

    await embed_compat(embedder, "alpha", is_query=True)
    await embed_compat(embedder, "beta", is_query=True)

    assert embedder.calls == ["alpha", "beta"]


async def test_embed_compat_dedupes_concurrent_same_text():
    # Mirrors gather.py: all finds for one request run as sibling tasks that
    # copy the request context, so the cache dict must be shared by reference
    # and the first find's in-flight embed is awaited by every sibling.
    embedder = CountingEmbedder()
    query_embed_cache_var.set({})

    async def one():
        return await embed_compat(embedder, "shared", is_query=True)

    results = await asyncio.gather(*(one() for _ in range(5)))

    assert len(results) == 5
    assert all(r.dense_vector == [1.0] for r in results)
    assert embedder.calls == ["shared"]


async def test_embed_compat_never_caches_resource_embeds():
    embedder = CountingEmbedder()
    query_embed_cache_var.set({})

    await embed_compat(embedder, "hello", is_query=False)
    await embed_compat(embedder, "hello", is_query=False)

    assert embedder.calls == ["hello", "hello"]


async def test_embed_compat_does_not_cache_outside_request_scope():
    # Without the request scope installed (no handler set), behavior must stay
    # exactly as today: every embed goes through.
    embedder = CountingEmbedder()

    await embed_compat(embedder, "hello", is_query=True)
    await embed_compat(embedder, "hello", is_query=True)

    assert embedder.calls == ["hello", "hello"]


async def test_embed_compat_retries_after_a_failed_embed():
    embedder = CountingEmbedder()
    embedder.fail_next = 1
    query_embed_cache_var.set({})

    failed = False
    try:
        await embed_compat(embedder, "hello", is_query=True)
    except RuntimeError:
        failed = True
    assert failed

    result = await embed_compat(embedder, "hello", is_query=True)
    assert result.dense_vector == [1.0]
    assert embedder.calls == ["hello", "hello"]


async def test_query_embed_cache_scope_resets_after_exit():
    from openviking.models.embedder.base import query_embed_cache_scope

    with query_embed_cache_scope():
        assert query_embed_cache_var.get() is not None
    assert query_embed_cache_var.get() is None


async def test_query_embed_cache_scope_resets_on_exception():
    from openviking.models.embedder.base import query_embed_cache_scope

    with pytest.raises(RuntimeError):
        with query_embed_cache_scope():
            raise RuntimeError("boom")
    assert query_embed_cache_var.get() is None


async def test_embedders_with_same_model_name_do_not_share_entries():
    # Keying by embedder identity keeps two embedders that happen to share a
    # model_name from serving each other's vectors.
    first = CountingEmbedder(model_name="shared-name")
    second = CountingEmbedder(model_name="shared-name")
    query_embed_cache_var.set({})

    await embed_compat(first, "same-text", is_query=True)
    await embed_compat(second, "same-text", is_query=True)

    assert first.calls == ["same-text"]
    assert second.calls == ["same-text"]


async def test_waiter_cancellation_leaves_shared_embed_running():
    # Cancelling a waiter must not cancel the shared in-flight embed (shield),
    # and the cache entry must stay usable for later waiters.
    embedder = CountingEmbedder(delay=0.1)
    query_embed_cache_var.set({})

    waiter = asyncio.create_task(embed_compat(embedder, "shared", is_query=True))
    await asyncio.sleep(0.01)  # let the waiter create and await the shared task
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    # The shared embed survived the waiter cancellation and completed.
    await asyncio.sleep(0.15)
    assert embedder.calls == ["shared"]

    result = await embed_compat(embedder, "shared", is_query=True)
    assert result.dense_vector == [1.0]
    assert embedder.calls == ["shared"]  # served from cache, no second embed


async def test_cancelled_shared_embed_is_evicted_and_retried():
    # When the shared task itself is cancelled, waiters must observe the
    # cancellation, the poisoned key must be evicted, and the next embed of the
    # same text must start a fresh task instead of awaiting the dead one.
    embedder = CountingEmbedder(delay=0.1)
    query_embed_cache_var.set({})

    waiter = asyncio.create_task(embed_compat(embedder, "shared", is_query=True))
    await asyncio.sleep(0.01)  # let the waiter create and await the shared task
    cache = query_embed_cache_var.get()
    assert cache is not None
    next(iter(cache.values())).cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert cache == {}

    result = await embed_compat(embedder, "shared", is_query=True)
    assert result.dense_vector == [1.0]
    assert embedder.calls == ["shared", "shared"]


async def test_waiter_cancelled_then_shared_embed_failure_evicts_entry():
    # The last waiter is cancelled while the shared embed still runs; when that
    # orphaned embed then fails, the stale entry must be evicted so the next
    # embed of the same text starts fresh instead of replaying the old failure.
    embedder = CountingEmbedder(delay=0.1)
    embedder.fail_next = 1
    query_embed_cache_var.set({})

    waiter = asyncio.create_task(embed_compat(embedder, "shared", is_query=True))
    await asyncio.sleep(0.01)  # let the waiter create and await the shared task
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    # Let the orphaned shared embed run to failure; the done callback must
    # have evicted the failed entry.
    await asyncio.sleep(0.2)
    assert query_embed_cache_var.get() == {}

    result = await embed_compat(embedder, "shared", is_query=True)
    assert result.dense_vector == [1.0]
    assert embedder.calls == ["shared", "shared"]
