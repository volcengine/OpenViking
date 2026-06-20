# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Hierarchical retriever rerank behavior tests."""

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.server.identity import RequestContext, Role
from openviking_cli.retrieve.types import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import RerankConfig, RetrievalConfig


class DummyEmbedResult:
    def __init__(self) -> None:
        self.dense_vector = [1.0]
        self.sparse_vector = {"hello": 1.0}


class DummyEmbedder:
    def prepare_embedding_input(self, text: str) -> str:
        return text

    def embed(self, _query: str, is_query: bool = False) -> DummyEmbedResult:
        return DummyEmbedResult()

    async def embed_async(self, text: str, is_query: bool = False) -> DummyEmbedResult:
        return self.embed(text, is_query=is_query)


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


class DirectChildProxy:
    async def search_children_in_tenant(
        self,
        parent_uri: str,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        return [
            {
                "uri": f"{parent_uri}/file-a",
                "abstract": "child A",
                "_score": 0.2,
                "level": 2,
                "context_type": "resource",
            },
            {
                "uri": f"{parent_uri}/file-b",
                "abstract": "child B",
                "_score": 0.8,
                "level": 2,
                "context_type": "resource",
            },
        ]


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
    return RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)


def _query() -> TypedQuery:
    return TypedQuery(query="hello", context_type=ContextType.RESOURCE, intent="")


def _config() -> RerankConfig:
    return RerankConfig(ak="ak", sk="sk", threshold=0.1)


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
    fake_client = FakeRerankClient([0.11, 0.95])
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


@pytest.mark.asyncio
async def test_score_propagation_alpha_uses_configured_weight():
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
        retrieval_config=RetrievalConfig(score_propagation_alpha=1.0),
    )

    candidates = await retriever._recursive_search(
        vector_proxy=DirectChildProxy(),
        query="hello",
        query_vector=None,
        sparse_query_vector=None,
        starting_points=[("viking://resources", 0.4)],
        limit=1,
        mode=RetrieverMode.QUICK,
    )

    assert candidates[0]["uri"] == "viking://resources/file-b"
    assert candidates[0]["_final_score"] == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_default_retrieval_config_uses_semantic_score_without_hotness(monkeypatch):
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.hotness_score",
        lambda *args, **kwargs: pytest.fail("hotness_score should not be called by default"),
    )
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
    )

    result = await retriever._convert_to_matched_contexts(
        [
            {
                "uri": "viking://resources/file-a",
                "abstract": "child A",
                "_score": 1.0,
                "level": 2,
                "context_type": "resource",
            }
        ],
        ctx=_ctx(),
    )

    assert result[0].score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_retrieval_hotness_alpha_blends_when_configured(monkeypatch):
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.hotness_score",
        lambda *args, **kwargs: 0.5,
    )
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
        retrieval_config=RetrievalConfig(hotness_alpha=0.2),
    )

    result = await retriever._convert_to_matched_contexts(
        [
            {
                "uri": "viking://resources/file-a",
                "abstract": "child A",
                "_score": 1.0,
                "level": 2,
                "context_type": "resource",
            }
        ],
        ctx=_ctx(),
    )

    assert result[0].score == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_convert_to_matched_contexts_returns_empty_relations():
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
    )

    result = await retriever._convert_to_matched_contexts(
        [
            {
                "uri": "viking://resources/file-a",
                "abstract": "child A",
                "_score": 1.0,
                "level": 2,
                "context_type": "resource",
            }
        ],
        ctx=_ctx(),
    )

    assert result[0].relations == []


class _L2OverflowChildProxy:
    """Return a mixed-level child set for the root, empty for deeper parents.

    Children:
      - an L1 directory with a small abstract (rerank-eligible),
      - an L2 file with an oversized full-content abstract (must be excluded),
      - a row with NO ``level`` field, which the retriever treats as L2 by
        convention (``r.get("level", 2)``), so it must also be excluded.

    Regression for issue #2739.
    """

    def __init__(self) -> None:
        self._seen: set = set()

    async def search_children_in_tenant(
        self,
        parent_uri: str,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        if parent_uri in self._seen or parent_uri != "viking://resources":
            return []
        self._seen.add(parent_uri)
        return [
            {
                "uri": "viking://resources/subdir",
                "abstract": "small directory abstract",
                "_score": 0.2,
                "level": 1,
                "context_type": "resource",
            },
            {
                "uri": "viking://resources/big-file.md",
                "abstract": "X" * 5000,  # oversized L2 full-content abstract
                "_score": 0.8,
                "level": 2,
                "context_type": "resource",
            },
            {
                # No "level" field -> treated as L2 by the retriever's
                # r.get("level", 2) convention, so excluded from rerank too.
                "uri": "viking://resources/no-level.md",
                "abstract": "Y" * 4000,
                "_score": 0.5,
                "context_type": "resource",
            },
        ]


@pytest.mark.asyncio
async def test_recursive_search_excludes_l2_abstracts_from_rerank(monkeypatch):
    # Regression for #2739: _recursive_search must not feed L2 (full-content)
    # abstracts to the reranker. Doing so overflows the rerank batch and forces
    # the whole batch (including L0/L1 directory abstracts) to fall back to
    # vector scores. Only non-L2 docs are reranked; L2 (and missing-level, which
    # the retriever treats as L2) candidates keep their vector score.
    fake_client = FakeRerankClient([0.99])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=_config(),
        retrieval_config=RetrievalConfig(score_propagation_alpha=1.0),
    )

    candidates = await retriever._recursive_search(
        vector_proxy=_L2OverflowChildProxy(),
        query="hello",
        query_vector=None,
        sparse_query_vector=None,
        starting_points=[("viking://resources", 0.4)],
        limit=10,
        mode=RetrieverMode.THINKING,
    )

    # Only the non-L2 (L1) abstract reaches the reranker; the oversized L2 file
    # abstract AND the missing-level row are excluded, so the batch never
    # overflows.
    assert fake_client.calls == [("hello", ["small directory abstract"])]

    by_uri = {c["uri"]: c for c in candidates}
    # L2 candidate keeps its vector score (0.8), not a rerank score.
    assert "viking://resources/big-file.md" in by_uri
    assert by_uri["viking://resources/big-file.md"]["_final_score"] == pytest.approx(0.8)
    # Missing-level candidate is treated as L2 and keeps its vector score (0.5).
    assert "viking://resources/no-level.md" in by_uri
    assert by_uri["viking://resources/no-level.md"]["_final_score"] == pytest.approx(0.5)
    # Non-L2 candidate received the rerank score.
    assert "viking://resources/subdir" in by_uri
    assert by_uri["viking://resources/subdir"]["_final_score"] == pytest.approx(0.99)
