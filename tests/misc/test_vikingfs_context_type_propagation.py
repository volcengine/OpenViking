# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests: the observer's context-type label is statistics-only.

The retrieval observer bucketed every query under "unknown" because the API's
``context_type`` reached only the result filter (``scope_dsl``) and never the
retriever's classification (#4090). The label now travels on a dedicated
statistics-only channel — ``stats_context_type`` — rather than through
``TypedQuery.context_type``, which retrieval actually reads (directory
selection in ``default_target_directories`` and the vector filter in
``_build_scope_filter``).

These tests pin both halves of that contract: the requested type reaches
``record_query``, and retrieval is left byte-for-byte unchanged.
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
            captured["kwargs"] = kwargs
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
async def test_find_carries_stats_label_without_touching_retrieval(monkeypatch):
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    await fs.find(
        "guide",
        target_uri=V + "resources/docs",
        ctx=_ctx(),
        stats_context_type="resource",
    )

    # The label reaches the retriever for the observer ...
    assert captured["kwargs"]["stats_context_type"] == "resource"
    # ... and TypedQuery.context_type stays None, so directory selection and the
    # vector filter behave exactly as they did before the label existed.
    assert captured["typed_query"].context_type is None


@pytest.mark.asyncio
async def test_find_without_label_leaves_classification_to_the_retriever(monkeypatch):
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    await fs.find("guide", target_uri=V + "resources/docs", ctx=_ctx())

    assert captured["kwargs"]["stats_context_type"] is None
    assert captured["typed_query"].context_type is None


@pytest.mark.asyncio
async def test_find_passes_multi_type_label_through_unchanged(monkeypatch):
    """A joined multi-type label is the router's business; the fs layer is opaque."""
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    await fs.find(
        "both",
        target_uri=V + "resources/docs",
        ctx=_ctx(),
        stats_context_type="memory+skill",
    )

    assert captured["kwargs"]["stats_context_type"] == "memory+skill"
    assert captured["typed_query"].context_type is None


@pytest.mark.asyncio
async def test_find_image_query_keeps_resource_default_and_carries_label(monkeypatch):
    """An image query keeps its retrieval-side RESOURCE default.

    The retriever applies image->RESOURCE only while TypedQuery.context_type is
    None, so the statistics channel must not disturb it.
    """
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    await fs.find(
        "photo",
        target_uri=V + "resources/docs",
        ctx=_ctx(),
        stats_context_type="memory",
        image_url="data:image/png;base64,abc",
    )

    assert captured["typed_query"].image_query is True
    assert captured["typed_query"].context_type is None
    assert captured["kwargs"]["stats_context_type"] == "memory"


@pytest.mark.asyncio
async def test_search_raw_fallback_carries_stats_label(monkeypatch):
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    # No session context and no image query: the raw-query fallback runs.
    await fs.search("raw", ctx=_ctx(), stats_context_type="memory")

    assert captured["kwargs"]["stats_context_type"] == "memory"
    assert captured["typed_query"].context_type is None
    assert captured["typed_query"].intent == ""


@pytest.mark.asyncio
async def test_search_image_branch_carries_stats_label(monkeypatch):
    fs = _make_viking_fs()
    captured = {}
    _install_fake_retriever(monkeypatch, captured)

    await fs.search(
        "photo",
        target_uri=V + "resources/docs",
        ctx=_ctx(),
        stats_context_type="skill",
        image_url="data:image/png;base64,abc",
    )

    assert captured["typed_query"].image_query is True
    assert captured["typed_query"].context_type is None
    assert captured["kwargs"]["stats_context_type"] == "skill"


class _ObserverHarness:
    """Drives the real HierarchicalRetriever against a fake vector proxy."""

    def __init__(self, monkeypatch):
        from openviking.models.embedder.base import EmbedResult

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

        FakeProxy.captured = {}
        self.proxy = FakeProxy
        monkeypatch.setattr(
            "openviking.retrieve.hierarchical_retriever.VikingDBManagerProxy",
            FakeProxy,
        )
        self.embedder = Embedder()


@pytest.mark.asyncio
async def test_observer_records_stats_label_while_retrieval_stays_untyped(monkeypatch):
    """End-to-end pin for #4090: the label reaches record_query().

    The vector filter must NOT receive the requested type: that keeps the fix
    provably metrics-only rather than smuggling in a second scope predicate.
    """
    from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
    from openviking.retrieve.retrieval_stats import get_stats_collector

    harness = _ObserverHarness(monkeypatch)
    collector = get_stats_collector()
    collector.reset()
    try:
        retriever = HierarchicalRetriever(storage=object(), embedder=harness.embedder)
        await retriever.retrieve(
            TypedQuery(
                query="guide",
                context_type=None,
                intent="",
                target_directories=[V + "user/acc1/user1"],
            ),
            ctx=_ctx(),
            limit=5,
            stats_context_type="resource",
        )

        snapshot = collector.snapshot()
        assert snapshot.queries_by_type.get("resource") == 1
        assert snapshot.queries_by_type.get("unknown", 0) == 0
        # Retrieval untouched: the type never became a vector-filter predicate.
        assert harness.proxy.captured["context_type"] is None
    finally:
        collector.reset()


@pytest.mark.asyncio
async def test_observer_falls_back_to_query_type_when_no_label(monkeypatch):
    """Intent analysis keeps classifying the observer when the caller said nothing."""
    from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
    from openviking.retrieve.retrieval_stats import get_stats_collector

    harness = _ObserverHarness(monkeypatch)
    collector = get_stats_collector()
    collector.reset()
    try:
        retriever = HierarchicalRetriever(storage=object(), embedder=harness.embedder)
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
        assert harness.proxy.captured["context_type"] == "memory"
    finally:
        collector.reset()


@pytest.mark.asyncio
async def test_observer_prefers_query_type_over_the_request_label(monkeypatch):
    """A finer retrieval-side type is not overwritten by the caller's label.

    This is the fan-out contract from the #4090 review: with intent analysis on,
    one request becomes several TypedQueries that each carry their own
    analyzer-assigned type, so the caller's coarser request-level join must not
    absorb classifications the observer already had.
    """
    from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
    from openviking.retrieve.retrieval_stats import get_stats_collector

    harness = _ObserverHarness(monkeypatch)
    collector = get_stats_collector()
    collector.reset()
    try:
        retriever = HierarchicalRetriever(storage=object(), embedder=harness.embedder)
        await retriever.retrieve(
            TypedQuery(
                query="remember this",
                context_type=ContextType.MEMORY,
                intent="",
                target_directories=[V + "user/acc1/user1"],
            ),
            ctx=_ctx(),
            limit=5,
            stats_context_type="memory+resource",
        )

        snapshot = collector.snapshot()
        assert snapshot.queries_by_type.get("memory") == 1
        # The request-level join did not absorb the per-query classification.
        assert snapshot.queries_by_type.get("memory+resource", 0) == 0
        assert snapshot.queries_by_type.get("unknown", 0) == 0
    finally:
        collector.reset()


@pytest.mark.asyncio
async def test_observer_fan_out_records_a_label_per_query(monkeypatch):
    """A request that fans out to memory + resource + resource keeps all three.

    Caller-first precedence recorded this as three ``memory+resource`` rows:
    the counts stayed right while the classification got coarser than before the
    label existed, on the only path that already classified correctly.
    """
    from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
    from openviking.retrieve.retrieval_stats import get_stats_collector

    harness = _ObserverHarness(monkeypatch)
    collector = get_stats_collector()
    collector.reset()
    try:
        retriever = HierarchicalRetriever(storage=object(), embedder=harness.embedder)
        typed_queries = [
            TypedQuery(
                query="what do I remember",
                context_type=ContextType.MEMORY,
                intent="",
                target_directories=[V + "user/acc1/user1"],
            ),
            TypedQuery(
                query="the design doc",
                context_type=ContextType.RESOURCE,
                intent="",
                target_directories=[V + "user/acc1/user1"],
            ),
            TypedQuery(
                query="the migration notes",
                context_type=ContextType.RESOURCE,
                intent="",
                target_directories=[V + "user/acc1/user1"],
            ),
        ]
        for typed_query in typed_queries:
            await retriever.retrieve(
                typed_query,
                ctx=_ctx(),
                limit=5,
                stats_context_type="memory+resource",
            )

        snapshot = collector.snapshot()
        assert snapshot.queries_by_type.get("memory") == 1
        assert snapshot.queries_by_type.get("resource") == 2
        assert snapshot.queries_by_type.get("memory+resource", 0) == 0
    finally:
        collector.reset()


@pytest.mark.asyncio
async def test_observer_records_unknown_when_nothing_claims_the_query(monkeypatch):
    from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
    from openviking.retrieve.retrieval_stats import get_stats_collector

    harness = _ObserverHarness(monkeypatch)
    collector = get_stats_collector()
    collector.reset()
    try:
        retriever = HierarchicalRetriever(storage=object(), embedder=harness.embedder)
        await retriever.retrieve(
            TypedQuery(
                query="guide",
                context_type=None,
                intent="",
                target_directories=[V + "user/acc1/user1"],
            ),
            ctx=_ctx(),
            limit=5,
        )

        snapshot = collector.snapshot()
        assert snapshot.queries_by_type.get("unknown") == 1
    finally:
        collector.reset()


def test_stats_label_normalizes_and_joins_multi_type_requests():
    from openviking.utils.search_filters import stats_context_type_label

    assert stats_context_type_label(None) is None
    assert stats_context_type_label("") is None
    assert stats_context_type_label(" Resource ") == "resource"
    assert stats_context_type_label(ContextType.SKILL) == "skill"
    assert stats_context_type_label(["memory", "skill"]) == "memory+skill"
    assert stats_context_type_label("memory,resource") == "memory+resource"
    assert stats_context_type_label(["memory", "memory"]) == "memory"
