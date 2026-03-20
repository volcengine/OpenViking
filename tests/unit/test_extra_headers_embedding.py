# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for extra_headers support in OpenAIDenseEmbedder and EmbeddingConfig factory.

Covers:
  1. extra_headers is passed as default_headers to openai.OpenAI client
  2. omitting extra_headers does not inject default_headers key
  3. factory (_create_embedder) transparently forwards extra_headers
  4. api_key dead-code bug fix: no raise when api_base is set without api_key
"""

from unittest.mock import MagicMock, patch

import pytest

from openviking.models.embedder import OpenAIDenseEmbedder
from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def _make_mock_client():
    """Build a MagicMock openai client that returns a minimal valid embedding response."""
    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 8)],
        usage=None,
    )
    return mock_client


class TestExtraHeadersDirectConstruction:
    """Test extra_headers behaviour when constructing OpenAIDenseEmbedder directly."""

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_extra_headers_passed_as_default_headers(self, mock_openai_class):
        """extra_headers dict must arrive as default_headers kwarg in openai.OpenAI()."""
        mock_openai_class.return_value = _make_mock_client()

        headers = {"HTTP-Referer": "https://example.com", "X-Title": "My App"}
        OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="sk-test",
            extra_headers=headers,
        )

        mock_openai_class.assert_called_once()
        call_kwargs = mock_openai_class.call_args[1]
        assert call_kwargs.get("default_headers") == headers

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_no_extra_headers_omits_default_headers(self, mock_openai_class):
        """When extra_headers is not provided, default_headers must NOT appear in openai.OpenAI()."""
        mock_openai_class.return_value = _make_mock_client()

        OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="sk-test",
        )

        mock_openai_class.assert_called_once()
        call_kwargs = mock_openai_class.call_args[1]
        assert "default_headers" not in call_kwargs


class TestExtraHeadersViaFactory:
    """Test extra_headers forwarding through EmbeddingConfig._create_embedder."""

    @patch("openai.OpenAI")
    def test_factory_passes_extra_headers(self, mock_openai_class):
        """Factory must forward extra_headers as default_headers to openai.OpenAI()."""
        mock_openai_class.return_value = _make_mock_client()

        headers = {"HTTP-Referer": "https://myapp.com", "X-Title": "MyApp"}
        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
            extra_headers=headers,
        )
        EmbeddingConfig(dense=cfg)._create_embedder("openai", "dense", cfg)

        mock_openai_class.assert_called_once()
        call_kwargs = mock_openai_class.call_args[1]
        assert call_kwargs.get("default_headers") == headers

    @patch("openai.OpenAI")
    def test_factory_omits_extra_headers_when_none(self, mock_openai_class):
        """Factory must NOT inject default_headers when extra_headers is None."""
        mock_openai_class.return_value = _make_mock_client()

        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
        )
        EmbeddingConfig(dense=cfg)._create_embedder("openai", "dense", cfg)

        mock_openai_class.assert_called_once()
        call_kwargs = mock_openai_class.call_args[1]
        assert "default_headers" not in call_kwargs


class TestEmbeddingModelConfigExtraHeaders:
    """Test that EmbeddingModelConfig accepts and stores the extra_headers field."""

    def test_openai_config_accepts_extra_headers_field(self):
        """EmbeddingModelConfig should store extra_headers without validation error."""
        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
            extra_headers={"X-Custom": "value"},
        )
        assert cfg.extra_headers == {"X-Custom": "value"}

    def test_extra_headers_defaults_to_none(self):
        """extra_headers field should default to None when not supplied."""
        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
        )
        assert cfg.extra_headers is None


class TestApiKeyValidationFix:
    """Test the api_key dead-code bug fix: validate only when both api_key and api_base are absent."""

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_api_key_not_required_when_api_base_set(self, mock_openai_class):
        """No ValueError should be raised when api_base is provided without api_key."""
        mock_openai_class.return_value = _make_mock_client()

        # Should NOT raise; api_base substitutes for api_key for local/compatible servers
        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_base="http://localhost:8080/v1",
        )
        assert embedder is not None

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_api_key_required_when_no_api_base(self, mock_openai_class):
        """ValueError must be raised when neither api_key nor api_base is provided."""
        mock_openai_class.return_value = _make_mock_client()

        with pytest.raises(ValueError, match="api_key is required"):
            OpenAIDenseEmbedder(model_name="text-embedding-3-small")
