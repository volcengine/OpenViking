# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for Google/Gemini Embedder"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from openviking.models.embedder import GoogleDenseEmbedder
from openviking.models.embedder.google_embedders import GOOGLE_MODEL_DIMENSIONS


def _make_response(values: list) -> MagicMock:
    """Build a mock successful requests.Response with the given embedding values."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"embedding": {"values": values}}
    return mock_resp


def _make_error_response(status_code: int = 400) -> MagicMock:
    """Build a mock requests.Response that raises HTTPError on raise_for_status."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
        response=MagicMock(status_code=status_code)
    )
    return mock_resp


class TestGoogleDenseEmbedderInit:
    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key is required"):
            GoogleDenseEmbedder(model_name="gemini-embedding-2-preview")

    def test_rejects_unsupported_model(self):
        with pytest.raises(ValueError, match="Unsupported model"):
            GoogleDenseEmbedder(model_name="unknown-model", api_key="key")

    def test_rejects_dimension_exceeding_max(self):
        with pytest.raises(ValueError, match="exceeds maximum"):
            GoogleDenseEmbedder(
                model_name="gemini-embedding-2-preview",
                api_key="key",
                dimension=9999,
            )

    def test_default_dimension(self):
        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="key"
        )
        assert embedder.get_dimension() == GOOGLE_MODEL_DIMENSIONS["gemini-embedding-2-preview"]

    def test_custom_dimension(self):
        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="key", dimension=1024
        )
        assert embedder.get_dimension() == 1024

    def test_default_api_base(self):
        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="key"
        )
        assert embedder.api_base == "https://generativelanguage.googleapis.com/v1beta"

    def test_custom_api_base(self):
        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview",
            api_key="key",
            api_base="https://custom.endpoint/v1",
        )
        assert embedder.api_base == "https://custom.endpoint/v1"

    def test_default_max_tokens(self):
        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="key"
        )
        assert embedder.max_tokens == 8192

    def test_custom_max_tokens(self):
        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="key", max_tokens=4096
        )
        assert embedder.max_tokens == 4096

    def test_google_model_dimensions_constant(self):
        assert "gemini-embedding-2-preview" in GOOGLE_MODEL_DIMENSIONS
        assert GOOGLE_MODEL_DIMENSIONS["gemini-embedding-2-preview"] == 3072


class TestGoogleDenseEmbedderEmbed:
    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_returns_vector(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        result = embedder.embed("Hello world")

        assert result.dense_vector is not None
        assert len(result.dense_vector) == 3072
        mock_post.assert_called_once()

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_sends_correct_url(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        embedder.embed("Hello world")

        url = mock_post.call_args[0][0]
        assert "gemini-embedding-2-preview:embedContent" in url

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_sends_api_key_header(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="my-api-key"
        )
        embedder.embed("Hello world")

        headers = mock_post.call_args[1]["headers"]
        assert headers["x-goog-api-key"] == "my-api-key"

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_sends_text_in_parts(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        embedder.embed("Hello world")

        body = mock_post.call_args[1]["json"]
        assert body["content"]["parts"][0]["text"] == "Hello world"

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_does_not_send_task_type(self, mock_post):
        """taskType must not be sent — gemini-embedding-2-preview ignores it."""
        mock_post.return_value = _make_response([0.1] * 3072)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        embedder.embed("Hello world", is_query=True)
        embedder.embed("Hello world", is_query=False)

        for call in mock_post.call_args_list:
            body = call[1]["json"]
            assert "taskType" not in body
            assert "task_type" not in body

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_empty_text_returns_empty(self, mock_post):
        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        result = embedder.embed("")
        assert result.dense_vector is None
        mock_post.assert_not_called()

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_whitespace_text_returns_empty(self, mock_post):
        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        result = embedder.embed("   ")
        assert result.dense_vector is None
        mock_post.assert_not_called()

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_api_error_raises_runtime_error(self, mock_post):
        mock_post.return_value = _make_error_response(400)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        with pytest.raises(RuntimeError):
            embedder.embed("Hello world")

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_unexpected_response_format_raises(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"unexpected": "format"}
        mock_post.return_value = mock_resp

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        with pytest.raises(RuntimeError, match="Unexpected response format"):
            embedder.embed("Hello world")


class TestGoogleDenseEmbedderDimension:
    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_dimension_sent_as_output_dimensionality(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 1024)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview",
            api_key="test-key",
            dimension=1024,
        )
        embedder.embed("Hello world")

        body = mock_post.call_args[1]["json"]
        assert body["output_dimensionality"] == 1024

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_no_dimension_omits_output_dimensionality(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        embedder.embed("Hello world")

        body = mock_post.call_args[1]["json"]
        assert "output_dimensionality" not in body


class TestGoogleDenseEmbedderExtraHeaders:
    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_extra_headers_sent(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview",
            api_key="test-key",
            extra_headers={"X-Custom": "value"},
        )
        embedder.embed("Hello world")

        headers = mock_post.call_args[1]["headers"]
        assert headers["X-Custom"] == "value"


class TestGoogleDenseEmbedderBatch:
    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_batch_returns_results(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        results = embedder.embed_batch(["Hello", "World", "Test"])

        assert len(results) == 3
        assert mock_post.call_count == 3
        for result in results:
            assert result.dense_vector is not None

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_batch_empty_list(self, mock_post):
        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        results = embedder.embed_batch([])

        assert results == []
        mock_post.assert_not_called()

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_batch_skips_empty_texts(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        results = embedder.embed_batch(["Hello", "", "World"])

        assert len(results) == 3
        assert results[1].dense_vector is None
        assert mock_post.call_count == 2

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_batch_does_not_send_task_type(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        embedder.embed_batch(["Hello", "World"], is_query=True)

        for call in mock_post.call_args_list:
            body = call[1]["json"]
            assert "taskType" not in body
            assert "task_type" not in body


class TestGoogleDenseEmbedderChunking:
    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_oversized_text_is_chunked(self, mock_post):
        """Text exceeding max_tokens should be split and embeddings averaged."""
        mock_post.return_value = _make_response([0.5] * 3072)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview",
            api_key="test-key",
            max_tokens=5,
        )
        result = embedder.embed("word " * 100)

        assert result.dense_vector is not None
        assert mock_post.call_count > 1

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_small_text_not_chunked(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)

        embedder = GoogleDenseEmbedder(
            model_name="gemini-embedding-2-preview", api_key="test-key"
        )
        embedder.embed("Hello world")

        assert mock_post.call_count == 1
