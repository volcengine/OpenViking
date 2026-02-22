# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for Jina AI Embedder"""

from unittest.mock import MagicMock, patch

import pytest

from openviking.models.embedder import JinaDenseEmbedder
from openviking.models.embedder.jina_embedders import (
    JINA_MODEL_DIMENSIONS,
)


class TestJinaDenseEmbedder:
    """Test cases for JinaDenseEmbedder"""

    def test_init_requires_api_key(self):
        """Test that api_key is required"""
        with pytest.raises(ValueError, match="api_key is required"):
            JinaDenseEmbedder(model_name="jina-embeddings-v5-text-small")

    def test_init_with_api_key(self):
        """Test initialization with api_key"""
        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
        )
        assert embedder.api_key == "test-api-key"
        assert embedder.model_name == "jina-embeddings-v5-text-small"
        assert embedder.api_base == "https://api.jina.ai/v1"

    def test_init_with_custom_api_base(self):
        """Test initialization with custom api_base"""
        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
            api_base="https://custom.api.jina.ai/v1",
        )
        assert embedder.api_base == "https://custom.api.jina.ai/v1"

    def test_default_dimension_v5_small(self):
        """Test default dimension for jina-embeddings-v5-text-small"""
        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
        )
        assert embedder.get_dimension() == 1024

    def test_default_dimension_v5_nano(self):
        """Test default dimension for jina-embeddings-v5-text-nano"""
        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-nano",
            api_key="test-api-key",
        )
        assert embedder.get_dimension() == 768

    def test_custom_dimension(self):
        """Test custom dimension for Matryoshka reduction"""
        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
            dimension=256,
        )
        assert embedder.get_dimension() == 256

    def test_model_dimensions_constant(self):
        """Test JINA_MODEL_DIMENSIONS constant"""
        assert "jina-embeddings-v5-text-small" in JINA_MODEL_DIMENSIONS
        assert "jina-embeddings-v5-text-nano" in JINA_MODEL_DIMENSIONS
        assert JINA_MODEL_DIMENSIONS["jina-embeddings-v5-text-small"] == 1024
        assert JINA_MODEL_DIMENSIONS["jina-embeddings-v5-text-nano"] == 768

    @patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
    def test_embed_single_text(self, mock_openai_class):
        """Test embedding a single text"""
        # Setup mock
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1024

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        # Create embedder and embed
        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
        )
        result = embedder.embed("Hello world")

        # Verify
        assert result.dense_vector is not None
        assert len(result.dense_vector) == 1024
        mock_client.embeddings.create.assert_called_once()

    @patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
    def test_embed_with_dimension(self, mock_openai_class):
        """Test embedding with custom dimension parameter"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 768

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
            dimension=768,
        )
        embedder.embed("Hello world")

        # Check dimensions was passed
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["dimensions"] == 768

    @patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
    def test_embed_with_task(self, mock_openai_class):
        """Test embedding with task parameter"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1024

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
            task="retrieval.query",
        )
        embedder.embed("Hello world")

        # Check extra_body was passed with task
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"]["task"] == "retrieval.query"

    @patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
    def test_embed_with_late_chunking(self, mock_openai_class):
        """Test embedding with late_chunking parameter"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1024

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
            late_chunking=True,
        )
        embedder.embed("Hello world")

        # Check extra_body was passed with late_chunking
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"]["late_chunking"] is True

    @patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
    def test_embed_batch(self, mock_openai_class):
        """Test batch embedding"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embeddings = [MagicMock(embedding=[0.1] * 1024) for _ in range(3)]

        mock_response = MagicMock()
        mock_response.data = mock_embeddings
        mock_client.embeddings.create.return_value = mock_response

        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
        )
        results = embedder.embed_batch(["Hello", "World", "Test"])

        assert len(results) == 3
        for result in results:
            assert result.dense_vector is not None
            assert len(result.dense_vector) == 1024

    @patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
    def test_embed_batch_empty(self, mock_openai_class):
        """Test batch embedding with empty list"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
        )
        results = embedder.embed_batch([])

        assert results == []
        mock_client.embeddings.create.assert_not_called()

    @patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
    def test_embed_api_error(self, mock_openai_class):
        """Test embedding with API error"""
        import openai

        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_client.embeddings.create.side_effect = openai.APIError(
            message="Test API error",
            request=MagicMock(),
            body=None,
        )

        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
        )

        with pytest.raises(RuntimeError, match="Jina API error"):
            embedder.embed("Hello world")

    def test_build_extra_body_none(self):
        """Test _build_extra_body returns None when no params set"""
        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
        )
        assert embedder._build_extra_body() is None

    def test_build_extra_body_with_params(self):
        """Test _build_extra_body returns dict with params"""
        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
            task="retrieval.passage",
            late_chunking=True,
        )
        extra_body = embedder._build_extra_body()
        assert extra_body is not None
        assert extra_body["task"] == "retrieval.passage"
        assert extra_body["late_chunking"] is True

    def test_dimension_validation_exceeds_max(self):
        """Test that requesting dimension exceeding model max raises ValueError"""
        with pytest.raises(ValueError, match="exceeds maximum"):
            JinaDenseEmbedder(
                model_name="jina-embeddings-v5-text-nano",
                api_key="test-key",
                dimension=1024,  # nano max is 768
            )

    def test_dimension_validation_within_range(self):
        """Test that requesting dimension within model max works"""
        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-nano",
            api_key="test-key",
            dimension=256,
        )
        assert embedder.get_dimension() == 256
