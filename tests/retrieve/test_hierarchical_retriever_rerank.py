# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Hierarchical retriever rerank behavior tests."""

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.server.identity import RequestContext, Role
from openviking_cli.retrieve.types import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import RerankConfig


class DummyEmbedResult:
    def __init__(self) -> None:
        self.dense_vector = [1.0]
        self.sparse_vector = {"hello": 1.0}


class DummyEmbedder:
    def embed(self, _query: str, is_query: bool = False) -> DummyEmbedResult:
        return DummyEmbedResult()


class DummyStorage:
    def __init__(self) -> None:
        self.collection_name = "context"
        self.global_search_calls = []
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
        extra_filter=None,
        limit: int = 10,
    ):
        self.global_search_calls.append(
            {
                "ctx": ctx,
                "query_vector": query_vector,
                "sparse_query_vector": sparse_query_vector,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "limit": limit,
            }
        )
        return [
            {
                "uri": "viking://resources/root-a",
                "abstract": "root A",
                "_score": 0.2,
                "level": 1,
                "context_type": "resource",
            },
            {
                "uri": "viking://resources/root-b",
                "abstract": "root B",
                "_score": 0.8,
                "level": 1,
                "context_type": "resource",
            },
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
        self.child_search_calls.append(
            {
                "ctx": ctx,
                "parent_uri": parent_uri,
                "query_vector": query_vector,
                "sparse_query_vector": sparse_query_vector,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "limit": limit,
            }
        )
        if parent_uri == "viking://resources":
            return [
                {
                    "uri": "viking://resources/file-a",
                    "abstract": "child A",
                    "_score": 0.2,
                    "level": 2,
                    "context_type": "resource",
                    "category": "doc",
                },
                {
                    "uri": "viking://resources/file-b",
                    "abstract": "child B",
                    "_score": 0.8,
                    "level": 2,
                    "context_type": "resource",
                    "category": "doc",
                },
            ]
        return []


class LevelTwoGlobalStorage(DummyStorage):
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
        self.global_search_calls.append(
            {
                "ctx": ctx,
                "query_vector": query_vector,
                "sparse_query_vector": sparse_query_vector,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "limit": limit,
            }
        )
        return [
            {
                "uri": "viking://resources/file-a",
                "abstract": "child A",
                "_score": 0.2,
                "level": 2,
                "context_type": "resource",
                "category": "doc",
            },
            {
                "uri": "viking://resources/file-b",
                "abstract": "child B",
                "_score": 0.8,
                "level": 2,
                "context_type": "resource",
                "category": "doc",
            },
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
        self.child_search_calls.append(
            {
                "ctx": ctx,
                "parent_uri": parent_uri,
                "query_vector": query_vector,
                "sparse_query_vector": sparse_query_vector,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "limit": limit,
            }
        )
        return []


class FakeRerankClient:
    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = []
        self._cursor = 0

    def rerank_batch(self, query: str, documents: list[str]):
        self.calls.append((query, list(documents)))
        start = self._cursor
        end = start + len(documents)
        self._cursor = end
        return list(self.scores[start:end])


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)


def _query() -> TypedQuery:
    return TypedQuery(query="hello", context_type=ContextType.RESOURCE, intent="")


def _config() -> RerankConfig:
    return RerankConfig(ak="ak", sk="sk", threshold=0.1)


@pytest.fixture(autouse=True)
def _disable_viking_fs(monkeypatch):
    monkeypatch.setattr("openviking.retrieve.hierarchical_retriever.get_viking_fs", lambda: None)


def test_retriever_initializes_rerank_client(monkeypatch):
    fake_client = FakeRerankClient([0.9, 0.1])

    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    assert retriever._rerank_client is fake_client


def test_merge_starting_points_prefers_rerank_scores_in_thinking_mode(monkeypatch):
    fake_client = FakeRerankClient([0.95, 0.05])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    starting_points = retriever._merge_starting_points(
        "hello",
        ["viking://resources"],
        [
            {
                "uri": "viking://resources/root-a",
                "abstract": "root A",
                "_score": 0.2,
                "level": 1,
            },
            {
                "uri": "viking://resources/root-b",
                "abstract": "root B",
                "_score": 0.8,
                "level": 1,
            },
        ],
        mode=RetrieverMode.THINKING,
    )

    assert starting_points[:2] == [
        ("viking://resources/root-a", 0.95),
        ("viking://resources/root-b", 0.05),
    ]
    assert fake_client.calls == [("hello", ["root A", "root B"])]


@pytest.mark.asyncio
async def test_retrieve_uses_rerank_scores_in_thinking_mode(monkeypatch):
    fake_client = FakeRerankClient([0.95, 0.05, 0.11, 0.95])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.THINKING)

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-b",
        "viking://resources/file-a",
    ]
    assert fake_client.calls[0] == ("hello", ["root A", "root B"])
    assert fake_client.calls[1] == ("hello", ["child A", "child B"])


@pytest.mark.asyncio
async def test_retrieve_reranks_level_two_initial_candidates_in_thinking_mode(monkeypatch):
    fake_client = FakeRerankClient([0.05, 0.95])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=LevelTwoGlobalStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.THINKING)

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-b",
        "viking://resources/file-a",
    ]
    assert fake_client.calls == [("hello", ["child A", "child B"])]


@pytest.mark.asyncio
async def test_retrieve_falls_back_to_vector_scores_when_rerank_returns_none(monkeypatch):
    class NoneRerankClient(FakeRerankClient):
        def rerank_batch(self, query: str, documents: list[str]):
            self.calls.append((query, list(documents)))
            return None

    fake_client = NoneRerankClient([])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.THINKING)

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-b",
        "viking://resources/file-a",
    ]
    assert fake_client.calls


@pytest.mark.asyncio
async def test_quick_mode_skips_rerank(monkeypatch):
    fake_client = FakeRerankClient([0.95, 0.05, 0.05, 0.95])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.QUICK)

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-b",
        "viking://resources/file-a",
    ]
    assert fake_client.calls == []
