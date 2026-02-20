# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Hierarchical retriever target_directories tests."""

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking_cli.retrieve.types import ContextType, TypedQuery


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


def _contains_prefix_filter(obj, prefix: str) -> bool:
    if isinstance(obj, dict):
        if obj.get("op") == "prefix" and obj.get("field") == "uri" and obj.get("prefix") == prefix:
            return True
        return any(_contains_prefix_filter(v, prefix) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_prefix_filter(v, prefix) for v in obj)
    return False


@pytest.mark.asyncio
async def test_retrieve_honors_target_directories_prefix_filter():
    target_uri = "viking://resources/foo"
    storage = DummyStorage()
    retriever = HierarchicalRetriever(storage=storage, embedder=None, rerank_config=None)

    query = TypedQuery(
        query="test",
        context_type=ContextType.RESOURCE,
        intent="",
        target_directories=[target_uri],
    )

    result = await retriever.retrieve(query, limit=3)

    assert result.searched_directories == [target_uri]
    assert storage.search_calls
    assert _contains_prefix_filter(storage.search_calls[0]["filter"], target_uri)
