# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Query-side embedding truncation regression tests."""

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.server.identity import RequestContext, Role
from openviking.utils.embedding_input import estimate_embedding_input_tokens
from openviking_cli.retrieve.types import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier


class CapturingEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        self.calls.append((text, is_query))
        return EmbedResult(dense_vector=[1.0])


class EmptyStorage:
    collection_name = "context"

    async def collection_exists_bound(self) -> bool:
        return True

    async def search_global_roots_in_tenant(
        self,
        ctx,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        return []

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
        return []


async def _retrieve_with_query(query_text: str, max_tokens: int) -> str:
    embedder = CapturingEmbedder()
    retriever = HierarchicalRetriever(
        storage=EmptyStorage(),
        embedder=embedder,
        rerank_config=None,
        query_max_input_tokens=max_tokens,
    )
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)

    await retriever.retrieve(
        TypedQuery(
            query=query_text,
            context_type=ContextType.RESOURCE,
            intent="",
            target_directories=["viking://resources"],
        ),
        ctx=ctx,
        limit=3,
    )

    assert len(embedder.calls) == 1
    embedded_text, is_query = embedder.calls[0]
    assert is_query is True
    return embedded_text


@pytest.mark.asyncio
async def test_ascii_query_is_truncated_before_embedding():
    query = "release-triage " * 200

    embedded_text = await _retrieve_with_query(query, max_tokens=50)

    assert embedded_text != query
    assert estimate_embedding_input_tokens(embedded_text) <= 50
    assert "...(truncated for embedding)" not in embedded_text


@pytest.mark.asyncio
async def test_cjk_query_is_truncated_before_embedding():
    query = "请分析这个发布回归问题" * 200

    embedded_text = await _retrieve_with_query(query, max_tokens=50)

    assert embedded_text != query
    assert estimate_embedding_input_tokens(embedded_text) <= 50
    assert "...(truncated for embedding)" not in embedded_text
