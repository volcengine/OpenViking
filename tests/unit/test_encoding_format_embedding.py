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

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest
from pydantic import ValidationError

from openviking.models.embedder import OpenAIDenseEmbedder, RetryEmbedder
from openviking.models.model_error import ModelError, ModelErrorKind
from openviking.server.error_mapping import map_exception
from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def _mock_openai_client():
    """Return a mock openai.OpenAI client with a minimal valid embedding response."""
    client = MagicMock()
    client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 8)],
        usage=None,
    )
    return client


def _bad_request(message: str, *, param: str | None, status_code: int = 400):
    request = httpx.Request("POST", "https://gateway.example.com/v1/embeddings")
    response = httpx.Response(status_code, request=request)
    error_body = {
        "message": message,
        "type": "invalid_request_error",
        "code": "unsupported_parameter",
    }
    if param is not None:
        error_body["param"] = param
    error_class = openai.UnprocessableEntityError if status_code == 422 else openai.BadRequestError
    return error_class(message, response=response, body=error_body)


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

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_explicit_base64_refusal_names_config_fix_without_retry(self, mock_openai_class):
        client = MagicMock()
        provider_error = _bad_request(
            "This route does not support encoding_format=base64",
            param="encoding_format",
        )
        client.embeddings.create.side_effect = provider_error
        mock_openai_class.return_value = client

        inner = OpenAIDenseEmbedder(
            model_name="custom-embedding-model",
            api_key="sk-test",
            api_base="https://gateway.example.com/v1",
            dimension=8,
            encoding_format="base64",
            config={"max_retries": 3},
        )
        embedder = RetryEmbedder(inner)

        with pytest.raises(ModelError) as raised:
            embedder.embed("hello")

        assert raised.value.kind is ModelErrorKind.UNSUPPORTED
        assert raised.value.__cause__ is provider_error
        assert 'embedding.dense.encoding_format to "float"' in str(raised.value)
        assert "remove the explicit base64 setting" in str(raised.value)
        assert client.embeddings.create.call_count == 1

        mapped = map_exception(raised.value)
        assert mapped is not None
        assert mapped.code == "INVALID_ARGUMENT"
        assert 'embedding.dense.encoding_format to "float"' in str(mapped)
        assert 'embedding.dense.encoding_format to "float"' in mapped.details["remediation"]

    @pytest.mark.parametrize(
        ("encoding_format", "message", "param"),
        [
            pytest.param("base64", "Unsupported dimensions", "dimensions", id="other-field"),
            pytest.param(None, "Unsupported encoding_format", "encoding_format", id="unset-config"),
        ],
    )
    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_encoding_fix_requires_explicit_base64_refusal(
        self, mock_openai_class, encoding_format, message, param
    ):
        client = MagicMock()
        client.embeddings.create.side_effect = _bad_request(message, param=param)
        mock_openai_class.return_value = client

        embedder = OpenAIDenseEmbedder(
            model_name="custom-embedding-model",
            api_key="sk-test",
            api_base="https://gateway.example.com/v1",
            dimension=8,
            encoding_format=encoding_format,
        )

        with pytest.raises(ModelError) as raised:
            embedder.embed("hello")

        assert "embedding.dense.encoding_format" not in str(raised.value)

    @pytest.mark.asyncio
    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    async def test_async_explicit_base64_refusal_names_config_fix(
        self, mock_openai_class, monkeypatch
    ):
        mock_openai_class.return_value = _mock_openai_client()
        provider_error = _bad_request(
            "Unsupported parameter: encoding format",
            param=None,
            status_code=422,
        )
        async_client = MagicMock()
        async_client.embeddings.create = AsyncMock(side_effect=provider_error)

        embedder = OpenAIDenseEmbedder(
            model_name="custom-embedding-model",
            api_key="sk-test",
            api_base="https://gateway.example.com/v1",
            dimension=8,
            encoding_format="base64",
        )
        monkeypatch.setattr(embedder, "_get_async_client", lambda: async_client)

        with pytest.raises(ModelError) as raised:
            await embedder.embed_async("hello")

        assert raised.value.kind is ModelErrorKind.UNSUPPORTED
        assert 'embedding.dense.encoding_format to "float"' in str(raised.value)
        assert async_client.embeddings.create.await_count == 1


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
        assert isinstance(embedder, RetryEmbedder)
        assert isinstance(embedder.inner, OpenAIDenseEmbedder)
        assert embedder.inner.encoding_format == "float"

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
        assert isinstance(embedder, RetryEmbedder)
        assert isinstance(embedder.inner, OpenAIDenseEmbedder)
        assert embedder.inner.encoding_format == "float"

    @patch("openai.OpenAI")
    def test_factory_omits_when_unset(self, mock_openai_class):
        mock_openai_class.return_value = _mock_openai_client()

        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
        )
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("openai", "dense", cfg)
        assert isinstance(embedder, RetryEmbedder)
        assert isinstance(embedder.inner, OpenAIDenseEmbedder)
        assert embedder.inner.encoding_format is None
