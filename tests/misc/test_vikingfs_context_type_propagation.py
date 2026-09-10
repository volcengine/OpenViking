# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests: API context_type reaches TypedQuery.context_type.

The retrieval observer buckets every query under "unknown" when
TypedQuery.context_type is None. The API's context_type used to be applied
only as a result filter (scope_dsl); these tests pin the new behavior that a
single-type request is threaded through to the retriever query while
multi-type / invalid values stay on the filter path (unclassified).
"""

import contextvars
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.retrieve.types import ContextType, QueryResult, TypedQuery
from openviking_cli.session.user_id import UserIdentifier


V = "viking:" + "//"


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)


def _make_viking_fs() -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs.query_embedder = MagicMock(name="embedder")
    fs.rerank_config = None
    fs.retrieval_config = None
    fs.vector_store = MagicMock(name="vector_store")
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx_ctx_type", default=None)
    fs._ensure_access = MagicMock()
    fs._get_vector_store = MagicMock(return_value=fs.vector_store)
    fs._get_embedder = MagicMock(return_value=fs.query_embedder)
    fs._ctx_or_default = MagicMock(return_value=_ctx())
    fs.abstract = AsyncMock(return_value="")
    return fs


def _install_fake_retriever(monkeypatch, captured):
    class FakeRetriever:
        def __init__(self, storage, embedder, rerank_config, retrieval_config):
            pass

        async def retrieve(self, typed_query, **kwargs):
            captured["typed_query"] = typed_query
            return QueryResult(
                query=typed_query,
                matched_contexts=[],
                searched_directories=typed_query.target_directories,
            )

    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.HierarchicalRetriever",
        FakeRetriever,
    )


@pytest.mark.asyncio
async def test_find_propagates_single_context_type_string(monkeypatch):
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    await fs.find("guide", target_uri=V + "resources/docs", ctx=_ctx(), context_type="resource")

    assert captured["typed_query"].context_type == ContextType.RESOURCE


@pytest.mark.asyncio
async def test_find_propagates_context_type_enum(monkeypatch):
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    await fs.find(
        "skill query",
        target_uri=V + "resources/docs",
        ctx=_ctx(),
        context_type=ContextType.SKILL,
    )

    assert captured["typed_query"].context_type == ContextType.SKILL


@pytest.mark.asyncio
async def test_find_multi_type_list_stays_on_filter_path(monkeypatch):
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    await fs.find(
        "both",
        target_uri=V + "resources/docs",
        ctx=_ctx(),
        context_type=["memory", "skill"],
    )

    # TypedQuery carries a single type; a multi-type request is scoped by the
    # filter and intentionally left unclassified for the observer.
    assert captured["typed_query"].context_type is None


@pytest.mark.asyncio
async def test_find_invalid_context_type_stays_unclassified(monkeypatch):
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    await fs.find("weird", target_uri=V + "resources/docs", ctx=_ctx(), context_type="not-a-type")

    assert captured["typed_query"].context_type is None


@pytest.mark.asyncio
async def test_search_fallback_propagates_context_type(monkeypatch):
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    # No session context and no image query: the raw-query fallback runs.
    await fs.search("raw", ctx=_ctx(), context_type="memory")

    assert captured["typed_query"].context_type == ContextType.MEMORY
    assert captured["typed_query"].intent == ""


@pytest.mark.asyncio
async def test_find_image_query_propagates_context_type(monkeypatch):
    """find() threads a typed image request (no image->RESOURCE fallback)."""
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    await fs.find(
        "photo",
        target_uri=V + "resources/docs",
        ctx=_ctx(),
        context_type="memory",
        image_url="data:image/png;base64,abc",
    )

    assert captured["typed_query"].context_type == ContextType.MEMORY
    assert captured["typed_query"].image_query is True


@pytest.mark.asyncio
async def test_search_image_query_propagates_context_type(monkeypatch):
    """search()'s image branch is consistent with find() (review #4091)."""
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    await fs.search(
        "photo",
        target_uri=V + "resources/docs",
        ctx=_ctx(),
        context_type="skill",
        image_url="data:image/png;base64,abc",
    )

    assert captured["typed_query"].context_type == ContextType.SKILL
    assert captured["typed_query"].image_query is True


@pytest.mark.asyncio
async def test_find_normalizes_context_type_case_and_whitespace(monkeypatch):
    """Observer classification matches the filter: "Memory" -> memory."""
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    await fs.find("x", target_uri=V + "resources/docs", ctx=_ctx(), context_type=" Memory ")

    assert captured["typed_query"].context_type == ContextType.MEMORY


@pytest.mark.asyncio
async def test_observer_records_threaded_context_type(monkeypatch):
    """End-to-end pin for #4090: the threaded type reaches record_query().

    The propagation tests above stub HierarchicalRetriever; this one drives
    the real retriever against a fake vector proxy so the observer bucket
    counter (the line #4090 is about) is exercised.
    """
    from openviking.models.embedder.base import EmbedResult
    from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
    from openviking.retrieve.retrieval_stats import get_stats_collector

    class FakeProxy:
        captured = {}

        def __init__(self, _storage, _ctx):
            pass

        @property
        def collection_name(self):
            return "test"

        async def collection_exists_bound(self):
            return True

        async def search_in_tenant(self, **kwargs):
            self.captured.update(kwargs)
            return []

    class Embedder:
        supports_multimodal = False

        def prepare_embedding_input(self, content):
            return content

        async def embed_async(self, content, is_query=False):
            return EmbedResult(dense_vector=[1.0])

    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.VikingDBManagerProxy",
        FakeProxy,
    )

    collector = get_stats_collector()
    collector.reset()
    try:
        retriever = HierarchicalRetriever(storage=object(), embedder=Embedder())
        await retriever.retrieve(
            TypedQuery(
                query="guide",
                context_type=ContextType.MEMORY,
                intent="",
                target_directories=[V + "user/acc1/user1"],
            ),
            ctx=_ctx(),
            limit=5,
        )

        snapshot = collector.snapshot()
        assert snapshot.queries_by_type.get("memory") == 1
        assert snapshot.queries_by_type.get("unknown", 0) == 0
        assert FakeProxy.captured["context_type"] == "memory"
    finally:
        collector.reset()
