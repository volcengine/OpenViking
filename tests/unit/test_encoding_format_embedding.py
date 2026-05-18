# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the configurable encoding_format option on OpenAI-compatible embedders.

Covers:
  1. EmbeddingModelConfig accepts encoding_format ('float' / 'base64' / unset).
  2. The OpenAI / Azure factory branches forward encoding_format to
     OpenAIDenseEmbedder.
  3. OpenAIDenseEmbedder forwards the value to ``client.embeddings.create`` and
     omits the kwarg entirely when unset, preserving the SDK's own default.
"""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from openviking.models.embedder import OpenAIDenseEmbedder
from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def _mock_openai_client():
    """Return a mock openai.OpenAI client with a minimal valid embedding response."""
    client = MagicMock()
    client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 8)],
        usage=None,
    )
    return client


class TestEncodingFormatConfig:
    def test_default_is_unset(self):
        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
        )
        assert cfg.encoding_format is None

    def test_accepts_float(self):
        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
            encoding_format="float",
        )
        assert cfg.encoding_format == "float"

    def test_accepts_base64(self):
        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
            encoding_format="base64",
        )
        assert cfg.encoding_format == "base64"

    def test_rejects_unknown_value(self):
        with pytest.raises(ValidationError):
            EmbeddingModelConfig(
                provider="openai",
                model="text-embedding-3-small",
                api_key="sk-test",
                encoding_format="hex",  # type: ignore[arg-type]
            )


class TestEncodingFormatRuntime:
    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_kwarg_forwarded_when_set(self, mock_openai_class):
        mock_openai_class.return_value = _mock_openai_client()

        embedder = OpenAIDenseEmbedder(
            model_name="bge-m3",
            api_key="sk-test",
            api_base="https://gateway.example.com/v1",
            dimension=1024,
            encoding_format="float",
        )
        embedder.embed("hello")

        call_kwargs = mock_openai_class.return_value.embeddings.create.call_args[1]
        assert call_kwargs["encoding_format"] == "float"

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_kwarg_omitted_when_unset(self, mock_openai_class):
        mock_openai_class.return_value = _mock_openai_client()

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="sk-test",
        )
        embedder.embed("hello")

        call_kwargs = mock_openai_class.return_value.embeddings.create.call_args[1]
        assert "encoding_format" not in call_kwargs


class TestEncodingFormatFactory:
    @patch("openai.OpenAI")
    def test_openai_factory_forwards(self, mock_openai_class):
        mock_openai_class.return_value = _mock_openai_client()

        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
            encoding_format="float",
        )
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("openai", "dense", cfg)
        assert isinstance(embedder, OpenAIDenseEmbedder)
        assert embedder.encoding_format == "float"

    @patch("openai.AzureOpenAI")
    def test_azure_factory_forwards(self, mock_azure_class):
        mock_azure_class.return_value = _mock_openai_client()

        cfg = EmbeddingModelConfig(
            provider="azure",
            model="text-embedding-3-small",
            api_key="sk-test",
            api_base="https://example.openai.azure.com",
            api_version="2024-12-01-preview",
            encoding_format="float",
        )
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("azure", "dense", cfg)
        assert isinstance(embedder, OpenAIDenseEmbedder)
        assert embedder.encoding_format == "float"

    @patch("openai.OpenAI")
    def test_factory_omits_when_unset(self, mock_openai_class):
        mock_openai_class.return_value = _mock_openai_client()

        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
        )
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("openai", "dense", cfg)
        assert isinstance(embedder, OpenAIDenseEmbedder)
        assert embedder.encoding_format is None
