# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VikingDB non-symmetric embedding support."""

from unittest.mock import patch

import pytest

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


def test_dense_backward_compat_no_params():
    """VikingDBDenseEmbedder without query_param/document_param works."""
    embedder = VikingDBDenseEmbedder.__new__(VikingDBDenseEmbedder)
    embedder.query_param = None
    embedder.document_param = None
    embedder.model_name = "test"
    embedder.dimension = 1024
    # Should not raise
    assert embedder._resolve_input_type(is_query=True) is None
