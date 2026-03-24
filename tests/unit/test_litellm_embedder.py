# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for LiteLLM Embedder and factory integration."""

from unittest.mock import MagicMock, patch

import pytest

from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def _mock_litellm_response(vectors=None, usage=None):
    """Create a mock litellm embedding response."""
    if vectors is None:
        vectors = [[0.1] * 1536]
    response = MagicMock()
    response.data = [{"embedding": v} for v in vectors]
    response.usage = usage
    return response


class TestLiteLLMDenseEmbedder:
    """Test cases for LiteLLMDenseEmbedder."""

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_embed_basic(self, mock_litellm):
        """Basic embedding should return a dense vector."""
        mock_litellm.embedding.return_value = _mock_litellm_response()

        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        embedder = LiteLLMDenseEmbedder(
            model_name="openai/text-embedding-3-small",
            api_key="test-key",
            dimension=1536,
        )
        result = embedder.embed("Hello world")

        assert result.dense_vector is not None
        assert len(result.dense_vector) == 1536
        mock_litellm.embedding.assert_called()
        call_kwargs = mock_litellm.embedding.call_args[1]
        assert call_kwargs["model"] == "openai/text-embedding-3-small"
        assert call_kwargs["api_key"] == "test-key"

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_embed_with_api_base(self, mock_litellm):
        """api_base should be forwarded to litellm."""
        mock_litellm.embedding.return_value = _mock_litellm_response()

        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        embedder = LiteLLMDenseEmbedder(
            model_name="openai/text-embedding-3-small",
            api_key="test-key",
            api_base="https://openrouter.ai/api/v1",
            dimension=1536,
        )
        embedder.embed("Hello")

        call_kwargs = mock_litellm.embedding.call_args[1]
        assert call_kwargs["api_base"] == "https://openrouter.ai/api/v1"

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_embed_batch(self, mock_litellm):
        """Batch embedding should return multiple results."""
        vectors = [[0.1] * 1536, [0.2] * 1536]
        mock_litellm.embedding.return_value = _mock_litellm_response(vectors)

        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        embedder = LiteLLMDenseEmbedder(
            model_name="openai/text-embedding-3-small",
            api_key="test-key",
            dimension=1536,
        )
        results = embedder.embed_batch(["Hello", "World"])

        assert len(results) == 2
        assert results[0].dense_vector[0] == 0.1
        assert results[1].dense_vector[0] == 0.2

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_embed_batch_empty(self, mock_litellm):
        """Empty batch should return empty list without API call."""
        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        embedder = LiteLLMDenseEmbedder(
            model_name="openai/text-embedding-3-small",
            api_key="test-key",
            dimension=1536,
        )
        results = embedder.embed_batch([])

        assert results == []
        mock_litellm.embedding.assert_not_called()

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_embed_non_symmetric_query(self, mock_litellm):
        """Query param should be forwarded as input_type."""
        mock_litellm.embedding.return_value = _mock_litellm_response()

        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        embedder = LiteLLMDenseEmbedder(
            model_name="openai/text-embedding-3-small",
            api_key="test-key",
            dimension=1536,
            query_param="query",
        )
        embedder.embed("search query", is_query=True)

        call_kwargs = mock_litellm.embedding.call_args[1]
        assert call_kwargs["input_type"] == "query"

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_embed_non_symmetric_document(self, mock_litellm):
        """Document param should be forwarded as input_type."""
        mock_litellm.embedding.return_value = _mock_litellm_response()

        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        embedder = LiteLLMDenseEmbedder(
            model_name="openai/text-embedding-3-small",
            api_key="test-key",
            dimension=1536,
            document_param="passage",
        )
        embedder.embed("document text", is_query=False)

        call_kwargs = mock_litellm.embedding.call_args[1]
        assert call_kwargs["input_type"] == "passage"

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_embed_no_extra_body_when_symmetric(self, mock_litellm):
        """No input_type or extra_body when symmetric mode."""
        mock_litellm.embedding.return_value = _mock_litellm_response()

        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        embedder = LiteLLMDenseEmbedder(
            model_name="openai/text-embedding-3-small",
            api_key="test-key",
            dimension=1536,
        )
        embedder.embed("Hello world")

        call_kwargs = mock_litellm.embedding.call_args[1]
        assert "input_type" not in call_kwargs
        assert "extra_body" not in call_kwargs

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_embed_key_value_param(self, mock_litellm):
        """Key=value format params should be sent as extra_body."""
        mock_litellm.embedding.return_value = _mock_litellm_response()

        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        embedder = LiteLLMDenseEmbedder(
            model_name="openai/text-embedding-3-small",
            api_key="test-key",
            dimension=1536,
            query_param="input_type=query,task=search",
        )
        embedder.embed("query text", is_query=True)

        call_kwargs = mock_litellm.embedding.call_args[1]
        assert call_kwargs["extra_body"] == {"input_type": "query", "task": "search"}

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_get_dimension(self, mock_litellm):
        """get_dimension should return the configured dimension."""
        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        embedder = LiteLLMDenseEmbedder(
            model_name="openai/text-embedding-3-small",
            api_key="test-key",
            dimension=1024,
        )
        assert embedder.get_dimension() == 1024

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_no_api_key_allowed(self, mock_litellm):
        """litellm allows no api_key (uses env vars)."""
        mock_litellm.embedding.return_value = _mock_litellm_response()

        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        embedder = LiteLLMDenseEmbedder(
            model_name="ollama/nomic-embed-text",
            dimension=768,
        )
        embedder.embed("test")

        call_kwargs = mock_litellm.embedding.call_args[1]
        assert "api_key" not in call_kwargs

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_extra_headers_forwarded(self, mock_litellm):
        """Extra headers should be forwarded to litellm."""
        mock_litellm.embedding.return_value = _mock_litellm_response()

        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        headers = {"HTTP-Referer": "https://mysite.com", "X-Title": "MyApp"}
        embedder = LiteLLMDenseEmbedder(
            model_name="openai/text-embedding-3-small",
            api_key="test-key",
            dimension=1536,
            extra_headers=headers,
        )
        embedder.embed("test")

        call_kwargs = mock_litellm.embedding.call_args[1]
        assert call_kwargs["extra_headers"] == headers


class TestLiteLLMEmbeddingFactory:
    """Test the factory creates LiteLLMDenseEmbedder correctly."""

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_factory_creates_litellm_embedder(self, mock_litellm):
        """EmbeddingConfig factory should create LiteLLMDenseEmbedder for provider='litellm'."""
        mock_litellm.embedding.return_value = _mock_litellm_response()

        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        cfg = EmbeddingModelConfig(
            provider="litellm",
            model="openai/text-embedding-3-small",
            api_key="test-key",
            api_base="https://openrouter.ai/api/v1",
            dimension=1536,
        )
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("litellm", "dense", cfg)

        assert isinstance(embedder, LiteLLMDenseEmbedder)
        assert embedder.model_name == "openai/text-embedding-3-small"
        assert embedder.api_key == "test-key"
        assert embedder.api_base == "https://openrouter.ai/api/v1"

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_factory_forwards_query_document_params(self, mock_litellm):
        """Factory should forward query_param and document_param."""
        mock_litellm.embedding.return_value = _mock_litellm_response()

        cfg = EmbeddingModelConfig(
            provider="litellm",
            model="openai/text-embedding-3-small",
            api_key="test-key",
            dimension=1536,
            query_param="query",
            document_param="passage",
        )
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("litellm", "dense", cfg)

        assert embedder.query_param == "query"
        assert embedder.document_param == "passage"

    def test_config_validation_accepts_litellm(self):
        """EmbeddingModelConfig should accept 'litellm' as a valid provider."""
        cfg = EmbeddingModelConfig(
            provider="litellm",
            model="openai/text-embedding-3-small",
            dimension=1536,
        )
        assert cfg.provider == "litellm"

    def test_config_validation_litellm_no_api_key_ok(self):
        """litellm provider should not require api_key."""
        cfg = EmbeddingModelConfig(
            provider="litellm",
            model="ollama/nomic-embed-text",
            dimension=768,
        )
        assert cfg.api_key is None

    def test_config_validation_litellm_requires_dimension(self):
        """litellm provider should require dimension to be set."""
        with pytest.raises(ValueError, match="dimension"):
            EmbeddingModelConfig(
                provider="litellm",
                model="openai/text-embedding-3-small",
            )

    @patch("openviking.models.embedder.litellm_embedders.litellm")
    def test_dimension_required_in_embedder(self, mock_litellm):
        """LiteLLMDenseEmbedder should raise ValueError when dimension is None."""
        from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

        with pytest.raises(ValueError, match="dimension"):
            LiteLLMDenseEmbedder(
                model_name="openai/text-embedding-3-small",
                api_key="test-key",
            )

    def test_factory_raises_when_litellm_not_installed(self):
        """Factory should raise clear error when litellm is not installed."""
        cfg = EmbeddingModelConfig(
            provider="litellm",
            model="openai/text-embedding-3-small",
            dimension=1536,
        )
        with patch("openviking.models.embedder.LiteLLMDenseEmbedder", None):
            with pytest.raises(ValueError, match="not installed"):
                EmbeddingConfig(dense=cfg)._create_embedder("litellm", "dense", cfg)
