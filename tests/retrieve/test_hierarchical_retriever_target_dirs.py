# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Hierarchical retriever target_directories tests."""

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.server.identity import RequestContext, Role
from openviking.storage.vector_store.expr import Prefix
from openviking_cli.retrieve.types import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier


class DummyStorage:
    """Minimal storage stub to capture search filters."""

    def __init__(self) -> None:
        self.search_calls = []

    async def collection_exists(self, _name: str) -> bool:
        return True

    async def search(
        self,
        collection: str,
        query_vector=None,
        sparse_query_vector=None,
        filter=None,
        limit: int = 10,
        offset: int = 0,
        output_fields=None,
        with_vector: bool = False,
    ):
        self.search_calls.append(
            {
                "collection": collection,
                "filter": filter,
                "limit": limit,
                "offset": offset,
            }
        )
        return []


def _contains_uri_scope_filter(obj, target_uri: str) -> bool:
    if isinstance(obj, Prefix):
        return obj.field == "uri" and obj.prefix == target_uri
    if isinstance(obj, dict):
        if (
            obj.get("op") == "must"
            and obj.get("field") == "uri"
            and target_uri in obj.get("conds", [])
        ):
            return True
        return any(_contains_uri_scope_filter(v, target_uri) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_uri_scope_filter(v, target_uri) for v in obj)
    if hasattr(obj, "__dict__"):
        return any(_contains_uri_scope_filter(v, target_uri) for v in vars(obj).values())
    return False


@pytest.mark.asyncio
async def test_retrieve_honors_target_directories_scope_filter():
    target_uri = "viking://resources/foo"
    storage = DummyStorage()
    retriever = HierarchicalRetriever(storage=storage, embedder=None, rerank_config=None)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)

    query = TypedQuery(
        query="test",
        context_type=ContextType.RESOURCE,
        intent="",
        target_directories=[target_uri],
    )

    result = await retriever.retrieve(query, ctx=ctx, limit=3)

    assert result.searched_directories == [target_uri]
    assert storage.search_calls
    assert _contains_uri_scope_filter(storage.search_calls[0]["filter"], target_uri)
