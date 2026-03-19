# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for optional max_input_tokens truncation before embedding API calls."""

from unittest.mock import MagicMock, patch

from openviking.models.embedder.base import (
    EMBEDDING_CHARS_PER_TOKEN_HEURISTIC,
    truncate_embedding_input_text,
)
from openviking.models.embedder import OpenAIDenseEmbedder


def test_truncate_embedding_input_text_respects_limit():
    limit = 10
    long = "a" * (limit * EMBEDDING_CHARS_PER_TOKEN_HEURISTIC + 5)
    out = truncate_embedding_input_text(long, limit, model_name="m")
    assert len(out) == limit * EMBEDDING_CHARS_PER_TOKEN_HEURISTIC


def test_truncate_embedding_input_text_none_means_no_truncation():
    long = "x" * 1000
    assert truncate_embedding_input_text(long, None, model_name="m") == long


@patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
def test_openai_embed_truncates_when_max_input_tokens_set(mock_openai_class):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_embedding = MagicMock()
    mock_embedding.embedding = [0.1] * 8
    mock_response = MagicMock()
    mock_response.data = [mock_embedding]
    mock_client.embeddings.create.return_value = mock_response

    limit = 2
    max_chars = limit * EMBEDDING_CHARS_PER_TOKEN_HEURISTIC
    long_text = "b" * (max_chars + 20)

    embedder = OpenAIDenseEmbedder(
        model_name="text-embedding-3-small",
        api_key="k",
        max_input_tokens=limit,
    )
    embedder.embed(long_text)

    sent = mock_client.embeddings.create.call_args[1]["input"]
    assert len(sent) == max_chars
