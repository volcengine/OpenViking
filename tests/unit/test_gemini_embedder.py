# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for GeminiDenseEmbedder.
Pattern: patch at module import path, use MagicMock, never make real API calls.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

def _make_mock_embedding(values):
    emb = MagicMock()
    emb.values = values
    return emb


def _make_mock_result(values_list):
    result = MagicMock()
    result.embeddings = [_make_mock_embedding(v) for v in values_list]
    return result


def test_input_token_limit_constant():
    from openviking.models.embedder.gemini_embedders import _GEMINI_INPUT_TOKEN_LIMIT
    assert _GEMINI_INPUT_TOKEN_LIMIT == 8192


class TestGeminiDenseEmbedderInit:
    def test_requires_api_key(self):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        with pytest.raises(ValueError, match="api_key"):
            GeminiDenseEmbedder("gemini-embedding-2-preview")

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_init_stores_fields(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        embedder = GeminiDenseEmbedder(
            "gemini-embedding-2-preview",
            api_key="test-key",
            dimension=1536,
            task_type="RETRIEVAL_DOCUMENT",
        )
        assert embedder.model_name == "gemini-embedding-2-preview"
        assert embedder.task_type == "RETRIEVAL_DOCUMENT"
        assert embedder.get_dimension() == 1536
        mock_client_class.assert_called_once_with(api_key="test-key")

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_default_dimension_3072(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        assert embedder.get_dimension() == 3072

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_supports_multimodal_false(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        assert embedder.supports_multimodal is False


class TestGeminiDenseEmbedderEmbed:
    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_text(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1, 0.2, 0.3]])
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=3)
        result = embedder.embed("hello world")
        assert result.dense_vector is not None
        assert len(result.dense_vector) == 3
        mock_client.models.embed_content.assert_called_once()
        _, kwargs = mock_client.models.embed_content.call_args
        assert kwargs["model"] == "gemini-embedding-2-preview"

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_passes_task_type_in_config(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        embedder = GeminiDenseEmbedder(
            "gemini-embedding-2-preview", api_key="key", dimension=1, task_type="RETRIEVAL_QUERY"
        )
        embedder.embed("query text")
        _, kwargs = mock_client.models.embed_content.call_args
        assert kwargs["config"].task_type == "RETRIEVAL_QUERY"

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_raises_runtime_error_on_api_error(self, mock_client_class):
        from google.genai.errors import APIError
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        mock_client = mock_client_class.return_value
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client.models.embed_content.side_effect = APIError(401, {}, response=mock_response)
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        with pytest.raises(RuntimeError, match="Gemini embedding failed"):
            embedder.embed("hello")


class TestGeminiDenseEmbedderBatch:
    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_batch_empty(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        mock_client = mock_client_class.return_value
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        results = embedder.embed_batch([])
        assert results == []
        mock_client.models.embed_content.assert_not_called()

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_batch_single_chunk(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1], [0.2], [0.3]])
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=1)
        results = embedder.embed_batch(["a", "b", "c"])
        assert len(results) == 3
        mock_client.models.embed_content.assert_called_once()
        _, kwargs = mock_client.models.embed_content.call_args
        assert kwargs["contents"] == ["a", "b", "c"]

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_batch_chunks_at_100(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.side_effect = [
            _make_mock_result([[0.1]] * 100),
            _make_mock_result([[0.2]] * 10),
        ]
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=1)
        results = embedder.embed_batch([f"text{i}" for i in range(110)])
        assert len(results) == 110
        assert mock_client.models.embed_content.call_count == 2

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_batch_falls_back_to_individual_on_error(self, mock_client_class):
        from google.genai.errors import APIError
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        mock_client = mock_client_class.return_value
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client.models.embed_content.side_effect = [
            APIError(500, {}, response=mock_response),
            _make_mock_result([[0.1]]),
            _make_mock_result([[0.2]]),
        ]
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=1)
        results = embedder.embed_batch(["a", "b"])
        assert len(results) == 2
        assert mock_client.models.embed_content.call_count == 3


class TestGeminiDenseEmbedderAsyncBatch:
    """Unit tests for async_embed_batch (uses AsyncMock, no real API)."""

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    @pytest.mark.anyio
    async def test_async_embed_batch_dispatches_all_chunks(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        mock_client = mock_client_class.return_value
        mock_client.aio.models.embed_content = AsyncMock(side_effect=[
            _make_mock_result([[0.1]] * 100),
            _make_mock_result([[0.2]] * 10),
        ])
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=1)
        results = await embedder.async_embed_batch([f"t{i}" for i in range(110)])
        assert len(results) == 110
        assert mock_client.aio.models.embed_content.call_count == 2

    @patch("openviking.models.embedder.gemini_embedders._TEXT_BATCH_SIZE", 1)
    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    @pytest.mark.anyio
    async def test_async_embed_batch_preserves_order(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        mock_client = mock_client_class.return_value
        mock_client.aio.models.embed_content = AsyncMock(side_effect=[
            _make_mock_result([[1.0]]),
            _make_mock_result([[2.0]]),
            _make_mock_result([[3.0]]),
        ])
        embedder = GeminiDenseEmbedder(
            "gemini-embedding-2-preview", api_key="key", dimension=1, max_concurrent_batches=3
        )
        results = await embedder.async_embed_batch(["a", "b", "c"])
        # Order must match input regardless of task completion order
        assert [r.dense_vector[0] for r in results] == [1.0, 2.0, 3.0]

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    @pytest.mark.anyio
    async def test_async_embed_batch_error_fallback_to_individual(self, mock_client_class):
        from google.genai.errors import APIError
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        mock_client = mock_client_class.return_value
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client.aio.models.embed_content = AsyncMock(
            side_effect=APIError(500, {}, response=mock_response)
        )
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=1)
        results = await embedder.async_embed_batch(["a", "b"])
        assert len(results) == 2
        assert mock_client.models.embed_content.call_count == 2

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    @pytest.mark.anyio
    async def test_async_embed_batch_empty(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        assert await embedder.async_embed_batch([]) == []

    @patch("openviking.models.embedder.gemini_embedders._ANYIO_AVAILABLE", False)
    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    @pytest.mark.anyio
    async def test_async_embed_batch_raises_without_anyio(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        with pytest.raises(ImportError, match="anyio is required"):
            await embedder.async_embed_batch(["text"])
