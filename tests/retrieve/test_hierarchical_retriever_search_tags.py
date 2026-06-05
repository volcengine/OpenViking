# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.server.identity import RequestContext, Role
from openviking_cli.retrieve.types import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier


class DummyStorage:
    def __init__(self) -> None:
        self.collection_name = "context"
        self.global_search_calls = []
        self.tag_search_calls = []
        self.child_search_calls = []

    async def collection_exists_bound(self) -> bool:
        return True

    async def search_global_roots_in_tenant(self, ctx, **kwargs):
        self.global_search_calls.append({"ctx": ctx, **kwargs})
        return []

    async def search_by_tags_in_tenant(self, ctx, **kwargs):
        self.tag_search_calls.append({"ctx": ctx, **kwargs})
        return [
            {
                "uri": "viking://resources/finance",
                "abstract": "finance folder",
                "_score": 0.6,
                "level": 1,
                "context_type": "resource",
                "search_tags": ["user:finance"],
            }
        ]

    async def search_children_in_tenant(self, ctx, parent_uri: str, **kwargs):
        self.child_search_calls.append({"ctx": ctx, "parent_uri": parent_uri, **kwargs})
        return []


@pytest.mark.asyncio
async def test_retrieve_passes_expanded_tags_to_global_and_tag_search():
    storage = DummyStorage()
    retriever = HierarchicalRetriever(storage=storage, embedder=None, rerank_config=None)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)

    query = TypedQuery(
        query="finance docs",
        context_type=ContextType.RESOURCE,
        intent="",
        tags=["user:finance", "auto:finance"],
    )

    await retriever.retrieve(query, ctx=ctx, limit=3)

    assert storage.global_search_calls[0]["search_tags"] == ["user:finance", "auto:finance"]
    assert storage.tag_search_calls[0]["search_tags"] == ["user:finance", "auto:finance"]
    assert storage.child_search_calls[0]["search_tags"] == ["user:finance", "auto:finance"]
