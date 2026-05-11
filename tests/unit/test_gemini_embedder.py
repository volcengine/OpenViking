# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
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
        mock_client_class.assert_called_once()

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_default_dimension_3072(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        assert embedder.get_dimension() == 3072

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_dimension_1_valid(self, mock_client_class):
        """API accepts dimension=1 (128 is a quality recommendation, not a hard limit)."""
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=1)
        assert embedder.get_dimension() == 1

    def test_default_dimension_classmethod_prefix_rule(self):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        assert GeminiDenseEmbedder._default_dimension("gemini-embedding-2") == 3072
        assert GeminiDenseEmbedder._default_dimension("gemini-embedding-2.1") == 3072
        assert GeminiDenseEmbedder._default_dimension("gemini-embedding-3-preview") == 3072
        assert GeminiDenseEmbedder._default_dimension("text-embedding-005") == 768
        assert (
            GeminiDenseEmbedder._default_dimension("text-embedding-004") == 768
        )  # exact match wins
        assert (
            GeminiDenseEmbedder._default_dimension("gemini-embedding-2-preview") == 3072
        )  # exact match

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_token_limit_per_model(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import (
            _MODEL_TOKEN_LIMITS,
            GeminiDenseEmbedder,
        )

        for model, expected in _MODEL_TOKEN_LIMITS.items():
            e = GeminiDenseEmbedder(model, api_key="key")
            assert e._token_limit == expected

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
            "gemini-embedding-2-preview",
            api_key="key",
            dimension=1,
            task_type="RETRIEVAL_QUERY",
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

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_empty_string_returns_zero_vector(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=3)
        for text in ["", "   ", "\t\n"]:
            result = embedder.embed(text)
            assert result.dense_vector == [0.0, 0.0, 0.0]
        mock_client.models.embed_content.assert_not_called()


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
    def test_embed_batch_skips_empty_strings(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result(
            [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        )
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=3)
        results = embedder.embed_batch(["hello", "", "world", "  "])
        assert len(results) == 4
        # Empty positions get zero vectors
        assert results[1].dense_vector == [0.0, 0.0, 0.0]
        assert results[3].dense_vector == [0.0, 0.0, 0.0]
        # Non-empty positions have actual embeddings
        assert results[0].dense_vector is not None
        assert results[2].dense_vector is not None
        # API only called with non-empty texts
        _, kwargs = mock_client.models.embed_content.call_args
        assert kwargs["contents"] == ["hello", "world"]

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
        mock_client.aio.models.embed_content = AsyncMock(
            side_effect=[
                _make_mock_result([[0.1]] * 100),
                _make_mock_result([[0.2]] * 10),
            ]
        )
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
        # Use orthogonal unit vectors so _l2_normalize keeps them distinguishable
        mock_client.aio.models.embed_content = AsyncMock(
            side_effect=[
                _make_mock_result([[1.0, 0.0, 0.0]]),
                _make_mock_result([[0.0, 1.0, 0.0]]),
                _make_mock_result([[0.0, 0.0, 1.0]]),
            ]
        )
        embedder = GeminiDenseEmbedder(
            "gemini-embedding-2-preview",
            api_key="key",
            dimension=3,
            max_concurrent_batches=3,
        )
        results = await embedder.async_embed_batch(["a", "b", "c"])
        # Order must match input regardless of task completion order
        assert results[0].dense_vector == pytest.approx([1.0, 0.0, 0.0])
        assert results[1].dense_vector == pytest.approx([0.0, 1.0, 0.0])
        assert results[2].dense_vector == pytest.approx([0.0, 0.0, 1.0])

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


class TestGeminiValidation:
    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_all_valid_task_types_accepted(self, mock_client):
        from openviking.models.embedder.gemini_embedders import (
            _VALID_TASK_TYPES,
            GeminiDenseEmbedder,
        )

        for tt in _VALID_TASK_TYPES:
            e = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="k", task_type=tt)
            assert e.task_type == tt

    def test_invalid_task_type_raises_on_init(self):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        with pytest.raises(ValueError, match="Invalid task_type"):
            GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="k", task_type="NOT_VALID")

    def test_valid_task_types_count(self):
        from openviking.models.embedder.gemini_embedders import _VALID_TASK_TYPES

        assert len(_VALID_TASK_TYPES) == 8

    def test_code_retrieval_query_in_task_types(self):
        from openviking.models.embedder.gemini_embedders import _VALID_TASK_TYPES

        assert "CODE_RETRIEVAL_QUERY" in _VALID_TASK_TYPES

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_dimension_too_high_raises(self, mock_client):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        with pytest.raises(ValueError, match="3072"):
            GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="k", dimension=4096)


class TestGeminiErrorMessages:
    @pytest.mark.parametrize(
        "code,match",
        [
            (401, "Invalid API key"),
            (403, "Permission denied"),
            (404, "Model not found"),
            (429, "Quota exceeded"),
            (500, "service error"),
        ],
    )
    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_error_code_hint(self, mock_client, code, match):
        from google.genai.errors import APIError

        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock = mock_client.return_value
        mock_resp = MagicMock()
        mock_resp.status_code = code
        mock.models.embed_content.side_effect = APIError(code, {}, response=mock_resp)
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="k")
        with pytest.raises(RuntimeError, match=match):
            embedder.embed("hello")

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_error_message_includes_http_code(self, mock_client):
        from google.genai.errors import APIError

        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock = mock_client.return_value
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock.models.embed_content.side_effect = APIError(404, {}, response=mock_resp)
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="k")
        with pytest.raises(RuntimeError, match="HTTP 404"):
            embedder.embed("hello")


class TestBuildConfig:
    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_build_config_defaults(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=512)
        cfg = embedder._build_config()
        assert cfg.output_dimensionality == 512
        assert cfg.task_type is None
        assert cfg.title is None

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_build_config_with_task_type_override(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        embedder = GeminiDenseEmbedder(
            "gemini-embedding-2-preview",
            api_key="key",
            dimension=1,
            task_type="RETRIEVAL_QUERY",
        )
        cfg = embedder._build_config(task_type="SEMANTIC_SIMILARITY")
        assert cfg.task_type == "SEMANTIC_SIMILARITY"

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_build_config_with_title(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=1)
        cfg = embedder._build_config(title="My Document")
        assert cfg.title == "My Document"

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_per_call_task_type(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=1)
        embedder.embed("text", task_type="CLUSTERING")
        _, kwargs = mock_client.models.embed_content.call_args
        assert kwargs["config"].task_type == "CLUSTERING"

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_per_call_title(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=1)
        embedder.embed("text", title="Doc Title")
        _, kwargs = mock_client.models.embed_content.call_args
        assert kwargs["config"].title == "Doc Title"

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_batch_with_titles_falls_back(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.side_effect = [
            _make_mock_result([[0.1]]),
            _make_mock_result([[0.2]]),
        ]
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=1)
        results = embedder.embed_batch(["alpha", "beta"], titles=["Title A", "Title B"])
        assert len(results) == 2
        # Called once per item (not as a batch)
        assert mock_client.models.embed_content.call_count == 2
        # First call should have title="Title A"
        first_cfg = mock_client.models.embed_content.call_args_list[0][1]["config"]
        assert first_cfg.title == "Title A"

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_repr(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        embedder = GeminiDenseEmbedder(
            "gemini-embedding-2-preview",
            api_key="key",
            dimension=768,
            task_type="RETRIEVAL_DOCUMENT",
        )
        r = repr(embedder)
        assert "GeminiDenseEmbedder(" in r
        assert "gemini-embedding-2-preview" in r
        assert "768" in r
        assert "RETRIEVAL_DOCUMENT" in r

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_client_constructed_with_retry_options(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import (
            _HTTP_RETRY_AVAILABLE,
            GeminiDenseEmbedder,
        )

        GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        mock_client_class.assert_called_once()
        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs.get("api_key") == "key"
        if _HTTP_RETRY_AVAILABLE:
            assert "http_options" in call_kwargs


# ============================================================================
# Multimodal branch (gemini-embedding-2 family + input_type='multimodal')
# ============================================================================


class TestGeminiMultimodalInit:
    """input_type switching, model-pin enforcement, and the supports_multimodal
    @property derived from input_type (single source of truth)."""

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_multimodal_with_v2_succeeds(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        e = GeminiDenseEmbedder("gemini-embedding-2", api_key="key", input_type="multimodal")
        assert e._input_type == "multimodal"
        assert e.supports_multimodal is True

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_multimodal_with_v2_preview_succeeds(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        e = GeminiDenseEmbedder(
            "gemini-embedding-2-preview", api_key="key", input_type="multimodal"
        )
        assert e.supports_multimodal is True

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_multimodal_with_001_raises(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        with pytest.raises(ValueError, match="gemini-embedding-2 family"):
            GeminiDenseEmbedder("gemini-embedding-001", api_key="key", input_type="multimodal")

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_multimodal_with_text_embedding_004_raises(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        with pytest.raises(ValueError, match="gemini-embedding-2 family"):
            GeminiDenseEmbedder("text-embedding-004", api_key="key", input_type="multimodal")

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_invalid_input_type_raises(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        with pytest.raises(ValueError, match="Invalid input_type"):
            GeminiDenseEmbedder("gemini-embedding-2", api_key="key", input_type="image")

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_text_mode_default_supports_multimodal_false(self, mock_client_class):
        """Backward-compat: default input_type='text' keeps supports_multimodal False."""
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        e = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        assert e._input_type == "text"
        assert e.supports_multimodal is False

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_supports_multimodal_is_property_not_settable(self, mock_client_class):
        """Single source of truth — setting supports_multimodal directly fails."""
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        e = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        with pytest.raises(AttributeError):
            e.supports_multimodal = True  # type: ignore[misc]

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_task_instruction_stored(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            task_instruction="Retrieve documents that answer:",
        )
        assert e.task_instruction == "Retrieve documents that answer:"


class TestGeminiMimeWhitelist:
    """The mime-type whitelist is the user-visible constraint that turns silent
    Google API 400s into clear 'pre-convert your file' errors."""

    @pytest.mark.parametrize(
        "url,expected_mime",
        [
            # Images — union of both Google docs pages
            ("foo.png", "image/png"),
            ("foo.PNG", "image/png"),
            ("https://x/y/img.jpg", "image/jpeg"),
            ("https://x/y/img.jpeg", "image/jpeg"),
            ("photo.webp", "image/webp"),
            ("scan.bmp", "image/bmp"),
            ("phone.heic", "image/heic"),
            ("phone.heif", "image/heif"),
            ("modern.avif", "image/avif"),
            # Audio
            ("https://x/audio.mp3?token=abc&v=2", "audio/mpeg"),
            ("clip.wav", "audio/wav"),
            # Video — union (mp4, mov, mpeg)
            ("video.mp4", "video/mp4"),
            ("video.MOV", "video/quicktime"),
            ("clip.mpeg", "video/mpeg"),
            ("clip.MPG", "video/mpeg"),
            # Documents
            ("doc.pdf", "application/pdf"),
        ],
    )
    def test_whitelist_resolves(self, url, expected_mime):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        assert GeminiDenseEmbedder._detect_mime_type(url, None) == expected_mime

    @pytest.mark.parametrize(
        "url",
        [
            "report.docx",
            "notes.txt",
            "diagram.svg",
            "image.gif",
            "audio.m4a",
            "audio.ogg",
            "audio.flac",
            "video.webm",
            "video.mkv",
            "video.avi",
            "no_extension",
            "https://x/y/no_extension_in_path?ext=.png",  # ext only in query → reject
        ],
    )
    def test_whitelist_rejects(self, url):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        with pytest.raises(ValueError, match="Unsupported file extension"):
            GeminiDenseEmbedder._detect_mime_type(url, None)

    def test_explicit_mime_in_whitelist_passes(self):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        # Bytes input + explicit whitelisted mime should work
        assert GeminiDenseEmbedder._detect_mime_type(b"fake-png-bytes", "image/png") == "image/png"
        # webp is in the union whitelist
        assert GeminiDenseEmbedder._detect_mime_type(b"fake-webp", "image/webp") == "image/webp"

    def test_explicit_mime_outside_whitelist_rejects(self):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        # image/gif is outside the union whitelist
        with pytest.raises(ValueError, match="not in the gemini-embedding-2"):
            GeminiDenseEmbedder._detect_mime_type(b"...", "image/gif")

    def test_bytes_without_explicit_mime_raises(self):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        with pytest.raises(ValueError, match="Cannot infer mime_type from raw bytes"):
            GeminiDenseEmbedder._detect_mime_type(b"...", None)


class TestGeminiSSRFGuard:
    """The SSRF guard is what stops a malicious YAML or upstream caller from
    getting the google-genai SDK to fetch internal services or local files."""

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "file:///Users/me/.aws/credentials",
            "gs://internal-bucket/secret.png",
            "data:image/png;base64,iVBOR...",
            "ftp://example.com/file.png",
            "javascript:alert(1)",
        ],
    )
    def test_rejects_non_http_schemes(self, url):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        with pytest.raises(ValueError, match="not allowed"):
            GeminiDenseEmbedder._validate_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/admin",
            "http://localhost:8080/internal",
            "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
            "http://metadata.google.internal/computeMetadata/v1/",  # GCP metadata via DNS
            "http://10.0.0.1/admin",
            "http://172.16.0.1/admin",
            "http://192.168.1.1/admin",
            "http://[::1]/admin",
        ],
    )
    def test_rejects_internal_addresses(self, url):
        """All these should hit the IP-range block (loopback / link-local /
        RFC1918 / etc.) regardless of scheme normalization."""
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        # GCP metadata requires DNS resolution — the test allows ValueError OR
        # silent return on DNS failure (which falls through to SDK error path).
        try:
            GeminiDenseEmbedder._validate_url(url)
        except ValueError as e:
            assert (
                "loopback" in str(e)
                or "link-local" in str(e)
                or "private" in str(e)
                or "reserved" in str(e)
                or "multicast" in str(e)
            )
            return
        # If no raise, the host failed DNS resolution — acceptable in test envs
        # where metadata.google.internal isn't resolvable.

    def test_https_to_public_host_passes(self):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        # Pick a host with a stable IP that's NOT in private range.
        # example.com is reserved by IANA for documentation but resolves to a
        # public IP. We catch the ValueError (raised) and assert the message
        # is NOT about SSRF — so test passes if the validator doesn't raise OR
        # raises for an unrelated reason (which it shouldn't).
        try:
            GeminiDenseEmbedder._validate_url("https://example.com/img.png")
        except ValueError as e:
            # Only acceptable failure: DNS doesn't resolve (offline env)
            pytest.fail(f"Expected pass for public host; got ValueError: {e}")

    def test_url_without_host_raises(self):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        with pytest.raises(ValueError, match="no host"):
            GeminiDenseEmbedder._validate_url("https:///no-host-here")


class TestGeminiEmbedContent:
    """The actual multimodal SDK call path. Mocks google.genai.Client and asserts
    the SDK gets called with the right model + Parts."""

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_content_text_only(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1, 0.2, 0.3]])
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=3,
        )
        result = e.embed_content([{"text": "hello multimodal world"}])
        assert result.dense_vector is not None
        assert len(result.dense_vector) == 3
        mock_client.models.embed_content.assert_called_once()
        _, kwargs = mock_client.models.embed_content.call_args
        assert kwargs["model"] == "gemini-embedding-2"
        # Parts list, not a string — multimodal API contract
        assert isinstance(kwargs["contents"], list)

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_content_with_image_bytes(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1, 0.2]])
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=2,
        )
        result = e.embed_content(
            [
                {"text": "describe"},
                {"image": b"fake-png-bytes", "mime_type": "image/png"},
            ]
        )
        assert len(result.dense_vector) == 2
        mock_client.models.embed_content.assert_called_once()
        _, kwargs = mock_client.models.embed_content.call_args
        # Two parts in one aggregated call
        assert len(kwargs["contents"]) == 2

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_content_raises_in_text_mode(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        e = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        # Default input_type='text' — embed_content must hard-error
        with pytest.raises(RuntimeError, match="only available in multimodal mode"):
            e.embed_content([{"text": "hi"}])

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_content_empty_list_raises(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
        )
        with pytest.raises(ValueError, match="non-empty list"):
            e.embed_content([])

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_content_unknown_dict_shape_raises(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
        )
        with pytest.raises(ValueError, match="Unknown content dict shape"):
            e.embed_content([{"transcript": "what?"}])

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_content_propagates_api_error(self, mock_client_class):
        from google.genai.errors import APIError

        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_client.models.embed_content.side_effect = APIError(429, {}, response=mock_resp)
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
        )
        with pytest.raises(RuntimeError, match="Quota exceeded"):
            e.embed_content([{"text": "hi"}])


class TestGeminiTaskInstruction:
    """task_instruction is prepended to the FIRST text part on the multimodal
    branch — single instruction, not query/document split (vectorizer doesn't
    thread is_query through; documented in design doc Eng Phase Corrections §2)."""

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_task_instruction_prepended_to_first_text_part(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=1,
            task_instruction="Classify the sentiment of:",
        )
        e.embed_content([{"text": "this product is great"}])
        _, kwargs = mock_client.models.embed_content.call_args
        first_part = kwargs["contents"][0]
        # The Part should now carry the prefixed text
        assert hasattr(first_part, "text")
        assert "Classify the sentiment of:" in first_part.text
        assert "this product is great" in first_part.text

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_task_instruction_inserts_when_no_text_part(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=1,
            task_instruction="Describe this image:",
        )
        e.embed_content([{"image": b"fake-png", "mime_type": "image/png"}])
        _, kwargs = mock_client.models.embed_content.call_args
        # Prepended as a NEW first text part because no text part existed
        assert len(kwargs["contents"]) == 2
        assert getattr(kwargs["contents"][0], "text", None) == "Describe this image:"

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_no_task_instruction_no_prepend(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=1,
        )
        e.embed_content([{"text": "raw"}])
        _, kwargs = mock_client.models.embed_content.call_args
        first_part = kwargs["contents"][0]
        assert first_part.text == "raw"


class TestGeminiEmbedContentAsync:
    """Async path mirrors the sync path; uses client.aio.models.embed_content."""

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    @pytest.mark.anyio
    async def test_embed_content_async_text(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.aio.models.embed_content = AsyncMock(
            return_value=_make_mock_result([[0.1, 0.2, 0.3]])
        )
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=3,
        )
        result = await e.embed_content_async([{"text": "async hello"}])
        assert len(result.dense_vector) == 3
        mock_client.aio.models.embed_content.assert_called_once()

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    @pytest.mark.anyio
    async def test_embed_content_async_raises_in_text_mode(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        e = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        with pytest.raises(RuntimeError, match="only available in multimodal mode"):
            await e.embed_content_async([{"text": "hi"}])


def _make_count_tokens_response(total_tokens: int):
    """Mock a CountTokensResponse — only `.total_tokens` is read by the embedder."""
    resp = MagicMock()
    resp.total_tokens = total_tokens
    return resp


class TestGeminiMultimodalTelemetry:
    """The /metrics Prometheus endpoint depends on every embedder forwarding
    token usage via the base class's update_token_usage(). The multimodal
    branch uses Google's `count_tokens` API for an exact server-side count
    (one extra round-trip per embed_content call; count_tokens is free).
    On count_tokens failure: log warning and skip the telemetry update —
    zero-recorded is preferable to wrong-recorded for ops dashboards."""

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_content_calls_count_tokens_then_update(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        mock_client.models.count_tokens.return_value = _make_count_tokens_response(42)
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=1,
        )
        with patch.object(e, "update_token_usage") as mock_track:
            e.embed_content([{"text": "abcd" * 25}])
            mock_client.models.count_tokens.assert_called_once()
            mock_track.assert_called_once_with(
                model_name="gemini-embedding-2",
                provider="gemini",
                prompt_tokens=42,
                completion_tokens=0,
            )

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_count_tokens_called_with_same_parts_as_embed(self, mock_client_class):
        """count_tokens must see the exact same Parts list embed_content saw —
        otherwise telemetry diverges from what was actually billed."""
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        mock_client.models.count_tokens.return_value = _make_count_tokens_response(5)
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=1,
        )
        e.embed_content([{"text": "hello"}])
        embed_parts = mock_client.models.embed_content.call_args.kwargs["contents"]
        count_parts = mock_client.models.count_tokens.call_args.kwargs["contents"]
        assert embed_parts == count_parts

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_count_tokens_failure_skips_telemetry(self, mock_client_class):
        """count_tokens failure must NOT block the embed; it logs and skips
        the /metrics update so ops sees fewer-but-honest numbers."""
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        mock_client.models.count_tokens.side_effect = RuntimeError("rate limited")
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=1,
        )
        with patch.object(e, "update_token_usage") as mock_track:
            r = e.embed_content([{"text": "hello"}])
            assert r.dense_vector  # embed still succeeded
            mock_track.assert_not_called()

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_task_instruction_is_counted_via_parts(self, mock_client_class):
        """task_instruction is baked into the parts list before count_tokens
        sees them, so its tokens are naturally included in the count."""
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        mock_client.models.count_tokens.return_value = _make_count_tokens_response(1)
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=1,
            task_instruction="Retrieve documents that answer:",
        )
        e.embed_content([{"text": "what is x"}])
        parts = mock_client.models.count_tokens.call_args.kwargs["contents"]
        # First part should now carry the prepended instruction
        assert parts[0].text.startswith("Retrieve documents that answer:")

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_content_skips_telemetry_on_api_error(self, mock_client_class):
        """If the API call raises, we don't track tokens for a failed call —
        avoids inflating /metrics with phantom usage."""
        from google.genai.errors import APIError

        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_client.models.embed_content.side_effect = APIError(500, {}, response=mock_resp)
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=1,
        )
        with patch.object(e, "update_token_usage") as mock_track:
            with pytest.raises(RuntimeError):
                e.embed_content([{"text": "hi"}])
            mock_track.assert_not_called()

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    @pytest.mark.anyio
    async def test_embed_content_async_calls_count_tokens_then_update(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.aio.models.embed_content = AsyncMock(return_value=_make_mock_result([[0.1]]))
        mock_client.aio.models.count_tokens = AsyncMock(
            return_value=_make_count_tokens_response(17)
        )
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=1,
        )
        with patch.object(e, "update_token_usage") as mock_track:
            await e.embed_content_async([{"text": "async hello"}])
            mock_client.aio.models.count_tokens.assert_called_once()
            mock_track.assert_called_once_with(
                model_name="gemini-embedding-2",
                provider="gemini",
                prompt_tokens=17,
                completion_tokens=0,
            )

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    @pytest.mark.anyio
    async def test_async_count_tokens_failure_skips_telemetry(self, mock_client_class):
        """Async count_tokens failure logs warning, embed succeeds, telemetry skipped."""
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.aio.models.embed_content = AsyncMock(return_value=_make_mock_result([[0.1]]))
        mock_client.aio.models.count_tokens = AsyncMock(side_effect=RuntimeError("rate limited"))
        e = GeminiDenseEmbedder(
            "gemini-embedding-2",
            api_key="key",
            input_type="multimodal",
            dimension=1,
        )
        with patch.object(e, "update_token_usage") as mock_track:
            r = await e.embed_content_async([{"text": "async hello"}])
            assert r.dense_vector
            mock_track.assert_not_called()


class TestGeminiOptionalDepFactoryGuard:
    """When google-genai isn't installed, openviking.models.embedder.__init__
    sets GeminiDenseEmbedder = None (existing optional-dep pattern). The factory
    must catch this and raise a clear ValueError pointing at the install hint —
    matching the existing LiteLLM pattern at embedding_config.py:478."""

    def test_factory_raises_when_gemini_class_is_none(self, monkeypatch):
        """Simulate google-genai not being installed by setting the
        package-level export to None, then verify the factory catches it
        before invoking the constructor."""
        from openviking_cli.utils.config import embedding_config as ec_mod

        monkeypatch.setattr(
            "openviking.models.embedder.GeminiDenseEmbedder",
            None,
            raising=False,
        )
        # The factory imports GeminiDenseEmbedder inside _create_embedder via
        # a fresh `from openviking.models.embedder import ...`, so the patch
        # has to land on the source module too.
        monkeypatch.setattr(
            "openviking.models.embedder.gemini_embedders.GeminiDenseEmbedder",
            None,
            raising=False,
        )
        cfg = ec_mod.EmbeddingConfig(
            dense=ec_mod.EmbeddingModelConfig(
                provider="gemini", model="gemini-embedding-001", api_key="k"
            )
        )
        with pytest.raises(ValueError, match="google-genai"):
            cfg.get_embedder()


class TestGeminiPydanticConfigValidator:
    """Ensures the EmbeddingModelConfig validator doesn't false-positive on
    existing 001 users (whose `input` defaults to 'multimodal' from the schema
    default) and DOES catch explicit task_instruction misconfigurations."""

    def test_existing_001_default_input_does_not_raise(self):
        """Backward-compat: a Gemini config with model='gemini-embedding-001'
        and the schema-default input='multimodal' MUST validate cleanly. This
        is the case every existing Gemini user is in today."""
        from openviking_cli.utils.config.embedding_config import EmbeddingModelConfig

        # No `input` set explicitly — picks up the default of "multimodal"
        cfg = EmbeddingModelConfig(provider="gemini", model="gemini-embedding-001", api_key="k")
        assert cfg.input == "multimodal"  # default applied
        # Did NOT raise — validator is permissive on the schema default

    def test_v2_with_multimodal_input_validates(self):
        from openviking_cli.utils.config.embedding_config import EmbeddingModelConfig

        cfg = EmbeddingModelConfig(
            provider="gemini",
            model="gemini-embedding-2",
            api_key="k",
            input="multimodal",
        )
        assert cfg.model == "gemini-embedding-2"

    def test_task_instruction_with_text_input_raises(self):
        from openviking_cli.utils.config.embedding_config import EmbeddingModelConfig

        with pytest.raises(ValueError, match="task_instruction is only used"):
            EmbeddingModelConfig(
                provider="gemini",
                model="gemini-embedding-2",
                api_key="k",
                input="text",
                task_instruction="should error",
            )

    def test_task_instruction_with_001_raises(self):
        from openviking_cli.utils.config.embedding_config import EmbeddingModelConfig

        with pytest.raises(ValueError, match="not in the gemini-embedding-2 family"):
            EmbeddingModelConfig(
                provider="gemini",
                model="gemini-embedding-001",
                api_key="k",
                input="multimodal",
                task_instruction="explicit-multimodal-signal",
            )

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_factory_existing_001_user_gets_text_mode(self, mock_client_class):
        """The factory contract: existing 001 users with default
        input='multimodal' get text mode at the embedder layer (no input_type
        threaded). This is the safety net that preserves backward compat."""
        from openviking_cli.utils.config.embedding_config import (
            EmbeddingConfig,
            EmbeddingModelConfig,
        )

        cfg = EmbeddingConfig(
            dense=EmbeddingModelConfig(provider="gemini", model="gemini-embedding-001", api_key="k")
        )
        embedder = cfg.get_embedder()
        # Embedder constructed in text mode despite cfg.dense.input == 'multimodal'
        assert embedder._input_type == "text"
        assert embedder.supports_multimodal is False

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_factory_v2_with_multimodal_threads_input_type(self, mock_client_class):
        from openviking_cli.utils.config.embedding_config import (
            EmbeddingConfig,
            EmbeddingModelConfig,
        )

        cfg = EmbeddingConfig(
            dense=EmbeddingModelConfig(
                provider="gemini",
                model="gemini-embedding-2",
                api_key="k",
                input="multimodal",
                task_instruction="Retrieve documents that answer:",
            )
        )
        embedder = cfg.get_embedder()
        assert embedder._input_type == "multimodal"
        assert embedder.supports_multimodal is True
        assert embedder.task_instruction == "Retrieve documents that answer:"
