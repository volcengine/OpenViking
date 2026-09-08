"""#4200 — a missing optional dependency surfaced as "'NoneType' object is not callable".

`openviking.models.embedder` exports `GeminiDenseEmbedder = None` when
`google-genai` is not installed. `_create_embedder()` looked the class up in
its factory registry and called it, so on a machine without the package the
doctor printed

    Embedding: FAIL  gemini/gemini-embedding-2-preview dimension=3072
              (invalid embedding config: 'NoneType' object is not callable)
              Fix: Fix embedding.dense provider/model/api_base/dimension in ov.conf

— an error that names neither the provider nor the missing package, and a
remediation line pointing at a config that is in fact correct.

`litellm` already had the guard this adds for `gemini`.
"""

import pytest

from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig

GEMINI_DENSE = EmbeddingModelConfig(
    provider="gemini",
    model="gemini-embedding-2-preview",
    api_key="not-a-real-key",
    dimension=3072,
)


def _uninstall(monkeypatch, name: str) -> None:
    """Make one optional embedder look absent, the way its ImportError does."""
    import openviking.models.embedder as embedder_pkg

    monkeypatch.setattr(embedder_pkg, name, None)


def test_missing_google_genai_names_the_package(monkeypatch):
    _uninstall(monkeypatch, "GeminiDenseEmbedder")

    with pytest.raises(ValueError) as excinfo:
        EmbeddingConfig(dense=GEMINI_DENSE).get_embedder()

    message = str(excinfo.value)
    assert "google-genai" in message
    assert "pip install openviking[gemini]" in message
    assert "NoneType" not in message


def test_missing_litellm_message_is_unchanged(monkeypatch):
    _uninstall(monkeypatch, "LiteLLMDenseEmbedder")

    with pytest.raises(ValueError) as excinfo:
        EmbeddingConfig(
            dense=EmbeddingModelConfig(
                provider="litellm", model="whatever", api_key="k", dimension=768
            )
        ).get_embedder()

    assert str(excinfo.value) == "LiteLLM is not installed. Install it with: pip install litellm"


def test_installed_provider_is_not_diverted(monkeypatch):
    """The guard must fire on absence only, never on a provider that is present."""
    import openviking.models.embedder as embedder_pkg

    if embedder_pkg.GeminiDenseEmbedder is None:
        pytest.skip("google-genai is not installed in this environment")

    embedder = EmbeddingConfig(dense=GEMINI_DENSE).get_embedder()

    assert type(embedder).__name__ == "GeminiDenseEmbedder"


def test_a_provider_without_an_optional_dependency_is_untouched():
    embedder = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="not-a-real-key",
            dimension=1536,
        )
    ).get_embedder()

    assert type(embedder).__name__ == "OpenAIDenseEmbedder"
