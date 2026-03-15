# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openviking_cli.utils.config.embedding_config import (
    EmbeddingConfig,
    EmbeddingContextConfig,
    EmbeddingModelConfig,
)


def _fake_embedder(provider, embedder_type, config, context):
    return SimpleNamespace(
        provider=provider,
        embedder_type=embedder_type,
        config=config,
        context=context,
        is_sparse=False,
    )


def test_context_overrides_require_base_dense():
    with pytest.raises(ValueError, match="dense_query/dense_document require a base dense"):
        EmbeddingConfig(
            dense_query=EmbeddingContextConfig(model="text-embedding-3-small"),
        )


def test_query_and_document_overrides_merge_with_base(monkeypatch):
    def fake_create(self, provider, embedder_type, config, context=None):
        return _fake_embedder(provider, embedder_type, config, context)

    monkeypatch.setattr(EmbeddingConfig, "_create_embedder", fake_create)

    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            api_key="test-key",
            api_base="https://api.example.com/v1",
            model="text-embedding-3-small",
            dimension=1536,
        ),
        dense_query=EmbeddingContextConfig(model="text-embedding-3-large"),
        dense_document=EmbeddingContextConfig(dimension=1024),
    )

    query_embedder = config.get_query_embedder()
    document_embedder = config.get_document_embedder()
    legacy_embedder = config.get_embedder()

    assert query_embedder.context == "query"
    assert query_embedder.provider == "openai"
    assert query_embedder.config.model == "text-embedding-3-large"
    assert query_embedder.config.dimension == 1536
    assert query_embedder.config.api_base == "https://api.example.com/v1"

    assert document_embedder.context == "document"
    assert document_embedder.provider == "openai"
    assert document_embedder.config.model == "text-embedding-3-small"
    assert document_embedder.config.dimension == 1024
    assert document_embedder.config.api_base == "https://api.example.com/v1"

    assert legacy_embedder.context == "query"
    assert legacy_embedder.config.model == "text-embedding-3-large"
    assert config.dimension == 1024


def test_context_override_backend_alias_updates_provider(monkeypatch):
    def fake_create(self, provider, embedder_type, config, context=None):
        return _fake_embedder(provider, embedder_type, config, context)

    monkeypatch.setattr(EmbeddingConfig, "_create_embedder", fake_create)

    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            api_key="test-key",
            model="text-embedding-3-small",
            dimension=1536,
        ),
        dense_query=EmbeddingContextConfig(
            backend="jina",
            model="jina-embeddings-v5-text-small",
            dimension=1024,
        ),
    )

    query_embedder = config.get_query_embedder()

    assert query_embedder.provider == "jina"
    assert query_embedder.config.provider == "jina"
    assert query_embedder.config.backend == "jina"


@patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
def test_jina_query_and_document_embedders_apply_context_defaults(mock_openai_class):
    mock_openai_class.return_value = MagicMock()

    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="jina",
            api_key="jina-test-key",
            model="jina-embeddings-v5-text-small",
        ),
    )

    query_embedder = config.get_query_embedder()
    document_embedder = config.get_document_embedder()

    assert query_embedder.task == "retrieval.query"
    assert document_embedder.task == "retrieval.passage"
