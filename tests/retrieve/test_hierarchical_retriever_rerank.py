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


class DummyVikingFS:
    """Mock VikingFS for graph scoring tests."""

    def __init__(self, relations_map=None, memory_files=None):
        self.relations_map = relations_map or {}
        self.memory_files = memory_files or {}
        self.relations_calls = []
        self.read_file_calls = []

    async def relations(self, uri: str, ctx=None):
        self.relations_calls.append(uri)
        return self.relations_map.get(uri, [])

    async def read_file(self, uri: str, ctx=None):
        self.read_file_calls.append(uri)
        return self.memory_files.get(uri, "")


@pytest.mark.asyncio
async def test_graph_alpha_zero_returns_empty_relations():
    """Explicit graph_alpha=0 should keep relations empty."""
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
        retrieval_config=RetrievalConfig(graph_alpha=0.0),
        viking_fs=DummyVikingFS(),
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
    assert len(result) == 1


@pytest.mark.asyncio
async def test_graph_scoring_with_relations_json():
    """Graph scoring should blend graph connectivity into final score."""
    vfs = DummyVikingFS(
        relations_map={
            "viking://resources/file-a": [
                {"uri": "viking://resources/file-b", "reason": "related concept"},
                {"uri": "viking://resources/file-c", "reason": "depends on"},
            ]
        }
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
        retrieval_config=RetrievalConfig(graph_alpha=0.5, graph_saturation_k=5.0),
        viking_fs=vfs,
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

    assert len(result) == 1
    mc = result[0]
    # graph_score = tanh(2 / 5) ≈ 0.38
    # final = 0.5 * 1.0 + 0.5 * 0.38 = 0.69
    assert mc.score == pytest.approx(0.69, abs=0.01)
    assert len(mc.relations) == 2
    assert mc.relations[0].uri == "viking://resources/file-b"
    assert mc.relations[0].abstract == "related concept"
    assert vfs.relations_calls == ["viking://resources/file-a"]


@pytest.mark.asyncio
async def test_graph_scoring_with_memory_file_links():
    """Graph scoring should parse MEMORY_FIELDS links from .md files."""
    memory_content = (
        "# Test Memory\n\n"
        "Some content here.\n\n"
        "<!-- MEMORY_FIELDS\n"
        '{\n  "links": [\n'
        '    {"to_uri": "viking://resources/other", "description": "linked item", '
        '"link_type": "related_to", "weight": 0.8}\n'
        "  ],\n"
        '  "backlinks": [\n'
        '    {"from_uri": "viking://resources/source", "description": "backlink source", '
        '"link_type": "related_to", "weight": 0.5}\n'
        "  ]\n"
        "}\n"
        "-->"
    )

    vfs = DummyVikingFS(
        memory_files={
            "viking://resources/file-a/.abstract.md": memory_content,
        }
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
        retrieval_config=RetrievalConfig(graph_alpha=0.5, graph_saturation_k=5.0),
        viking_fs=vfs,
    )

    # URI with .abstract.md suffix (as produced by _append_level_suffix)
    result = await retriever._convert_to_matched_contexts(
        [
            {
                "uri": "viking://resources/file-a/.abstract.md",
                "abstract": "child A",
                "_score": 1.0,
                "level": 0,
                "context_type": "resource",
            }
        ],
        ctx=_ctx(),
    )

    assert len(result) == 1
    mc = result[0]
    # 1 link + 1 backlink = 2 relations
    # graph_score = tanh(2 / 5) ≈ 0.38
    # final = 0.5 * 1.0 + 0.5 * 0.38 = 0.69
    assert mc.score == pytest.approx(0.69, abs=0.01)
    assert len(mc.relations) == 2


@pytest.mark.asyncio
async def test_graph_lazy_loading():
    """Graph data should only be loaded for top candidates, not all."""
    vfs = DummyVikingFS(
        relations_map={
            "viking://resources/file-a": [
                {"uri": "viking://resources/rel1", "reason": "related"}
            ]
        }
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
        retrieval_config=RetrievalConfig(graph_alpha=0.5),
        viking_fs=vfs,
    )

    # 10 candidates, but graph_alpha=0.5 *should* limit to top candidates
    candidates = [
        {
            "uri": f"viking://resources/file-{chr(ord('a') + i)}",
            "abstract": f"item {i}",
            "_score": 1.0 - i * 0.05,
            "level": 2,
            "context_type": "resource",
        }
        for i in range(10)
    ]

    result = await retriever._convert_to_matched_contexts(
        candidates,
        ctx=_ctx(),
    )

    # We loaded graph data for top candidates (max(len*2, 5) = max(20,5) = 20, capped by 10 avail)
    # The mock FS only has data for file-a
    assert vfs.relations_calls is not None
    # Verify at least the top result was loaded
    assert "viking://resources/file-a" in vfs.relations_calls
    # Verify file-a has its relations
    file_a = next(r for r in result if "file-a" in r.uri)
    assert len(file_a.relations) == 1

