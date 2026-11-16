# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for VikingFS.find retrieval behavior."""

import contextvars
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.retrieve.hierarchical_retriever import RetrieverMode
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.retrieve.types import ContextType, MatchedContext, QueryResult
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)


def _make_viking_fs() -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs.query_embedder = MagicMock(name="embedder")
    fs.rerank_config = None
    fs.retrieval_config = None
    fs.vector_store = MagicMock(name="vector_store")
    fs.acl_manager = None
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx_test", default=None)
    fs._ensure_access = AsyncMock()
    fs._get_vector_store = MagicMock(return_value=fs.vector_store)
    fs._get_embedder = MagicMock(return_value=fs.query_embedder)
    fs._ctx_or_default = MagicMock(return_value=_ctx())
    return fs


@pytest.mark.asyncio
async def test_find_works_without_rerank_config(monkeypatch) -> None:
    fs = _make_viking_fs()
    request_ctx = _ctx()
    captured = {}

    class FakeRetriever:
        def __init__(self, storage, embedder, rerank_config):
            captured["storage"] = storage
            captured["embedder"] = embedder
            captured["rerank_config"] = rerank_config

        async def retrieve(
            self,
            typed_query,
            ctx,
            limit,
            mode,
            score_threshold,
            scope_dsl,
            level,
            events_time_decay_protection=None,
            request_now=None,
            search_type="semantic",
            has_explicit_target=None,
        ):
            captured["typed_query"] = typed_query
            captured["ctx"] = ctx
            captured["limit"] = limit
            captured["mode"] = mode
            captured["score_threshold"] = score_threshold
            captured["scope_dsl"] = scope_dsl
            captured["level"] = level
            captured["search_type"] = search_type
            return QueryResult(
                query=typed_query,
                matched_contexts=[
                    MatchedContext(
                        uri="viking://resources/docs/guide.md",
                        context_type=ContextType.RESOURCE,
                        score=0.9,
                    )
                ],
                searched_directories=["viking://resources/docs"],
            )

    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.HierarchicalRetriever",
        FakeRetriever,
    )

    result = await fs.find(
        "guide",
        target_uri="viking://resources/docs",
        limit=3,
        score_threshold=0.2,
        filter={"category": "doc"},
        ctx=request_ctx,
    )

    assert result.total == 1
    assert [ctx.uri for ctx in result.resources] == ["viking://resources/docs/guide.md"]
    assert captured["storage"] is fs.vector_store
    assert captured["embedder"] is fs.query_embedder
    assert captured["rerank_config"] is None
    assert captured["typed_query"].query == "guide"
    assert captured["typed_query"].context_type is None
    assert captured["typed_query"].target_directories == ["viking://resources/docs"]
    assert captured["ctx"] == fs._ctx_or_default.return_value
    assert captured["limit"] == 3
    assert captured["mode"] == RetrieverMode.QUICK
    assert captured["score_threshold"] == 0.2
    assert captured["scope_dsl"] == {"category": "doc"}
    assert captured["level"] is None
    assert captured["search_type"] == "semantic"
    fs._ensure_access.assert_called_once_with("viking://resources/docs", request_ctx)


@pytest.mark.asyncio
async def test_image_retrieval_preserves_default_and_explicit_scopes(monkeypatch) -> None:
    fs = _make_viking_fs()
    fs.abstract = AsyncMock(return_value="")
    fs.query_embedder.supports_multimodal = True
    fs.query_embedder.prepare_embedding_input.side_effect = lambda content: content
    fs.query_embedder.embed_async = AsyncMock(return_value=EmbedResult(dense_vector=[1.0]))
    proxy = MagicMock()
    proxy.collection_exists_bound = AsyncMock(return_value=True)
    proxy.search_in_tenant = AsyncMock(
        return_value=[
            {"uri": "viking://resources/cat.png", "context_type": "resource", "_score": 0.9}
        ]
    )
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.VikingDBManagerProxy",
        MagicMock(return_value=proxy),
    )
    images = ["data:image/png;base64,YQ==", "data:image/png;base64,Yg=="]

    found = await fs.find("", image_url=images, ctx=_ctx())
    assert [hit.uri for hit in found.resources] == ["viking://resources/cat.png"]
    assert proxy.search_in_tenant.call_args.kwargs["context_type"] == "resource"
    fs.query_embedder.embed_async.assert_awaited_once_with(
        [
            {"type": "image_url", "image_url": {"url": images[0]}},
            {"type": "image_url", "image_url": {"url": images[1]}},
        ],
        is_query=True,
    )

    searched = await fs.search("", image_url=images, ctx=_ctx())
    assert [hit.uri for hit in searched.resources] == ["viking://resources/cat.png"]
    assert proxy.search_in_tenant.call_args.kwargs["context_type"] == "resource"

    # An explicit memory URI or type filter overrides the image resource default.
    proxy.search_in_tenant.return_value = [
        {"uri": "viking://user/user1/memories/cat.md", "context_type": "memory", "_score": 0.9}
    ]
    found = await fs.find(
        "", image_url=images, target_uri="viking://user/user1/memories", ctx=_ctx()
    )
    assert [hit.uri for hit in found.memories] == ["viking://user/user1/memories/cat.md"]
    assert proxy.search_in_tenant.call_args.kwargs["context_type"] is None
    assert proxy.search_in_tenant.call_args.kwargs["target_directories"] == [
        "viking://user/user1/memories"
    ]

    scope = {"op": "must", "field": "context_type", "conds": ["memory"]}
    searched = await fs.search("cat", image_url=images, filter=scope, ctx=_ctx())
    assert [hit.uri for hit in searched.memories] == ["viking://user/user1/memories/cat.md"]
    assert proxy.search_in_tenant.call_args.kwargs["context_type"] is None
    assert proxy.search_in_tenant.call_args.kwargs["extra_filter"] == scope


@pytest.mark.asyncio
async def test_find_uses_quick_mode_with_rerank_config(monkeypatch) -> None:
    fs = _make_viking_fs()
    fs.rerank_config = MagicMock(name="rerank_config")
    captured = {}

    class FakeRetriever:
        def __init__(self, storage, embedder, rerank_config):
            captured["rerank_config"] = rerank_config

        async def retrieve(self, typed_query, **kwargs):
            captured["mode"] = kwargs.get("mode")
            return QueryResult(
                query=typed_query,
                matched_contexts=[],
                searched_directories=["viking://resources/docs"],
            )

    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.HierarchicalRetriever",
        FakeRetriever,
    )

    result = await fs.find(
        "guide",
        target_uri="viking://resources/docs",
        ctx=_ctx(),
    )

    assert result.total == 0
    assert captured["rerank_config"] is fs.rerank_config
    assert captured["mode"] == RetrieverMode.QUICK
