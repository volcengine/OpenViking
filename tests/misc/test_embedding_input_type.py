# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for non-symmetric query/document embedding passthrough.

Tests EmbeddingConfig's ability to create context-specific embedders:
- OpenAI: fixed query input_type when document input_type is set
- Jina: fixed query task when task_document is set
"""

from unittest.mock import MagicMock, patch

from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


class TestEmbeddingModelConfigContextFields:
    """Test EmbeddingModelConfig fields for context-specific parameters."""

    def test_openai_query_document_param_fields_accept_values(self):
        """OpenAI config should accept query_param and document_param."""
        config = EmbeddingModelConfig(
            model="text-embedding-3-small",
            provider="openai",
            api_key="sk-test",
            query_param="search_query",
            document_param="search_document",
        )
        assert config.query_param == "search_query"
        assert config.document_param == "search_document"

    def test_jina_query_document_param_fields_accept_values(self):
        """Jina config should accept query_param and document_param."""
        config = EmbeddingModelConfig(
            model="jina-embeddings-v5-text-small",
            provider="jina",
            api_key="jina-test",
            query_param="retrieval.query",
            document_param="retrieval.passage",
        )
        assert config.query_param == "retrieval.query"
        assert config.document_param == "retrieval.passage"

    def test_context_fields_default_to_none(self):
        """Fields should default to None when not specified."""
        config = EmbeddingModelConfig(
            model="text-embedding-3-small",
            provider="openai",
            api_key="sk-test",
        )
        assert config.query_param is None
        assert config.document_param is None

    def test_query_document_param_lowercase_normalization(self):
        """Query/document value should be normalized to lowercase."""
        config = EmbeddingModelConfig(
            model="text-embedding-3-small",
            provider="openai",
            api_key="sk-test",
            query_param="SEARCH_QUERY",
            document_param="Search_Document",
        )
        assert config.query_param == "search_query"
        assert config.document_param == "search_document"

    def test_jina_query_document_param_lowercase_normalization(self):
        """Query/document task values should be normalized to lowercase."""
        config = EmbeddingModelConfig(
            model="jina-embeddings-v5-text-small",
            provider="jina",
            api_key="jina-test",
            query_param="RETRIEVAL.QUERY",
            document_param="Retrieval.Passage",
        )
        assert config.query_param == "retrieval.query"
        assert config.document_param == "retrieval.passage"


class TestEmbeddingConfigContextualEmbedders:
    """Test EmbeddingConfig passes query_param and document_param correctly."""

    @patch("openviking.models.embedder.OpenAIDenseEmbedder")
    def test_get_embedder_openai_passes_params(self, mock_embedder_class):
        """get_embedder should pass query_param and document_param to OpenAIDenseEmbedder."""
        mock_embedder_class.return_value = MagicMock()
        config = EmbeddingConfig(
            dense=EmbeddingModelConfig(
                model="text-embedding-3-small",
                provider="openai",
                api_key="sk-test",
                query_param="search_query",
                document_param="search_document",
            )
        )

        config.get_embedder()

        mock_embedder_class.assert_called_once()
        call_kwargs = mock_embedder_class.call_args[1]
        assert call_kwargs.get("query_param") == "search_query"
        assert call_kwargs.get("document_param") == "search_document"

    @patch("openviking.models.embedder.JinaDenseEmbedder")
    def test_get_embedder_jina_passes_params(self, mock_embedder_class):
        """get_embedder should pass query_param and document_param to JinaDenseEmbedder."""
        mock_embedder_class.return_value = MagicMock()
        config = EmbeddingConfig(
            dense=EmbeddingModelConfig(
                model="jina-embeddings-v5-text-small",
                provider="jina",
                api_key="jina-test",
                query_param="retrieval.query",
                document_param="retrieval.passage",
            )
        )

        config.get_embedder()

        mock_embedder_class.assert_called_once()
        call_kwargs = mock_embedder_class.call_args[1]
        assert call_kwargs.get("query_param") == "retrieval.query"
        assert call_kwargs.get("document_param") == "retrieval.passage"

    @patch("openviking.models.embedder.OpenAIDenseEmbedder")
    def test_get_embedder_openai_no_params_when_not_set(self, mock_embedder_class):
        """get_embedder should not pass query_param and document_param when not set."""
        mock_embedder_class.return_value = MagicMock()
        config = EmbeddingConfig(
            dense=EmbeddingModelConfig(
                model="text-embedding-3-small",
                provider="openai",
                api_key="sk-test",
            )
        )

        config.get_embedder()

        mock_embedder_class.assert_called_once()
        call_kwargs = mock_embedder_class.call_args[1]
        assert "query_param" not in call_kwargs
        assert "document_param" not in call_kwargs

    @patch("openviking.models.embedder.JinaDenseEmbedder")
    def test_get_embedder_jina_no_params_when_not_set(self, mock_embedder_class):
        """get_embedder should not pass query_param and document_param when not set."""
        mock_embedder_class.return_value = MagicMock()
        config = EmbeddingConfig(
            dense=EmbeddingModelConfig(
                model="jina-embeddings-v5-text-small",
                provider="jina",
                api_key="jina-test",
            )
        )

        config.get_embedder()

        mock_embedder_class.assert_called_once()
        call_kwargs = mock_embedder_class.call_args[1]
        assert "query_param" not in call_kwargs
        assert "document_param" not in call_kwargs


class TestOpenAIDenseEmbedderInputType:
    """Test OpenAIDenseEmbedder input_type support in embed and embed_batch."""

    @patch("openai.OpenAI")
    def test_embed_passes_input_type_in_extra_body(self, mock_openai_class):
        """embed should pass input_type in extra_body when provided."""
        from openviking.models.embedder import OpenAIDenseEmbedder

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1536)]
        mock_client.embeddings.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="sk-test",
            dimension=1536,
            query_param="search_query",
        )

        embedder.embed("test query", is_query=True)

        mock_client.embeddings.create.assert_called_once()
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs.get("extra_body") == {"input_type": "search_query"}

    @patch("openai.OpenAI")
    def test_embed_batch_passes_input_type_in_extra_body(self, mock_openai_class):
        """embed_batch should pass input_type in extra_body when provided."""
        from openviking.models.embedder import OpenAIDenseEmbedder

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1536), MagicMock(embedding=[0.2] * 1536)]
        mock_client.embeddings.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="sk-test",
            dimension=1536,
            document_param="search_document",
        )

        embedder.embed_batch(["doc 1", "doc 2"], is_query=False)

        mock_client.embeddings.create.assert_called_once()
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs.get("extra_body") == {"input_type": "search_document"}

    @patch("openai.OpenAI")
    def test_embed_no_extra_body_when_input_type_not_set(self, mock_openai_class):
        """embed should not set extra_body when input_type is None."""
        from openviking.models.embedder import OpenAIDenseEmbedder

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1536)]
        mock_client.embeddings.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="sk-test",
            dimension=1536,
        )

        embedder.embed("test query")

        mock_client.embeddings.create.assert_called_once()
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" not in call_kwargs


class TestJinaDenseEmbedderTask:
    """Test JinaDenseEmbedder task passthrough (already exists, verify behavior)."""

    @patch("openai.OpenAI")
    def test_embed_passes_task_in_extra_body(self, mock_openai_class):
        """embed should pass task in extra_body when provided."""
        from openviking.models.embedder import JinaDenseEmbedder

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1024)]
        mock_client.embeddings.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="jina-test",
            query_param="retrieval.query",
        )

        embedder.embed("test query", is_query=True)

        mock_client.embeddings.create.assert_called_once()
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs.get("extra_body") == {"task": "retrieval.query"}

    @patch("openai.OpenAI")
    def test_embed_batch_passes_task_in_extra_body(self, mock_openai_class):
        """embed_batch should pass task in extra_body when provided."""
        from openviking.models.embedder import JinaDenseEmbedder

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1024), MagicMock(embedding=[0.2] * 1024)]
        mock_client.embeddings.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        embedder = JinaDenseEmbedder(
            model_name="jina-embeddings-v5-text-small",
            api_key="jina-test",
            document_param="retrieval.passage",
        )

        embedder.embed_batch(["doc 1", "doc 2"], is_query=False)

        mock_client.embeddings.create.assert_called_once()
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs.get("extra_body") == {"task": "retrieval.passage"}
