# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for Google/Gemini Embedder"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from openviking.models.embedder import GoogleDenseEmbedder


def _make_response(values: list) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"embedding": {"values": values}}
    return mock_resp


def _embedder(**kwargs) -> GoogleDenseEmbedder:
    return GoogleDenseEmbedder(
        model_name="gemini-embedding-2-preview", api_key="test-key", **kwargs
    )


class TestGoogleDenseEmbedderInit:
    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key is required"):
            GoogleDenseEmbedder(model_name="gemini-embedding-2-preview")

    def test_rejects_unsupported_model(self):
        with pytest.raises(ValueError, match="Unsupported model"):
            GoogleDenseEmbedder(model_name="unknown-model", api_key="key")

    def test_rejects_dimension_exceeding_max(self):
        with pytest.raises(ValueError, match="exceeds maximum"):
            _embedder(dimension=9999)

    def test_defaults(self):
        e = _embedder()
        assert e.get_dimension() == 3072
        assert e.max_tokens == 8192
        assert e.api_base == "https://generativelanguage.googleapis.com/v1beta"

    def test_custom_values(self):
        e = _embedder(dimension=1024, max_tokens=4096, api_base="https://custom/v1")
        assert e.get_dimension() == 1024
        assert e.max_tokens == 4096
        assert e.api_base == "https://custom/v1"


class TestGoogleDenseEmbedderEmbed:
    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_embed_request_structure(self, mock_post):
        """Single embed call — verify URL, auth header, body, and return value."""
        mock_post.return_value = _make_response([0.1] * 3072)

        result = _embedder().embed("Hello world")

        assert result.dense_vector is not None
        assert len(result.dense_vector) == 3072
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert "gemini-embedding-2-preview:embedContent" in url
        headers = mock_post.call_args[1]["headers"]
        assert headers["x-goog-api-key"] == "test-key"
        body = mock_post.call_args[1]["json"]
        assert body["content"]["parts"][0]["text"] == "Hello world"
        assert "taskType" not in body
        assert "task_type" not in body

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_dimension_sent_as_output_dimensionality(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 1024)
        _embedder(dimension=1024).embed("Hello world")
        body = mock_post.call_args[1]["json"]
        assert body["output_dimensionality"] == 1024

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_no_dimension_omits_output_dimensionality(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)
        _embedder().embed("Hello world")
        assert "output_dimensionality" not in mock_post.call_args[1]["json"]

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_extra_headers_forwarded(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)
        _embedder(extra_headers={"X-Custom": "value"}).embed("Hello world")
        assert mock_post.call_args[1]["headers"]["X-Custom"] == "value"

    @pytest.mark.parametrize("text", ["", "   "])
    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_blank_text_returns_empty_without_request(self, mock_post, text):
        result = _embedder().embed(text)
        assert result.dense_vector is None
        mock_post.assert_not_called()

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_api_error_raises_runtime_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError()
        mock_post.return_value = mock_resp
        with pytest.raises(RuntimeError):
            _embedder().embed("Hello world")

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_unexpected_response_raises(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"unexpected": "format"}
        mock_post.return_value = mock_resp
        with pytest.raises(RuntimeError, match="Unexpected response format"):
            _embedder().embed("Hello world")


class TestGoogleDenseEmbedderBatch:
    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_batch_results_and_empty_skipped(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)
        results = _embedder().embed_batch(["Hello", "", "World"])
        assert len(results) == 3
        assert results[0].dense_vector is not None
        assert results[1].dense_vector is None
        assert results[2].dense_vector is not None
        assert mock_post.call_count == 2

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_batch_empty_list(self, mock_post):
        assert _embedder().embed_batch([]) == []
        mock_post.assert_not_called()


class TestGoogleDenseEmbedderChunking:
    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_oversized_text_chunked_and_averaged(self, mock_post):
        mock_post.return_value = _make_response([0.5] * 3072)
        result = _embedder(max_tokens=5).embed("word " * 100)
        assert result.dense_vector is not None
        assert mock_post.call_count > 1

    @patch("openviking.models.embedder.google_embedders.requests.post")
    def test_normal_text_single_request(self, mock_post):
        mock_post.return_value = _make_response([0.1] * 3072)
        _embedder().embed("Hello world")
        assert mock_post.call_count == 1
