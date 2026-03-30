# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression test for VikingFS.find without rerank configuration."""

import contextvars
from unittest.mock import MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.retrieve.types import ContextType, MatchedContext, QueryResult
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)


def _make_viking_fs() -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs.query_embedder = MagicMock(name="embedder")
    fs.rerank_config = None
    fs.vector_store = MagicMock(name="vector_store")
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx_test", default=None)
    fs._ensure_access = MagicMock()
    fs._get_vector_store = MagicMock(return_value=fs.vector_store)
    fs._get_embedder = MagicMock(return_value=fs.query_embedder)
    fs._infer_context_type = MagicMock(return_value=ContextType.RESOURCE)
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

        async def retrieve(self, typed_query, ctx, limit, score_threshold, scope_dsl):
            captured["typed_query"] = typed_query
            captured["ctx"] = ctx
            captured["limit"] = limit
            captured["score_threshold"] = score_threshold
            captured["scope_dsl"] = scope_dsl
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
    assert captured["typed_query"].context_type == ContextType.RESOURCE
    assert captured["typed_query"].target_directories == ["viking://resources/docs"]
    assert captured["ctx"] == fs._ctx_or_default.return_value
    assert captured["limit"] == 3
    assert captured["score_threshold"] == 0.2
    assert captured["scope_dsl"] == {"category": "doc"}
    fs._ensure_access.assert_called_once_with("viking://resources/docs", request_ctx)
