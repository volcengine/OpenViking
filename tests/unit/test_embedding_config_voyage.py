# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for Voyage embedding configuration."""

import pytest

from openviking.models.embedder import VoyageDenseEmbedder
from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def test_voyage_provider_requires_api_key():
    with pytest.raises(ValueError, match="Voyage provider requires 'api_key'"):
        EmbeddingModelConfig(provider="voyage", model="voyage-4-lite")


def test_voyage_dense_dimension_defaults_to_model_dimension():
    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="voyage",
            model="voyage-4-lite",
            api_key="voyage-key",
        )
    )

    assert config.dimension == 1024


def test_voyage_dense_dimension_honors_explicit_output_dimension():
    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="voyage",
            model="voyage-4-lite",
            api_key="voyage-key",
            dimension=512,
        )
    )

    assert config.dimension == 512


def test_voyage_get_embedder_returns_voyage_dense_embedder():
    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="voyage",
            model="voyage-4-lite",
            api_key="voyage-key",
        )
    )

    embedder = config.get_embedder()
    assert isinstance(embedder, VoyageDenseEmbedder)
    assert embedder.get_dimension() == 1024
