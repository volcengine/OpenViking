# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import asyncio
from typing import List

import pytest

from openviking.models.embedder.base import (
    EmbedderBase,
    EmbedResult,
    QueryEmbeddingCache,
    embed_compat,
    query_embed_cache_scope,
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
    query_embed_cache_var.set(QueryEmbeddingCache())

    first = await embed_compat(embedder, "hello", is_query=True)
    second = await embed_compat(embedder, "hello", is_query=True)

    assert first.dense_vector == second.dense_vector == [1.0]
    assert embedder.calls == ["hello"]


async def test_embed_compat_caches_distinct_texts_separately():
    embedder = CountingEmbedder()
    query_embed_cache_var.set(QueryEmbeddingCache())

    await embed_compat(embedder, "alpha", is_query=True)
    await embed_compat(embedder, "beta", is_query=True)

    assert embedder.calls == ["alpha", "beta"]


async def test_embed_compat_dedupes_concurrent_same_text():
    # Mirrors gather.py: all finds for one request run as sibling tasks that
    # copy the request context, so the cache dict must be shared by reference
    # and the first find's in-flight embed is awaited by every sibling.
    embedder = CountingEmbedder()
    query_embed_cache_var.set(QueryEmbeddingCache())

    async def one():
        return await embed_compat(embedder, "shared", is_query=True)

    results = await asyncio.gather(*(one() for _ in range(5)))

    assert len(results) == 5
    assert all(r.dense_vector == [1.0] for r in results)
    assert embedder.calls == ["shared"]


async def test_embed_compat_never_caches_resource_embeds():
    embedder = CountingEmbedder()
    query_embed_cache_var.set(QueryEmbeddingCache())

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
    query_embed_cache_var.set(QueryEmbeddingCache())

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
    async with query_embed_cache_scope():
        assert query_embed_cache_var.get() is not None
    assert query_embed_cache_var.get() is None


async def test_query_embed_cache_scope_resets_on_exception():
    with pytest.raises(RuntimeError):
        async with query_embed_cache_scope():
            raise RuntimeError("boom")
    assert query_embed_cache_var.get() is None


async def test_account_query_cache_isolated_by_account_and_config(monkeypatch):
    from openviking.config.binding import manager_over_source
    from openviking.config.embedding import AccountEmbeddingProvider
    from openviking.config.source import MemoryConfigSource
    from openviking.config.vector import AccountVectorConfigResolver
    from openviking_cli.utils.config import set_openviking_config
    from openviking_cli.utils.config.embedding_config import EmbeddingConfig
    from openviking_cli.utils.config.open_viking_config import (
        OpenVikingConfig,
        OpenVikingConfigSingleton,
    )

    clients = []

    def create(config):
        client = CountingEmbedder(config.dense.model)
        clients.append(client)
        return client

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", create)
    base = OpenVikingConfig.from_dict(
        {
            "embedding": {
                "dense": {
                    "model": "shared-name",
                    "dimension": 1,
                    "provider": "openai",
                    "api_key": "cluster-key",
                }
            }
        }
    )
    set_openviking_config(base)
    manager = manager_over_source(MemoryConfigSource(), base_config=base)
    provider = AccountEmbeddingProvider(AccountVectorConfigResolver(manager), manager)
    try:
        await manager.initialize()
        await manager.patch_account(
            "a",
            {
                "embedding": {
                    "dense": {
                        "model": "shared-name",
                        "dimension": 1,
                        "credentials": [{"provider": "openai", "api_key": "a-key"}],
                    }
                }
            },
            creating=True,
        )
        first, second = provider.bind("a"), provider.bind("b")
        async with query_embed_cache_scope():
            results = await asyncio.gather(
                embed_compat(first, "same-text", is_query=True),
                embed_compat(provider.bind("a"), "same-text", is_query=True),
            )
            assert all(result.dense_vector == [1.0] for result in results)
            await embed_compat(second, "same-text", is_query=True)
            assert [client.calls for client in clients] == [["same-text"], ["same-text"]]

            await manager.patch_account(
                "a",
                {
                    "embedding": {
                        "dense": {"credentials": [{"provider": "openai", "api_key": "rotated-key"}]}
                    }
                },
            )
            await embed_compat(first, "same-text", is_query=True)
            await embed_compat(second, "same-text", is_query=True)
            assert [client.calls for client in clients] == [
                ["same-text"],
                ["same-text"],
                ["same-text"],
            ]
    finally:
        await provider.close()
        OpenVikingConfigSingleton.reset_instance()


async def test_one_waiter_cancellation_leaves_shared_embed_running():
    # One cancelled sibling must not affect another waiter on the same query.
    embedder = CountingEmbedder(delay=0.1)
    query_embed_cache_var.set(QueryEmbeddingCache())

    waiter = asyncio.create_task(embed_compat(embedder, "shared", is_query=True))
    await asyncio.sleep(0.01)
    survivor = asyncio.create_task(embed_compat(embedder, "shared", is_query=True))
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    result = await survivor
    assert embedder.calls == ["shared"]
    assert result.dense_vector == [1.0]


async def test_cancelled_shared_embed_is_evicted_and_retried():
    # When the shared task itself is cancelled, waiters must observe the
    # cancellation, the poisoned key must be evicted, and the next embed of the
    # same text must start a fresh task instead of awaiting the dead one.
    embedder = CountingEmbedder(delay=0.1)
    query_embed_cache_var.set(QueryEmbeddingCache())

    waiter = asyncio.create_task(embed_compat(embedder, "shared", is_query=True))
    await asyncio.sleep(0.01)  # let the waiter create and await the shared task
    cache = query_embed_cache_var.get()
    assert cache is not None
    next(iter(cache.values())).task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert cache == {}

    result = await embed_compat(embedder, "shared", is_query=True)
    assert result.dense_vector == [1.0]
    assert embedder.calls == ["shared", "shared"]


async def test_last_waiter_cancellation_stops_shared_embed_and_evicts_entry():
    # The cache owns shared work, so cancelling its last waiter also cancels
    # that work instead of leaving an orphaned API call in the request scope.
    embedder = CountingEmbedder(delay=0.1)
    query_embed_cache_var.set(QueryEmbeddingCache())

    waiter = asyncio.create_task(embed_compat(embedder, "shared", is_query=True))
    await asyncio.sleep(0.01)  # let the waiter create and await the shared task
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    await asyncio.sleep(0)
    assert query_embed_cache_var.get() == {}

    result = await embed_compat(embedder, "shared", is_query=True)
    assert result.dense_vector == [1.0]
    assert embedder.calls == ["shared", "shared"]
