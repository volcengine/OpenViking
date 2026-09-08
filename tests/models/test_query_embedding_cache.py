# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import asyncio
from typing import List

from openviking.models.embedder.base import (
    EmbedResult,
    EmbedderBase,
    embed_compat,
    query_embed_cache_var,
)


class CountingEmbedder(EmbedderBase):
    """Fake embedder that records every async embed and can be told to fail."""

    def __init__(self, model_name: str = "fake-model"):
        super().__init__(model_name)
        self.calls: List[str] = []
        self.fail_next = 0

    def embed(self, content, is_query: bool = False) -> EmbedResult:
        self.calls.append(str(content))
        return EmbedResult(dense_vector=[1.0])

    async def embed_async(self, content, is_query: bool = False) -> EmbedResult:
        self.calls.append(str(content))
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
