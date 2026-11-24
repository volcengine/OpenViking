# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VikingFS image retrieval scope contract."""

import contextvars
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
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
