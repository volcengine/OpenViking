# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
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
        """Jina embedder should include task in extra_body when configured."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1024

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        # Pass task directly
        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
            query_param="retrieval.query",
        )

        embedder.embed("Hello world", is_query=True)

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"]["task"] == "retrieval.query"

    @patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
    def test_code_model_uses_code_task_defaults(self, mock_openai_class):
        """Jina code models should use code-specific default task names."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1024

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = JinaDenseEmbedder(
            model_name="jina-code-embeddings-1.5b",
            api_key="test-api-key",
        )

        embedder.embed("Write a binary search in Python", is_query=True)

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["extra_body"]["task"] == "nl2code.query"

    @patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
    def test_code_model_keeps_explicit_task_override(self, mock_openai_class):
        """Explicit task params should override the model-specific defaults."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1024

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = JinaDenseEmbedder(
            model_name="jina-code-embeddings-1.5b",
            api_key="test-api-key",
            query_param="custom.query",
            document_param="custom.passage",
        )

        embedder.embed("Write a binary search in Python", is_query=True)

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["extra_body"]["task"] == "custom.query"

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
            query_param=None,
            document_param=None,
        )
        assert embedder._build_extra_body() is None

    @patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
    def test_build_extra_body_with_params(self, mock_openai_class):
        """_build_extra_body should include task and late_chunking."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-api-key",
            document_param="retrieval.passage",
            late_chunking=True,
        )

        extra_body = embedder._build_extra_body(is_query=False)
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

    @patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
    def test_422_task_error_actionable_message(self, mock_openai_class):
        """422 error mentioning 'task' should produce actionable RuntimeError."""
        import openai

        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        error = openai.BadRequestError(
            message="Validation error",
            response=MagicMock(status_code=422, headers={}),
            body={"detail": "Input should be 'nl2code.query' ... task"},
        )
        mock_client.embeddings.create.side_effect = error

        embedder = JinaDenseEmbedder(
            model_name="jina-code-embeddings-1.5b",
            api_key="test-key",
        )

        with pytest.raises(RuntimeError, match="query_param.*document_param"):
            embedder.embed("hello")

    @patch("openviking.models.embedder.jina_embedders.openai.OpenAI")
    def test_non_422_error_passthrough(self, mock_openai_class):
        """Non-422 API errors should use the generic 'Jina API error' message."""
        import openai

        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        error = openai.APIError(
            message="Internal server error",
            request=MagicMock(),
            body=None,
        )
        mock_client.embeddings.create.side_effect = error

        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="test-key",
        )

        with pytest.raises(RuntimeError, match="Jina API error"):
            embedder.embed("hello")

    def test_code_model_dimensions(self):
        """Code models should have correct default dimensions."""
        embedder_1_5b = JinaDenseEmbedder(
            model_name="jina-code-embeddings-1.5b",
            api_key="test-key",
        )
        assert embedder_1_5b.get_dimension() == 1024

        embedder_0_5b = JinaDenseEmbedder(
            model_name="jina-code-embeddings-0.5b",
            api_key="test-key",
        )
        assert embedder_0_5b.get_dimension() == 768
