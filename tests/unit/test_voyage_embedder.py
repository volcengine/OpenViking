# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for Voyage AI embedder support."""

from unittest.mock import MagicMock, patch

import pytest

from openviking.models.embedder import VoyageDenseEmbedder
from openviking.models.embedder.voyage_embedders import VOYAGE_MODEL_DIMENSIONS


class TestVoyageDenseEmbedder:
    """Test cases for VoyageDenseEmbedder."""

    def test_init_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key is required"):
            VoyageDenseEmbedder(model_name="voyage-4-lite")

    def test_init_with_defaults(self):
        embedder = VoyageDenseEmbedder(
            model_name="voyage-4-lite",
            api_key="voyage-key",
        )
        assert embedder.api_key == "voyage-key"
        assert embedder.api_base == "https://api.voyageai.com/v1"
        assert embedder.get_dimension() == 1024

    def test_model_dimensions_constant(self):
        assert VOYAGE_MODEL_DIMENSIONS["voyage-4-lite"] == 1024
        assert VOYAGE_MODEL_DIMENSIONS["voyage-4"] == 1024
        assert VOYAGE_MODEL_DIMENSIONS["voyage-4-large"] == 1024
        assert VOYAGE_MODEL_DIMENSIONS["voyage-code-3"] == 1024

    def test_custom_dimension(self):
        embedder = VoyageDenseEmbedder(
            model_name="voyage-4-lite",
            api_key="voyage-key",
            dimension=256,
        )
        assert embedder.get_dimension() == 256

    def test_invalid_dimension_for_supported_model(self):
        with pytest.raises(ValueError, match="Supported dimensions"):
            VoyageDenseEmbedder(
                model_name="voyage-4-lite",
                api_key="voyage-key",
                dimension=1536,
            )

    @patch("openviking.models.embedder.voyage_embedders.openai.OpenAI")
    def test_embed_single_text(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1024

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = VoyageDenseEmbedder(
            model_name="voyage-4-lite",
            api_key="voyage-key",
        )
        result = embedder.embed("Hello world")

        assert result.dense_vector is not None
        assert len(result.dense_vector) == 1024
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["model"] == "voyage-4-lite"
        assert "dimensions" not in call_kwargs
        assert "extra_body" not in call_kwargs

    @patch("openviking.models.embedder.voyage_embedders.openai.OpenAI")
    def test_embed_uses_voyage_output_dimension(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 512

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = VoyageDenseEmbedder(
            model_name="voyage-4-lite",
            api_key="voyage-key",
            dimension=512,
        )
        embedder.embed("Hello world")

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"]["output_dimension"] == 512
        assert "dimensions" not in call_kwargs

    @patch("openviking.models.embedder.voyage_embedders.openai.OpenAI")
    def test_embed_batch(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(embedding=[0.1] * 1024),
            MagicMock(embedding=[0.2] * 1024),
        ]
        mock_client.embeddings.create.return_value = mock_response

        embedder = VoyageDenseEmbedder(
            model_name="voyage-4-lite",
            api_key="voyage-key",
        )
        results = embedder.embed_batch(["Hello", "World"])

        assert len(results) == 2
        assert len(results[0].dense_vector) == 1024
        assert len(results[1].dense_vector) == 1024

    @patch("openviking.models.embedder.voyage_embedders.openai.OpenAI")
    def test_embed_batch_empty(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        embedder = VoyageDenseEmbedder(
            model_name="voyage-4-lite",
            api_key="voyage-key",
        )
        assert embedder.embed_batch([]) == []
        mock_client.embeddings.create.assert_not_called()

    @patch("openviking.models.embedder.voyage_embedders.openai.OpenAI")
    def test_embed_api_error(self, mock_openai_class):
        import openai

        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.embeddings.create.side_effect = openai.APIError(
            message="Voyage error",
            request=MagicMock(),
            body=None,
        )

        embedder = VoyageDenseEmbedder(
            model_name="voyage-4-lite",
            api_key="voyage-key",
        )

        with pytest.raises(RuntimeError, match="Voyage API error"):
            embedder.embed("Hello world")
