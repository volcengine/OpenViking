# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VikingDB non-symmetric embedding support."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openviking.models.embedder.base import EmbedderBase
from openviking.models.embedder.vikingdb_embedders import (
    VikingDBDenseEmbedder,
    VikingDBHybridEmbedder,
)


@pytest.fixture
def mock_vikingdb_client():
    """Patch VikingDB client initialization."""
    with patch.object(
        VikingDBDenseEmbedder, "_init_vikingdb_client", return_value=None
    ) as mock_init:
        mock_init.side_effect = lambda *args, **kwargs: None
        yield mock_init


def test_dense_resolve_input_type_symmetric():
    """When no query_param/document_param, input_type is None (symmetric)."""
    embedder = VikingDBDenseEmbedder.__new__(VikingDBDenseEmbedder)
    embedder.query_param = None
    embedder.document_param = None
    assert embedder._resolve_input_type(is_query=True) is None
    assert embedder._resolve_input_type(is_query=False) is None


def test_dense_resolve_input_type_nonsymmetric():
    """When query_param/document_param set, return correct value for is_query."""
    embedder = VikingDBDenseEmbedder.__new__(VikingDBDenseEmbedder)
    embedder.query_param = "query"
    embedder.document_param = "passage"
    assert embedder._resolve_input_type(is_query=True) == "query"
    assert embedder._resolve_input_type(is_query=False) == "passage"


def test_hybrid_resolve_input_type_nonsymmetric():
    """Hybrid embedder also resolves input_type correctly."""
    embedder = VikingDBHybridEmbedder.__new__(VikingDBHybridEmbedder)
    embedder.query_param = "search_query"
    embedder.document_param = "search_document"
    assert embedder._resolve_input_type(is_query=True) == "search_query"
    assert embedder._resolve_input_type(is_query=False) == "search_document"


def _build_async_embedder(embedder_cls, payload, **params):
    """Build an embedder whose async request body can be inspected."""
    embedder = embedder_cls.__new__(embedder_cls)
    EmbedderBase.__init__(embedder, "test-model", {})
    embedder.dimension = None
    embedder.host = "test-host"
    embedder.dense_model = {"name": "test-model", "version": None, "dim": None}
    embedder.sparse_model = {"name": "test-model", "version": None}
    embedder.query_param = params.get("query_param")
    embedder.document_param = params.get("document_param")

    sent: dict = {}

    def _prepare_request(method, path, data=None):
        sent.update(data or {})
        return SimpleNamespace(method=method, path=path, headers={}, body=b"")

    response = SimpleNamespace(status_code=200, json=lambda: payload)
    embedder.client = Mock()
    embedder.client.prepare_request.side_effect = _prepare_request
    embedder._async_client_cache = Mock()
    embedder._async_client_cache.get.return_value = SimpleNamespace(
        request=AsyncMock(return_value=response)
    )
    return embedder, sent


@pytest.mark.asyncio
async def test_dense_embed_async_sends_input_type():
    """embed_compat routes every request through embed_async, so it must send input_type."""
    embedder, sent = _build_async_embedder(
        VikingDBDenseEmbedder,
        {"result": {"data": [{"dense_embedding": [0.1, 0.2]}]}},
        query_param="query",
        document_param="passage",
    )

    await embedder.embed_async("hello", is_query=True)
    assert sent["data"][0]["input_type"] == "query"

    sent.clear()
    await embedder.embed_async("hello", is_query=False)
    assert sent["data"][0]["input_type"] == "passage"


@pytest.mark.asyncio
async def test_hybrid_embed_async_sends_input_type():
    """The hybrid async path must send input_type as its sync path does."""
    embedder, sent = _build_async_embedder(
        VikingDBHybridEmbedder,
        {"result": {"data": [{"dense": [0.1, 0.2], "sparse": {"1": 0.5}}]}},
        query_param="search_query",
        document_param="search_document",
    )

    await embedder.embed_async("hello", is_query=True)
    assert sent["data"][0]["input_type"] == "search_query"

    sent.clear()
    await embedder.embed_async("hello", is_query=False)
    assert sent["data"][0]["input_type"] == "search_document"


@pytest.mark.asyncio
async def test_embed_async_symmetric_omits_input_type():
    """Without query_param/document_param the async request stays symmetric."""
    embedder, sent = _build_async_embedder(
        VikingDBDenseEmbedder,
        {"result": {"data": [{"dense_embedding": [0.1, 0.2]}]}},
    )

    await embedder.embed_async("hello", is_query=True)
    assert "input_type" not in sent["data"][0]


def test_dense_backward_compat_no_params():
    """VikingDBDenseEmbedder without query_param/document_param works."""
    embedder = VikingDBDenseEmbedder.__new__(VikingDBDenseEmbedder)
    embedder.query_param = None
    embedder.document_param = None
    embedder.model_name = "test"
    embedder.dimension = 1024
    # Should not raise
    assert embedder._resolve_input_type(is_query=True) is None
