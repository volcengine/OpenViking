# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for DashScope embedding config and factory wiring."""

from unittest.mock import MagicMock, patch

import pytest

from openviking.models.embedder import DashScopeDenseEmbedder
from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def _make_mock_client():
    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 8)],
        usage=None,
    )
    return mock_client


def test_dashscope_config_defaults_input_to_text():
    cfg = EmbeddingModelConfig(
        provider="dashscope",
        model="text-embedding-v4",
        api_key="dash-key",
    )

    assert cfg.provider == "dashscope"
    assert cfg.input == "text"


def test_dashscope_backend_sync_defaults_input_to_text():
    cfg = EmbeddingModelConfig(
        backend="dashscope",
        model="text-embedding-v4",
        api_key="dash-key",
    )

    assert cfg.provider == "dashscope"
    assert cfg.input == "text"


def test_dashscope_config_rejects_multimodal_input():
    with pytest.raises(ValueError, match="dense text embeddings only"):
        EmbeddingModelConfig(
            provider="dashscope",
            model="text-embedding-v4",
            api_key="dash-key",
            input="multimodal",
        )


def test_dashscope_config_requires_api_key():
    with pytest.raises(ValueError, match="DashScope provider requires 'api_key'"):
        EmbeddingModelConfig(
            provider="dashscope",
            model="text-embedding-v4",
        )


@patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
def test_dashscope_factory_creates_dashscope_embedder_with_default_api_base(mock_openai_class):
    mock_openai_class.return_value = _make_mock_client()

    cfg = EmbeddingModelConfig(
        provider="dashscope",
        model="text-embedding-v4",
        api_key="dash-key",
        dimension=8,
    )
    embedder = EmbeddingConfig(dense=cfg)._create_embedder("dashscope", "dense", cfg)

    assert isinstance(embedder, DashScopeDenseEmbedder)
    assert embedder.provider == "dashscope"
    assert embedder.input_type == "text"

    mock_openai_class.assert_called_once()
    call_kwargs = mock_openai_class.call_args[1]
    assert call_kwargs["api_key"] == "dash-key"
    assert call_kwargs["base_url"] == DashScopeDenseEmbedder.DEFAULT_API_BASE
