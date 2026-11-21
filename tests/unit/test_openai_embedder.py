# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for OpenAI Embedder"""

from unittest.mock import MagicMock, patch

import pytest

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
        """OpenAI batches keep item boundaries and truncate each oversized item."""
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
            config={"max_input_tokens": 1},
        )

        embedder.embed(["first text", "x"])

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["input"] == ["firs\n...(truncated for embedding)", "x"]

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


class TestExceedContextRetry:
    """The token guard estimates from character counts, so a provider can still
    reject an input as too long. When it reports the window it enforces, the
    embedder must retry once inside that window instead of failing the write."""

    @staticmethod
    def _bad_request(n_ctx=None, key="n_ctx"):
        import httpx
        import openai

        detail = {
            "code": 400,
            "message": "request (7208 tokens) exceeds the available context size",
            "type": "exceed_context_size_error",
        }
        if n_ctx is not None:
            detail[key] = n_ctx
        body = {"error": detail}
        request = httpx.Request("POST", "http://localhost:8080/v1/embeddings")
        response = httpx.Response(400, json=body, request=request)
        return openai.BadRequestError("bad request", response=response, body=body)

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_retries_once_with_provider_context_window(self, mock_openai_class):
        import openai

        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536
        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = None

        mock_client.embeddings.create.side_effect = [
            self._bad_request(n_ctx=2560),
            mock_response,
        ]

        embedder = OpenAIDenseEmbedder(
            model_name="bge-m3",
            api_key="test-api-key",
            api_base="http://localhost:8080/v1",
        )
        embedder.max_input_tokens = 2400

        long_text = "viking://user/default/memories/entities/kanban_task\n" * 600
        result = embedder.embed([long_text])

        assert result.dense_vector is not None
        assert mock_client.embeddings.create.call_count == 2
        retried_input = mock_client.embeddings.create.call_args_list[1][1]["input"]
        assert len(retried_input[0]) < len(long_text)

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_does_not_retry_without_context_size(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.embeddings.create.side_effect = self._bad_request(n_ctx=None)

        embedder = OpenAIDenseEmbedder(
            model_name="bge-m3",
            api_key="test-api-key",
            api_base="http://localhost:8080/v1",
        )
        embedder.max_input_tokens = 2400

        with pytest.raises(RuntimeError, match="OpenAI API error"):
            embedder.embed(["some text"])

        assert mock_client.embeddings.create.call_count == 1

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_does_not_retry_when_window_leaves_text_unchanged(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.embeddings.create.side_effect = self._bad_request(n_ctx=8192)

        embedder = OpenAIDenseEmbedder(
            model_name="bge-m3",
            api_key="test-api-key",
            api_base="http://localhost:8080/v1",
        )
        embedder.max_input_tokens = 2400

        with pytest.raises(RuntimeError, match="OpenAI API error"):
            embedder.embed(["short text"])

        assert mock_client.embeddings.create.call_count == 1

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_retry_failure_surfaces_the_second_error(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.embeddings.create.side_effect = [
            self._bad_request(n_ctx=2560),
            self._bad_request(n_ctx=2560),
        ]

        embedder = OpenAIDenseEmbedder(
            model_name="bge-m3",
            api_key="test-api-key",
            api_base="http://localhost:8080/v1",
        )
        embedder.max_input_tokens = 2400

        long_text = "viking://user/default/memories/entities/kanban_task\n" * 600
        with pytest.raises(RuntimeError, match="OpenAI API error"):
            embedder.embed([long_text])

        assert mock_client.embeddings.create.call_count == 2
