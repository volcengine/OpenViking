# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for #4302: `embedding.provider: "ollama"` 404s on a bare api_base.

Ollama routes its OpenAI-compatible surface under ``/v1`` and answers anything
else with ``404 page not found``. The OpenAI client appends ``/embeddings`` to
whatever base URL it is handed, so the bare ``http://127.0.0.1:11434`` shown in
docs/*/guides/01-configuration.md produced a request to ``/embeddings`` — and a
`doctor` probe failure — against a perfectly healthy server.
"""

from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def _ollama_embedder(api_base):
    dense = EmbeddingModelConfig(
        provider="ollama",
        model="nomic-embed-text",
        dimension=768,
        **({"api_base": api_base} if api_base is not None else {}),
    )
    return EmbeddingConfig(dense=dense).get_embedder()


class TestOllamaEmbedderApiBase:
    def test_bare_host_is_routed_to_the_openai_surface(self):
        """The documented config form must reach /v1/embeddings, not /embeddings."""
        embedder = _ollama_embedder("http://127.0.0.1:11434")
        assert embedder.api_base == "http://127.0.0.1:11434/v1"
        # The client joins its base URL with "embeddings"; a base without the
        # /v1 segment is what produced `404 page not found`.
        assert str(embedder.client.base_url).rstrip("/").endswith("/v1")

    def test_wizard_form_is_unchanged(self):
        """setup_wizard writes .../v1 — it must not become .../v1/v1."""
        embedder = _ollama_embedder("http://localhost:11434/v1")
        assert embedder.api_base == "http://localhost:11434/v1"

    def test_reverse_proxy_prefix_is_preserved(self):
        embedder = _ollama_embedder("https://gpu.internal/ollama/v1")
        assert embedder.api_base == "https://gpu.internal/ollama/v1"

    def test_omitted_api_base_keeps_the_local_default(self):
        embedder = _ollama_embedder(None)
        assert embedder.api_base == "http://localhost:11434/v1"

    def test_api_key_is_still_optional(self):
        """Ollama ignores the key but the OpenAI client requires a non-empty one."""
        embedder = _ollama_embedder("http://127.0.0.1:11434")
        assert embedder.api_key == "no-key"
