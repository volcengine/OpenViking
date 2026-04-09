# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Hierarchical retriever tag expansion tests."""

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.server.identity import RequestContext, Role
from openviking_cli.retrieve.types import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier


class DummyEmbedder:
    def embed(self, _text: str, is_query: bool = False) -> EmbedResult:
        return EmbedResult(dense_vector=[0.1, 0.2, 0.3])


class TagExpansionStorage:
    def __init__(self) -> None:
        self.collection_name = "context"
        self.global_search_calls = []
        self.tag_search_calls = []
        self.child_search_calls = []

    async def collection_exists_bound(self) -> bool:
        return True

    async def search_global_roots_in_tenant(
        self,
        ctx,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        tags=None,
        extra_filter=None,
        limit: int = 10,
    ):
        self.global_search_calls.append(
            {
                "ctx": ctx,
                "context_type": context_type,
                "target_directories": target_directories,
                "tags": tags,
                "extra_filter": extra_filter,
                "limit": limit,
            }
        )
        return [
            {
                "uri": "viking://resources/machine-learning",
                "parent_uri": "viking://resources",
                "abstract": "Machine learning notes",
                "_score": 0.9,
                "level": 1,
                "context_type": "resource",
                "tags": ["auto:model-training"],
            }
        ]

    async def search_by_tags_in_tenant(
        self,
        ctx,
        tags,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        levels=None,
        limit: int = 10,
    ):
        self.tag_search_calls.append(
            {
                "ctx": ctx,
                "tags": tags,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "levels": levels,
                "limit": limit,
            }
        )
        return [
            {
                "uri": "viking://resources/data-engineering/feature-store.md",
                "parent_uri": "viking://resources/data-engineering",
                "abstract": "Feature store guidance for model training",
                "level": 2,
                "context_type": "resource",
                "tags": ["auto:model-training", "auto:feature-store"],
            }
        ]

    async def search_children_in_tenant(
        self,
        ctx,
        parent_uri: str,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        self.child_search_calls.append(parent_uri)
        if parent_uri == "viking://resources/data-engineering":
            return [
                {
                    "uri": "viking://resources/data-engineering/feature-store.md",
                    "parent_uri": "viking://resources/data-engineering",
                    "abstract": "Feature store guidance for model training",
                    "_score": 0.95,
                    "level": 2,
                    "context_type": "resource",
                    "tags": ["auto:model-training", "auto:feature-store"],
                }
            ]
        return []


class ExplicitTagStorage(TagExpansionStorage):
    async def search_global_roots_in_tenant(
        self,
        ctx,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        tags=None,
        extra_filter=None,
        limit: int = 10,
    ):
        self.global_search_calls.append(
            {
                "ctx": ctx,
                "context_type": context_type,
                "target_directories": target_directories,
                "tags": tags,
                "extra_filter": extra_filter,
                "limit": limit,
            }
        )
        return []

    async def search_by_tags_in_tenant(
        self,
        ctx,
        tags,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        levels=None,
        limit: int = 10,
    ):
        self.tag_search_calls.append(
            {
                "ctx": ctx,
                "tags": tags,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "levels": levels,
                "limit": limit,
            }
        )
        return [
            {
                "uri": "viking://resources/architecture/microservices.md",
                "parent_uri": "viking://resources/architecture",
                "abstract": "Microservice architecture patterns",
                "level": 2,
                "context_type": "resource",
                "tags": ["auto:microservice"],
            }
        ]

    async def search_children_in_tenant(
        self,
        ctx,
        parent_uri: str,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        self.child_search_calls.append(parent_uri)
        if parent_uri == "viking://resources/architecture":
            return [
                {
                    "uri": "viking://resources/architecture/microservices.md",
                    "parent_uri": "viking://resources/architecture",
                    "abstract": "Microservice architecture patterns",
                    "_score": 0.88,
                    "level": 2,
                    "context_type": "resource",
                    "tags": ["auto:microservice"],
                }
            ]
        return []


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.ROOT)


@pytest.fixture(autouse=True)
def _disable_viking_fs(monkeypatch):
    monkeypatch.setattr("openviking.retrieve.hierarchical_retriever.get_viking_fs", lambda: None)


@pytest.mark.asyncio
async def test_retrieve_expands_related_subtree_from_global_hit_tags():
    storage = TagExpansionStorage()
    retriever = HierarchicalRetriever(storage=storage, embedder=DummyEmbedder(), rerank_config=None)

    result = await retriever.retrieve(
        TypedQuery(
            query="model training best practices",
            context_type=ContextType.RESOURCE,
            intent="",
        ),
        ctx=_ctx(),
        limit=3,
    )

    assert storage.tag_search_calls
    assert storage.tag_search_calls[0]["tags"] == ["auto:model-training"]
    assert "viking://resources/data-engineering" in storage.child_search_calls
    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/data-engineering/feature-store.md"
    ]


@pytest.mark.asyncio
async def test_retrieve_uses_explicit_tags_when_global_search_returns_nothing():
    storage = ExplicitTagStorage()
    retriever = HierarchicalRetriever(storage=storage, embedder=DummyEmbedder(), rerank_config=None)

    result = await retriever.retrieve(
        TypedQuery(
            query="architecture guidance",
            context_type=ContextType.RESOURCE,
            intent="",
            tags=["microservice"],
        ),
        ctx=_ctx(),
        limit=3,
    )

    assert storage.global_search_calls
    assert storage.global_search_calls[0]["tags"] == ["user:microservice", "auto:microservice"]
    assert storage.tag_search_calls
    assert storage.tag_search_calls[0]["tags"] == ["user:microservice", "auto:microservice"]
    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/architecture/microservices.md"
    ]
