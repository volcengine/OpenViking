# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for temporary LiteLLM hard-disable behavior."""

import pytest

from openviking_cli.utils.config.embedding_config import EmbeddingModelConfig
from openviking_cli.utils.config.rerank_config import RerankConfig


def test_embedding_config_rejects_litellm_provider():
    """Embedding config should fail fast when LiteLLM is selected."""
    with pytest.raises(ValueError, match="temporarily disabled"):
        EmbeddingModelConfig(
            provider="litellm",
            model="openai/text-embedding-3-small",
            dimension=1536,
        )


def test_rerank_config_rejects_litellm_provider():
    """Rerank config should fail fast when LiteLLM is selected."""
    with pytest.raises(ValueError, match="temporarily disabled"):
        RerankConfig(
            provider="litellm",
            model="cohere/rerank-v3.5",
        )
