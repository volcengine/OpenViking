# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VikingDB non-symmetric embedding support."""

from functools import partial
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
import requests

from openviking.config.vector import resolve_effective_embedding
from openviking.models.embedder.vikingdb_embedders import (
    VikingDBDenseEmbedder,
    VikingDBHybridEmbedder,
)
from openviking_cli.utils.config.embedding_config import EmbeddingConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("credentials_mode", ["direct", "credential", "long-lived", "account"])
async def test_vikingdb_dense_from_config_signs_and_reads_embedding(monkeypatch, credentials_mode):
    byteplus_host = "api-vikingdb.vikingdb.ap-southeast-1.bytepluses.com"
    default_host = "api-vikingdb.vikingdb.ap-southeast-1.volces.com"
    token = "test-session-token" if credentials_mode in ("direct", "credential") else None
    dense = {
        "provider": "vikingdb",
        "model": "bge-m3",
        "dimension": 1024,
        "input": "text",
        "region": "ap-southeast-1",
        "ak": "test-ak",
        "sk": "test-sk",
    }
    if token:
        dense["host"] = byteplus_host
        if credentials_mode == "credential":
            dense["credentials"] = [{"session_token": token}]
        else:
            dense["session_token"] = token
    host = byteplus_host if token else default_host
    vector = [0.125] * 1024
    field = "dense" if token else "dense_embedding"
    payload = {"result": {"data": [{field: vector}]}}

    def check_request(url, headers):
        assert str(url) == f"https://{host}/api/vikingdb/embedding"
        assert headers["Host"] == host
        assert headers.get("X-Security-Token") == token
        assert "Authorization" in headers

    def sync_request(self, *, url, headers, **kwargs):
        check_request(url, headers)
        return SimpleNamespace(status_code=200, json=lambda: payload)

    def async_request(request):
        check_request(request.url, request.headers)
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(requests.Session, "request", sync_request)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        partial(httpx.AsyncClient, transport=httpx.MockTransport(async_request)),
    )

    account = None
    if credentials_mode == "account":
        dense["session_token"] = "cluster-session-token"
        account = {
            "dense": {
                "model": "bge-m3",
                "dimension": 1024,
                "credentials": [
                    {
                        "provider": "vikingdb",
                        "ak": "account-ak",
                        "sk": "account-sk",
                        "region": "ap-southeast-1",
                    }
                ],
            },
        }
    cluster = EmbeddingConfig.model_validate({"dense": dense})
    embedder = resolve_effective_embedding(cluster, account).get_embedder()
    assert embedder.embed("hello").dense_vector == vector
    assert (await embedder.embed_async("hello")).dense_vector == vector


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
