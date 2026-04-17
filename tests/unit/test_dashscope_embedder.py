# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the DashScope dense embedder wrapper."""

from unittest.mock import MagicMock, patch

import pytest

from openviking.models.embedder import DashScopeDenseEmbedder


def _make_mock_client(vector_size: int = 8):
    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * vector_size)],
        usage=None,
    )
    return mock_client


@patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
def test_dashscope_embedder_uses_default_api_base_and_is_text_only(mock_openai_class):
    mock_openai_class.return_value = _make_mock_client()

    embedder = DashScopeDenseEmbedder(
        model_name="text-embedding-v4",
        api_key="dash-key",
        dimension=8,
    )

    assert embedder.provider == "dashscope"
    assert embedder._provider == "dashscope"
    assert embedder.supports_multimodal is False
    assert embedder.input_type == "text"

    mock_openai_class.assert_called_once()
    call_kwargs = mock_openai_class.call_args[1]
    assert call_kwargs["base_url"] == DashScopeDenseEmbedder.DEFAULT_API_BASE


def test_dashscope_embedder_rejects_multimodal_input():
    with pytest.raises(ValueError, match="multimodal"):
        DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="dash-key",
            dimension=8,
            input_type="multimodal",
        )


@patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
def test_dashscope_embed_does_not_send_dimensions_and_truncates_locally(mock_openai_class):
    mock_client = _make_mock_client(vector_size=12)
    mock_openai_class.return_value = mock_client

    embedder = DashScopeDenseEmbedder(
        model_name="text-embedding-v4",
        api_key="dash-key",
        dimension=8,
    )
    result = embedder.embed("hello dashscope")

    assert len(result.dense_vector) == 8

    mock_client.embeddings.create.assert_called_once()
    call_kwargs = mock_client.embeddings.create.call_args[1]
    assert call_kwargs["input"] == "hello dashscope"
    assert call_kwargs["model"] == "text-embedding-v4"
    assert "dimensions" not in call_kwargs
