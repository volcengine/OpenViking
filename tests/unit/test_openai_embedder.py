# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for OpenAI Embedder"""

from unittest.mock import MagicMock, patch

from openviking.models.embedder import OpenAIDenseEmbedder


class TestOpenAIDenseEmbedder:
    """Test cases for OpenAIDenseEmbedder"""

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_embed_does_not_send_dimensions(self, mock_openai_class):
        """OpenAI embed should omit dimensions param"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=1024,
        )

        embedder.embed("Hello world")

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "dimensions" not in call_kwargs

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_embed_with_input_type_none(self, mock_openai_class):
        """OpenAI embed should not include extra_body when input_type is None"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
        )

        embedder.embed("Hello world")

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" not in call_kwargs

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_default_input_type_embed_downgrades_multimodal_parts(self, mock_openai_class):
        """Direct embed(parts) should also apply the text-only input guard."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=1536,
        )
        parts = [
            {"type": "text", "text": "find this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
        ]

        embedder.embed(parts)

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["input"] == "find this"

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_embed_preserves_openai_batch_text_input(self, mock_openai_class):
        """OpenAI batch text input should keep the historical list[str] passthrough."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=1536,
        )

        embedder.embed(["first text", "second text"])

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["input"] == ["first text", "second text"]

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_multimodal_input_type_passes_content_parts(self, mock_openai_class):
        """OpenAI-compatible multimodal mode should pass content parts to embeddings.create."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="custom-multimodal-embedding",
            api_key="test-api-key",
            api_base="https://example.com/v1",
            dimension=1536,
            input_type="multimodal",
        )
        parts = [
            {"type": "text", "text": "find this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
        ]

        assert embedder.supports_multimodal is True
        assert embedder.prepare_embedding_input(parts) == parts

        embedder.embed(parts)

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["input"] == parts

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_embed_with_context_query(self, mock_openai_class):
        """OpenAI embed should include extra_body with input_type='query' when is_query=True"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            query_param="query",
        )

        embedder.embed("Hello world", is_query=True)

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"] == {"input_type": "query"}

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_embed_with_context_document(self, mock_openai_class):
        """OpenAI embed should include extra_body with input_type='passage' when is_query=False"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            document_param="passage",
        )

        embedder.embed("Hello world", is_query=False)

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"] == {"input_type": "passage"}

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_telemetry_skipped_when_no_usage(self, mock_openai_class):
        """_update_telemetry_token_usage should no-op when response has no usage"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = None
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=1536,
        )
        result = embedder.embed("Hello world")
        assert result.dense_vector is not None

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_telemetry_skipped_when_module_missing(self, mock_openai_class):
        """_update_telemetry_token_usage should silently no-op when telemetry module is not available"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.total_tokens = 10

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = mock_usage
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=1536,
        )

        with patch("importlib.import_module", side_effect=ImportError("no telemetry")):
            result = embedder.embed("Hello world")

        assert result.dense_vector is not None

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_telemetry_called_when_module_available(self, mock_openai_class):
        """_update_telemetry_token_usage should call telemetry when module is available"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 8
        mock_usage.total_tokens = 8

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = mock_usage
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=1536,
        )

        mock_telemetry = MagicMock()

        with patch(
            "openviking.models.embedder.openai_embedders.get_current_telemetry",
            return_value=mock_telemetry,
        ):
            result = embedder.embed("Hello world")

        assert result.dense_vector is not None
        mock_telemetry.add_token_usage_by_source.assert_called_once_with("embedding", 8, 0)
