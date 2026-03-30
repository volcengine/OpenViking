# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Cohere embedder support."""

from unittest.mock import MagicMock, patch

import pytest

from openviking.models.embedder import CohereDenseEmbedder
from openviking.models.embedder.cohere_embedders import (
    COHERE_ALLOWED_DIMENSIONS,
    COHERE_MODEL_DIMENSIONS,
    get_cohere_model_default_dimension,
)


class TestCohereDenseEmbedder:
    """Test cases for CohereDenseEmbedder."""

    def test_init_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key is required"):
            CohereDenseEmbedder(model_name="embed-v4.0")

    def test_init_with_defaults(self):
        embedder = CohereDenseEmbedder(
            model_name="embed-v4.0",
            api_key="cohere-key",
        )
        assert embedder.api_key == "cohere-key"
        assert embedder.api_base == "https://api.cohere.com"
        assert embedder.get_dimension() == 1536  # embed-v4.0 native

    def test_model_dimensions_constant(self):
        assert COHERE_MODEL_DIMENSIONS["embed-v4.0"] == 1536
        assert COHERE_MODEL_DIMENSIONS["embed-english-v3.0"] == 1024
        assert COHERE_MODEL_DIMENSIONS["embed-multilingual-v3.0"] == 1024
        assert COHERE_MODEL_DIMENSIONS["embed-english-light-v3.0"] == 384

    def test_allowed_dimensions_v4(self):
        assert COHERE_ALLOWED_DIMENSIONS["embed-v4.0"] == {256, 512, 1024, 1536}

    def test_default_dimension_helper(self):
        assert get_cohere_model_default_dimension("embed-v4.0") == 1536
        assert get_cohere_model_default_dimension("embed-english-v3.0") == 1024
        assert get_cohere_model_default_dimension(None) == 1024
        assert get_cohere_model_default_dimension("unknown-model") == 1024

    def test_custom_dimension_v4(self):
        embedder = CohereDenseEmbedder(
            model_name="embed-v4.0",
            api_key="cohere-key",
            dimension=1024,
        )
        assert embedder.get_dimension() == 1024

    def test_invalid_dimension_for_v4(self):
        with pytest.raises(ValueError, match="not supported"):
            CohereDenseEmbedder(
                model_name="embed-v4.0",
                api_key="cohere-key",
                dimension=768,
            )

    def test_v3_model_allows_truncation(self):
        """v3 models don't have server-side dim reduction, so any smaller dim is OK (client truncation)."""
        embedder = CohereDenseEmbedder(
            model_name="embed-english-v3.0",
            api_key="cohere-key",
            dimension=512,
        )
        assert embedder.get_dimension() == 512
        assert embedder._needs_truncation is True

    def test_server_dim_for_v4(self):
        """embed-v4.0 should use server-side output_dimension when dimension differs from native."""
        embedder = CohereDenseEmbedder(
            model_name="embed-v4.0",
            api_key="cohere-key",
            dimension=1024,
        )
        assert embedder._use_server_dim is True
        assert embedder._needs_truncation is False

    @patch("openviking.models.embedder.cohere_embedders.httpx.Client")
    def test_embed_single_text(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "embeddings": {"float": [[0.1] * 1024]}
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        embedder = CohereDenseEmbedder(
            model_name="embed-english-v3.0",
            api_key="cohere-key",
        )
        result = embedder.embed("Hello world")

        assert result.dense_vector is not None
        assert len(result.dense_vector) == 1024
        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["model"] == "embed-english-v3.0"
        assert payload["input_type"] == "search_document"
        assert payload["texts"] == ["Hello world"]

    @patch("openviking.models.embedder.cohere_embedders.httpx.Client")
    def test_embed_query_uses_search_query_type(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "embeddings": {"float": [[0.1] * 1024]}
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        embedder = CohereDenseEmbedder(
            model_name="embed-english-v3.0",
            api_key="cohere-key",
        )
        embedder.embed("search query", is_query=True)

        payload = mock_client.post.call_args[1]["json"]
        assert payload["input_type"] == "search_query"

    @patch("openviking.models.embedder.cohere_embedders.httpx.Client")
    def test_embed_v4_sends_output_dimension(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "embeddings": {"float": [[0.1] * 1024]}
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        embedder = CohereDenseEmbedder(
            model_name="embed-v4.0",
            api_key="cohere-key",
            dimension=1024,
        )
        embedder.embed("Hello world")

        payload = mock_client.post.call_args[1]["json"]
        assert payload["output_dimension"] == 1024

    @patch("openviking.models.embedder.cohere_embedders.httpx.Client")
    def test_embed_batch(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "embeddings": {"float": [[0.1] * 1024, [0.2] * 1024]}
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        embedder = CohereDenseEmbedder(
            model_name="embed-english-v3.0",
            api_key="cohere-key",
        )
        results = embedder.embed_batch(["Hello", "World"])

        assert len(results) == 2
        assert len(results[0].dense_vector) == 1024

    @patch("openviking.models.embedder.cohere_embedders.httpx.Client")
    def test_embed_batch_empty(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        embedder = CohereDenseEmbedder(
            model_name="embed-english-v3.0",
            api_key="cohere-key",
        )
        assert embedder.embed_batch([]) == []
        mock_client.post.assert_not_called()

    @patch("openviking.models.embedder.cohere_embedders.httpx.Client")
    def test_embed_api_error(self, mock_client_class):
        import httpx

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_request = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_client.post.return_value = mock_response
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=mock_request,
            response=mock_response,
        )

        embedder = CohereDenseEmbedder(
            model_name="embed-english-v3.0",
            api_key="bad-key",
        )

        with pytest.raises(RuntimeError, match="Cohere API error"):
            embedder.embed("Hello world")

    @patch("openviking.models.embedder.cohere_embedders.httpx.Client")
    def test_close(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        embedder = CohereDenseEmbedder(
            model_name="embed-english-v3.0",
            api_key="cohere-key",
        )
        embedder.close()
        mock_client.close.assert_called_once()
