# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for GeminiDenseEmbedder.
Pattern: patch at module import path, use MagicMock, never make real API calls.
"""

from unittest.mock import MagicMock, patch

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
