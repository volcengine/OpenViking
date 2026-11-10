# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for extra_body support in OpenAIDenseEmbedder and EmbeddingConfig factory.

Covers:
  1. extra_body is merged into every embeddings.create call
  2. explicit query_param/document_param keys override extra_body on conflict
  3. omitting extra_body does not inject an extra_body kwarg
  4. factory (_create_embedder) transparently forwards extra_body
"""

from unittest.mock import MagicMock, patch

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


class TestExtraBodyDirectConstruction:
    """Test extra_body behaviour when constructing OpenAIDenseEmbedder directly."""

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_extra_body_merged_into_request_kwargs(self, mock_openai_class):
        """extra_body dict must arrive as the extra_body kwarg in embeddings.create()."""
        mock_client = _make_mock_client()
        mock_openai_class.return_value = mock_client

        embedder = OpenAIDenseEmbedder(
            model_name="qwen/qwen3-embedding-8b",
            api_key="sk-test",
            api_base="https://openrouter.ai/api/v1",
            dimension=8,
            extra_body={"provider": {"sort": "latency"}},
        )
        embedder.embed("hello")

        mock_client.embeddings.create.assert_called_once()
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["extra_body"] == {"provider": {"sort": "latency"}}

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_no_extra_body_omits_kwarg(self, mock_openai_class):
        """When extra_body is not provided, extra_body must NOT appear in embeddings.create()."""
        mock_client = _make_mock_client()
        mock_openai_class.return_value = mock_client

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="sk-test",
            dimension=8,
        )
        embedder.embed("hello")

        mock_client.embeddings.create.assert_called_once()
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" not in call_kwargs

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_query_document_params_override_extra_body_on_conflict(self, mock_openai_class):
        """Explicit query_param/document_param keys win over extra_body keys."""
        mock_client = _make_mock_client()
        mock_openai_class.return_value = mock_client

        embedder = OpenAIDenseEmbedder(
            model_name="bge-m3",
            api_key="sk-test",
            api_base="https://your-api-endpoint.com/v1",
            dimension=8,
            query_param="query",
            document_param="passage",
            extra_body={"input_type": "default", "provider": {"sort": "latency"}},
        )

        embedder.embed("search text", is_query=True)
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["extra_body"]["input_type"] == "query"
        assert call_kwargs["extra_body"]["provider"] == {"sort": "latency"}

        embedder.embed("document text", is_query=False)
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["extra_body"]["input_type"] == "passage"
        assert call_kwargs["extra_body"]["provider"] == {"sort": "latency"}

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_extra_body_is_copied_not_mutated(self, mock_openai_class):
        """Merging query/document params must not mutate the configured dict."""
        mock_client = _make_mock_client()
        mock_openai_class.return_value = mock_client

        extra_body = {"input_type": "default"}
        embedder = OpenAIDenseEmbedder(
            model_name="bge-m3",
            api_key="sk-test",
            api_base="https://your-api-endpoint.com/v1",
            dimension=8,
            query_param="query",
            extra_body=extra_body,
        )
        embedder.embed("search text", is_query=True)

        assert extra_body == {"input_type": "default"}


class TestExtraBodyViaFactory:
    """Test extra_body forwarding through EmbeddingConfig._create_embedder."""

    @patch("openai.OpenAI")
    def test_factory_passes_extra_body(self, mock_openai_class):
        """Factory must forward config extra_body into every embeddings.create() call."""
        mock_client = _make_mock_client()
        mock_openai_class.return_value = mock_client

        cfg = EmbeddingModelConfig(
            provider="openai",
            model="qwen/qwen3-embedding-8b",
            api_key="sk-test",
            api_base="https://openrouter.ai/api/v1",
            dimension=8,
            extra_body={"provider": {"sort": "latency"}},
        )
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("openai", "dense", cfg)
        embedder.embed("hello")

        mock_client.embeddings.create.assert_called_once()
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["extra_body"] == {"provider": {"sort": "latency"}}

    @patch("openai.OpenAI")
    def test_factory_omits_extra_body_when_none(self, mock_openai_class):
        """Factory must NOT inject extra_body when it is None."""
        mock_client = _make_mock_client()
        mock_openai_class.return_value = mock_client

        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
            dimension=8,
        )
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("openai", "dense", cfg)
        embedder.embed("hello")

        mock_client.embeddings.create.assert_called_once()
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" not in call_kwargs


class TestEmbeddingModelConfigExtraBody:
    """Test that EmbeddingModelConfig accepts and stores the extra_body field."""

    def test_openai_config_accepts_extra_body_field(self):
        """EmbeddingModelConfig should store extra_body without validation error."""
        cfg = EmbeddingModelConfig(
            provider="openai",
            model="qwen/qwen3-embedding-8b",
            api_key="sk-test",
            extra_body={"provider": {"sort": "latency"}},
        )
        assert cfg.extra_body == {"provider": {"sort": "latency"}}

    def test_extra_body_defaults_to_none(self):
        """extra_body field should default to None when not supplied."""
        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
        )
        assert cfg.extra_body is None
