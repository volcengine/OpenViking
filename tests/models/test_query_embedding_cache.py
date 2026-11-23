# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from openviking.models.embedder.base import (
    EmbedderBase,
    EmbedResult,
    embed_compat,
    query_embed_cache_scope,
)


class CountingEmbedder(EmbedderBase):
    def __init__(self):
        super().__init__("test-embedder")
        self.calls: list[str] = []

    def embed(self, content, is_query: bool = False) -> EmbedResult:
        self.calls.append(content)
        return EmbedResult(dense_vector=[float(len(self.calls))])

    async def embed_async(self, content, is_query: bool = False) -> EmbedResult:
        return self.embed(content, is_query=is_query)


class BlockingEmbedder(CountingEmbedder):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def embed_async(self, content, is_query: bool = False) -> EmbedResult:
        result = self.embed(content, is_query=is_query)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return result


async def test_same_query_is_reused_but_distinct_text_and_next_request_are_not():
    embedder = CountingEmbedder()
    async with query_embed_cache_scope():
        assert (await embed_compat(embedder, "alpha", is_query=True)).dense_vector == [1.0]
        assert (await embed_compat(embedder, "beta", is_query=True)).dense_vector == [2.0]
        assert (await embed_compat(embedder, "alpha", is_query=True)).dense_vector == [1.0]

    assert (await embed_compat(embedder, "alpha", is_query=True)).dense_vector == [3.0]
    assert embedder.calls == ["alpha", "beta", "alpha"]


async def test_embed_compat_never_caches_resource_embeds():
    embedder = CountingEmbedder()
    async with query_embed_cache_scope():
        assert (await embed_compat(embedder, "shared", is_query=True)).dense_vector == [1.0]
        assert (await embed_compat(embedder, "shared")).dense_vector == [2.0]
        assert (await embed_compat(embedder, "shared")).dense_vector == [3.0]


@pytest.mark.parametrize("failure", [RuntimeError("embed failed"), asyncio.CancelledError()])
async def test_failed_embed_is_retried_within_the_same_request(monkeypatch, failure):
    embedder = CountingEmbedder()
    provider = AsyncMock(side_effect=[failure, EmbedResult(dense_vector=[1.0])])
    monkeypatch.setattr(embedder, "embed_async", provider)

    async with query_embed_cache_scope():
        with pytest.raises(type(failure)):
            await embed_compat(embedder, "query", is_query=True)
        assert (await embed_compat(embedder, "query", is_query=True)).dense_vector == [1.0]
        assert provider.await_count == 2


async def test_an_aborted_request_does_not_leave_its_query_cached():
    embedder = CountingEmbedder()
    with pytest.raises(RuntimeError, match="request aborted"):
        async with query_embed_cache_scope():
            assert (await embed_compat(embedder, "alpha", is_query=True)).dense_vector == [1.0]
            raise RuntimeError("request aborted")
    assert (await embed_compat(embedder, "alpha", is_query=True)).dense_vector == [2.0]


async def test_account_query_cache_isolated_by_account_and_config(mock_openai_transport):
    from openviking.config.binding import manager_over_source
    from openviking.config.embedding import AccountEmbeddingProvider
    from openviking.config.source import MemoryConfigSource
    from openviking.config.vector import AccountVectorConfigResolver
    from openviking_cli.utils.config import set_openviking_config
    from openviking_cli.utils.config.open_viking_config import (
        OpenVikingConfig,
        OpenVikingConfigSingleton,
    )

    requests = []

    def respond(request):
        assert request.url.path == "/v1/embeddings"
        assert json.loads(request.content)["input"] == "same-text"
        requests.append(request.headers["Authorization"])
        return httpx.Response(
            200,
            request=request,
            json={
                "object": "list",
                "model": "shared-name",
                "data": [{"object": "embedding", "index": 0, "embedding": [float(len(requests))]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    mock_openai_transport(respond)
    base = OpenVikingConfig.from_dict(
        {
            "embedding": {
                "dense": {
                    "model": "shared-name",
                    "dimension": 1,
                    "provider": "openai",
                    "api_key": "cluster-key",
                    "api_base": "https://provider.example.test/v1",
                }
            }
        }
    )
    set_openviking_config(base)
    manager = manager_over_source(MemoryConfigSource(), base_config=base)
    provider = AccountEmbeddingProvider(AccountVectorConfigResolver(manager), manager)
    try:
        await manager.initialize()
        for account in ("a", "b"):
            await manager.patch_account(
                account,
                {
                    "embedding": {
                        "dense": {
                            "model": "shared-name",
                            "dimension": 1,
                            "credentials": [{"provider": "openai", "api_key": "cluster-key"}],
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
            assert [result.dense_vector for result in results] == [[1.0], [1.0]]
            assert (await embed_compat(second, "same-text", is_query=True)).dense_vector == [2.0]
            # Both accounts still use the same configuration and provider key.
            assert requests == ["Bearer cluster-key", "Bearer cluster-key"]

            await manager.patch_account(
                "a",
                {
                    "embedding": {
                        "dense": {
                            "credentials": [{"provider": "openai", "api_key": "rotated-key"}],
                        }
                    }
                },
            )
            assert (await embed_compat(first, "same-text", is_query=True)).dense_vector == [3.0]
            assert (await embed_compat(second, "same-text", is_query=True)).dense_vector == [2.0]
            assert requests == ["Bearer cluster-key", "Bearer cluster-key", "Bearer rotated-key"]
    finally:
        await provider.close()
        OpenVikingConfigSingleton.reset_instance()


async def test_one_waiter_cancellation_leaves_shared_embed_running():
    embedder = BlockingEmbedder()
    sibling_entered = asyncio.Event()

    async def sibling():
        sibling_entered.set()
        return await embed_compat(embedder, "shared", is_query=True)

    async with query_embed_cache_scope():
        waiter = asyncio.create_task(embed_compat(embedder, "shared", is_query=True))
        survivor = asyncio.create_task(sibling())
        try:
            await asyncio.wait_for(embedder.started.wait(), timeout=2)
            await asyncio.wait_for(sibling_entered.wait(), timeout=2)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(waiter, timeout=2)
            embedder.release.set()
            assert (await asyncio.wait_for(survivor, timeout=2)).dense_vector == [1.0]
        finally:
            for task in (waiter, survivor):
                task.cancel()
            await asyncio.gather(waiter, survivor, return_exceptions=True)
    assert embedder.calls == ["shared"]
    assert not embedder.cancelled.is_set()


async def test_last_waiter_cancellation_stops_shared_embed_and_evicts_entry():
    embedder = BlockingEmbedder()
    async with query_embed_cache_scope():
        waiter = asyncio.create_task(embed_compat(embedder, "shared", is_query=True))
        try:
            await asyncio.wait_for(embedder.started.wait(), timeout=2)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(waiter, timeout=2)
            await asyncio.wait_for(embedder.cancelled.wait(), timeout=2)
            embedder.release.set()
            result = await asyncio.wait_for(
                embed_compat(embedder, "shared", is_query=True), timeout=2
            )
            assert result.dense_vector == [2.0]
            assert embedder.calls == ["shared", "shared"]
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
