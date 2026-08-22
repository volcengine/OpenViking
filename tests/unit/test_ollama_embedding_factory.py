# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the ollama embedding factory in EmbeddingConfig._create_embedder.

Regression tests for two bugs fixed in the ollama factory lambda:
  1. max_tokens was not forwarded to OpenAIDenseEmbedder (so user-configured
     chunking thresholds were silently ignored for Ollama).
  2. The api_key placeholder was "ollama" instead of "no-key", inconsistent
     with the openai factory and the placeholder used inside OpenAIDenseEmbedder.
"""

from unittest.mock import MagicMock, patch

from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def _make_mock_openai_class():
    """Return a mock openai.OpenAI class that records constructor kwargs."""
    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 8)],
        usage=None,
    )
    mock_openai_class = MagicMock(return_value=mock_client)
    return mock_openai_class, mock_client


def _make_ollama_cfg(**kwargs) -> EmbeddingModelConfig:
    defaults = dict(provider="ollama", model="nomic-embed-text", dimension=768)
    defaults.update(kwargs)
    return EmbeddingModelConfig(**defaults)


@patch("openai.OpenAI")
class TestOllamaFactoryMaxTokens:
    """max_tokens must be forwarded from config to OpenAIDenseEmbedder."""

    def test_custom_max_tokens_is_forwarded(self, mock_openai_class):
        """When max_tokens=512, the created embedder should report max_tokens=512."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 8)], usage=None
        )
        mock_openai_class.return_value = mock_client

        cfg = _make_ollama_cfg(max_tokens=512)
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("ollama", "dense", cfg)

        assert embedder.max_tokens == 512

    def test_none_max_tokens_uses_default(self, mock_openai_class):
        """When max_tokens is not set (None), the embedder should use its default (8000)."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 8)], usage=None
        )
        mock_openai_class.return_value = mock_client

        cfg = _make_ollama_cfg()  # max_tokens not set -> None
        assert cfg.max_tokens is None

        embedder = EmbeddingConfig(dense=cfg)._create_embedder("ollama", "dense", cfg)

        assert embedder.max_tokens == 8000  # class-level default

    def test_openai_factory_max_tokens_also_forwarded(self, mock_openai_class):
        """Sanity: the openai factory also forwards max_tokens (parity check)."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 8)], usage=None
        )
        mock_openai_class.return_value = mock_client

        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
            dimension=1536,
            max_tokens=4096,
        )
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("openai", "dense", cfg)

        assert embedder.max_tokens == 4096


@patch("openai.OpenAI")
class TestOllamaFactoryApiKeyPlaceholder:
    """The api_key placeholder for ollama must be "no-key", not "ollama"."""

    def test_no_api_key_uses_no_key_placeholder(self, mock_openai_class):
        """When no api_key is provided, openai.OpenAI must be called with api_key='no-key'."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 8)], usage=None
        )
        mock_openai_class.return_value = mock_client

        cfg = _make_ollama_cfg()  # no api_key
        EmbeddingConfig(dense=cfg)._create_embedder("ollama", "dense", cfg)

        mock_openai_class.assert_called_once()
        call_kwargs = mock_openai_class.call_args[1]
        assert call_kwargs["api_key"] == "no-key", (
            f"Expected placeholder 'no-key' but got {call_kwargs['api_key']!r}. "
            "The ollama factory must use the same placeholder as the openai factory."
        )

    def test_explicit_api_key_is_passed_through(self, mock_openai_class):
        """When an api_key is explicitly provided, it must be passed through unchanged."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 8)], usage=None
        )
        mock_openai_class.return_value = mock_client

        cfg = _make_ollama_cfg(api_key="my-custom-key")
        EmbeddingConfig(dense=cfg)._create_embedder("ollama", "dense", cfg)

        mock_openai_class.assert_called_once()
        call_kwargs = mock_openai_class.call_args[1]
        assert call_kwargs["api_key"] == "my-custom-key"

    def test_openai_factory_also_uses_no_key_placeholder(self, mock_openai_class):
        """Parity check: the openai factory also uses 'no-key' when api_base is set."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 8)], usage=None
        )
        mock_openai_class.return_value = mock_client

        cfg = EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_base="http://localhost:8080/v1",
            dimension=1536,
        )
        EmbeddingConfig(dense=cfg)._create_embedder("openai", "dense", cfg)

        call_kwargs = mock_openai_class.call_args[1]
        assert call_kwargs["api_key"] == "no-key"


@patch("openai.OpenAI")
class TestOllamaFactoryApiBase:
    """The ollama factory must supply the correct api_base."""

    def test_default_api_base_is_localhost_ollama(self, mock_openai_class):
        """When api_base is not set, it should default to http://localhost:11434/v1."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 8)], usage=None
        )
        mock_openai_class.return_value = mock_client

        cfg = _make_ollama_cfg()  # no api_base
        EmbeddingConfig(dense=cfg)._create_embedder("ollama", "dense", cfg)

        call_kwargs = mock_openai_class.call_args[1]
        assert call_kwargs["base_url"] == "http://localhost:11434/v1"

    def test_custom_api_base_is_forwarded(self, mock_openai_class):
        """When api_base is explicitly set, it must override the default."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 8)], usage=None
        )
        mock_openai_class.return_value = mock_client

        cfg = _make_ollama_cfg(api_base="http://gpu-server:11434/v1")
        EmbeddingConfig(dense=cfg)._create_embedder("ollama", "dense", cfg)

        call_kwargs = mock_openai_class.call_args[1]
        assert call_kwargs["base_url"] == "http://gpu-server:11434/v1"


@patch("openai.OpenAI")
class TestOllamaFactoryProviderAttribution:
    """Provider attribution for the ollama factory.

    Regression for PR #2317: the ollama factory branch omitted the ``provider``
    kwarg, so ``OpenAIDenseEmbedder`` defaulted ``self._provider`` to ``"openai"``
    while ``self.provider`` was correctly ``"ollama"``. This mislabeled embedding
    token-usage attribution (metrics recorded under ``provider="openai"``).
    """

    def test_provider_attribution_is_ollama(self, mock_openai_class):
        """A (regression catcher, fails before fix): _provider and provider must be 'ollama'."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 8)], usage=None
        )
        mock_openai_class.return_value = mock_client

        cfg = _make_ollama_cfg()
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("ollama", "dense", cfg)

        # Note: this asserts OpenAIDenseEmbedder._provider (factory-layer kwarg),
        # distinct from EmbeddingModelConfig.provider (model-layer field).
        assert embedder._provider == "ollama"  # was "openai" before fix
        assert embedder.provider == "ollama"

    def test_dimensions_sent_in_request(self, mock_openai_class):
        """B (behavior contract, always true, NOT a regression catcher):
        ollama sends the ``dimensions`` param when dimension is configured."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 768)], usage=None
        )
        mock_openai_class.return_value = mock_client

        cfg = _make_ollama_cfg(dimension=768)
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("ollama", "dense", cfg)
        embedder.embed("hello")

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["dimensions"] == 768

    def test_token_usage_attribution_is_ollama(self, mock_openai_class):
        """C (regression catcher, fails before fix): update_token_usage receives provider='ollama'.

        The mock response MUST carry ``usage`` (dict branch). Do NOT reuse
        ``_make_mock_openai_class`` -- its ``usage=None`` makes
        ``_update_telemetry_token_usage`` return early at
        ``openai_embedders.py`` ``if not usage: return``, so ``update_token_usage``
        is never invoked and the spy assertion would spuriously fail. A bare
        MagicMock usage would hit ``int(MagicMock)`` -> TypeError, hence the dict.
        """
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 8)],
            usage={"prompt_tokens": 7, "total_tokens": 9},
        )
        mock_openai_class.return_value = mock_client

        cfg = _make_ollama_cfg(dimension=768)
        embedder = EmbeddingConfig(dense=cfg)._create_embedder("ollama", "dense", cfg)

        with patch.object(embedder, "update_token_usage") as spy:
            embedder.embed("hello")

        spy.assert_called_once()
        assert spy.call_args.kwargs["provider"] == "ollama"  # was "openai" before fix
