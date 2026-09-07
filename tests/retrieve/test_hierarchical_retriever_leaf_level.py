# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""File-level (L2) vector hits must surface regardless of ACL mode (#4482)."""

import sys
import types

import pytest

_ark = types.ModuleType("volcenginesdkarkruntime")
_ark_exc = types.ModuleType("volcenginesdkarkruntime._exceptions")


class _ArkRateLimitError(Exception):
    pass


_ark_exc.ArkRateLimitError = _ArkRateLimitError
_ark._exceptions = _ark_exc
sys.modules.setdefault("volcenginesdkarkruntime", _ark)
sys.modules.setdefault("volcenginesdkarkruntime._exceptions", _ark_exc)

from openviking.models.embedder.base import EmbedResult
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.server.identity import RequestContext, Role
from openviking_cli.retrieve.types import TypedQuery
from openviking_cli.session.user_id import UserIdentifier


class _LeafAwareProxy:
    level_queries = []

    def __init__(self, _storage, _ctx):
        pass

    @property
    def collection_name(self):
        return "test"

    async def collection_exists_bound(self):
        return True

    async def search_children_in_tenant(self, *args, **kwargs):
        return []

    async def search_in_tenant(self, **kwargs):
        level = kwargs.get("level")
        _LeafAwareProxy.level_queries.append(tuple(level) if level else None)
        wants_leaf = level is None or 2 in level
        wants_dir = level is None or 0 in level or 1 in level
        results = []
        if wants_leaf:
            results.append(
                {
                    "uri": "viking://user/u/memories/entities/mem_test.md",
                    "context_type": "memory",
                    "level": 2,
                    "_score": 0.95,
                    "abstract": "UNIQUEKEYWORD12345 test content",
                }
            )
        if wants_dir:
            results.append(
                {
                    "uri": "viking://user/u/memories/entities",
                    "context_type": "memory",
                    "level": 0,
                    "_score": 0.4,
                    "abstract": "entities directory",
                }
            )
        return results


class _Embedder:
    supports_multimodal = False

    def prepare_embedding_input(self, content):
        return content

    async def embed_async(self, content, is_query=False):
        return EmbedResult(dense_vector=[1.0])


class _NoAclVectorStore:
    """Local single-user deployment: ACL manager absent, L2 vectors stored."""

    def _acl_enabled(self, ctx) -> bool:
        return False


def _ctx():
    return RequestContext(user=UserIdentifier("acc", "user"), role=Role.USER)


@pytest.fixture(autouse=True)
def _reset_capture():
    _LeafAwareProxy.level_queries = []
    yield


@pytest.mark.asyncio
async def test_leaf_vector_hits_returned_without_acl(monkeypatch):
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.VikingDBManagerProxy",
        _LeafAwareProxy,
    )
    retriever = HierarchicalRetriever(storage=_NoAclVectorStore(), embedder=_Embedder())

    result = await retriever.retrieve(
        TypedQuery(query="UNIQUEKEYWORD12345", context_type=None, intent=""),
        ctx=_ctx(),
        limit=5,
    )

    uris = [c.uri for c in result.matched_contexts]
    assert "viking://user/u/memories/entities/mem_test.md" in uris
    assert any(q is None or 2 in q for q in _LeafAwareProxy.level_queries)


@pytest.mark.asyncio
async def test_explicit_level_excludes_leaf_query(monkeypatch):
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.VikingDBManagerProxy",
        _LeafAwareProxy,
    )
    retriever = HierarchicalRetriever(storage=_NoAclVectorStore(), embedder=_Embedder())

    result = await retriever.retrieve(
        TypedQuery(query="anything", context_type=None, intent=""),
        ctx=_ctx(),
        limit=5,
        level=[0, 1],
    )

    assert all(q is not None and 2 not in q for q in _LeafAwareProxy.level_queries)
    assert all(
        "mem_test.md" not in c.uri for c in result.matched_contexts
    )


@pytest.mark.asyncio
async def test_thinking_mode_queries_leaf_vectors_without_acl(monkeypatch):
    """The fixed branch itself: THINKING's leaf query must not be ACL-gated."""
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.VikingDBManagerProxy",
        _LeafAwareProxy,
    )
    retriever = HierarchicalRetriever(storage=_NoAclVectorStore(), embedder=_Embedder())

    result = await retriever.retrieve(
        TypedQuery(query="UNIQUEKEYWORD12345", context_type=None, intent=""),
        ctx=_ctx(),
        limit=5,
        mode=RetrieverMode.THINKING,
    )

    assert (2,) in [q for q in _LeafAwareProxy.level_queries if q]  # L2 query issued
    uris = [c.uri for c in result.matched_contexts]
    assert "viking://user/u/memories/entities/mem_test.md" in uris


@pytest.mark.asyncio
async def test_thinking_mode_level_filter_skips_leaf_query(monkeypatch):
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.VikingDBManagerProxy",
        _LeafAwareProxy,
    )
    retriever = HierarchicalRetriever(storage=_NoAclVectorStore(), embedder=_Embedder())

    result = await retriever.retrieve(
        TypedQuery(query="anything", context_type=None, intent=""),
        ctx=_ctx(),
        limit=5,
        mode=RetrieverMode.THINKING,
        level=[0, 1],
    )

    assert all(q != (2,) for q in _LeafAwareProxy.level_queries)
    assert all(
        "mem_test.md" not in c.uri for c in result.matched_contexts
    )
