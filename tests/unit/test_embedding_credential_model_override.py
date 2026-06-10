# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for per-credential model override in EmbeddingModelConfig."""

from unittest.mock import patch

from openviking_cli.utils.config.embedding_config import (
    EmbeddingConfig,
    EmbeddingCredential,
    EmbeddingModelConfig,
)


def test_per_credential_model_overrides_parent_model():
    """When a credential specifies its own `model`, the merged config used to
    build that credential's embedder must use the credential's model, not the
    parent EmbeddingModelConfig.model."""
    parent = EmbeddingModelConfig(
        model="parent-model",
        dimension=1024,
        provider="volcengine",
        api_key="parent-key",
        credentials=[
            EmbeddingCredential(
                id="cred-a",
                provider="volcengine",
                model="endpoint-a",
                api_key="key-a",
                api_base="https://example.com/a",
            ),
            EmbeddingCredential(
                id="cred-b",
                provider="volcengine",
                api_key="key-b",
                api_base="https://example.com/b",
            ),
        ],
    )
    cfg = EmbeddingConfig(dense=parent)

    captured_models: list[str | None] = []

    def fake_create_embedder(_self, _provider, _embedder_type, config):
        captured_models.append(config.model)
        return object()

    # Skip wrapping into a FailoverEmbedder; we only care about the merged
    # configs passed into _create_embedder for each credential.
    with (
        patch.object(EmbeddingConfig, "_create_embedder", fake_create_embedder),
        patch(
            "openviking.models.embedder.FailoverEmbedder",
            lambda **kwargs: kwargs,
        ),
    ):
        cfg._create_failover_embedder("dense", parent)

    assert captured_models == ["endpoint-a", "parent-model"]


def test_credential_inherits_api_key_api_base_api_version_from_parent():
    """Regression: when a credential omits api_key/api_base/api_version, the
    merged config used to build that credential's embedder must fall back to
    the parent EmbeddingModelConfig fields. Validation already accepts this
    inheritance (cred.api_key or self.api_key); _create_failover_embedder
    must use the same fallback so EmbeddingModelConfig re-validation does not
    fail with 'OpenAI provider requires api_key to be set'.
    """
    parent = EmbeddingModelConfig(
        model="text-embedding-3-small",
        dimension=1536,
        provider="openai",
        api_key="parent-key",
        api_base="https://parent.example.com",
        api_version="2024-01-01",
        credentials=[
            # Credential 'a' omits api_key/api_base/api_version entirely;
            # they must be inherited from the parent.
            EmbeddingCredential(id="a"),
            # Credential 'b' overrides api_key but inherits api_base/api_version.
            EmbeddingCredential(id="b", api_key="cred-b-key"),
        ],
    )

    captured_configs: list[EmbeddingModelConfig] = []

    def fake_create_embedder(_self, _provider, _embedder_type, config):
        captured_configs.append(config)
        return object()

    with (
        patch.object(EmbeddingConfig, "_create_embedder", fake_create_embedder),
        patch(
            "openviking.models.embedder.FailoverEmbedder",
            lambda **kwargs: kwargs,
        ),
    ):
        # This call previously raised:
        #   ValueError: OpenAI provider requires 'api_key' to be set
        # because merged_config received api_key=None for credential 'a'.
        EmbeddingConfig(dense=parent)._create_failover_embedder("dense", parent)

    assert len(captured_configs) == 2

    # Credential 'a': all auth fields fall back to parent.
    cfg_a = captured_configs[0]
    assert cfg_a.api_key == "parent-key"
    assert cfg_a.api_base == "https://parent.example.com"
    assert cfg_a.api_version == "2024-01-01"

    # Credential 'b': api_key overridden, api_base/api_version still inherited.
    cfg_b = captured_configs[1]
    assert cfg_b.api_key == "cred-b-key"
    assert cfg_b.api_base == "https://parent.example.com"
    assert cfg_b.api_version == "2024-01-01"


def test_credential_without_inheritance_does_not_raise_at_get_embedder():
    """End-to-end variant: ensure get_embedder() does not re-validate to failure
    when credential omits api_key but parent has it."""
    parent = EmbeddingModelConfig(
        model="text-embedding-3-small",
        dimension=1536,
        provider="openai",
        api_key="parent-key",
        credentials=[EmbeddingCredential(id="a")],
    )
    cfg = EmbeddingConfig(dense=parent)

    captured: list[EmbeddingModelConfig] = []

    def fake_create_embedder(_self, _provider, _embedder_type, config):
        captured.append(config)
        return object()

    with (
        patch.object(EmbeddingConfig, "_create_embedder", fake_create_embedder),
        patch(
            "openviking.models.embedder.FailoverEmbedder",
            lambda **kwargs: kwargs,
        ),
    ):
        cfg._create_failover_embedder("dense", parent)

    # Must not raise; merged config must carry inherited api_key.
    assert len(captured) == 1
    assert captured[0].api_key == "parent-key"


def test_effective_provider_falls_back_to_first_credential():
    """When parent provider is left at default (volcengine) but credentials
    specify a different provider, _effective_provider must return the
    credential's provider so dimension/metadata reflect reality."""
    cfg = EmbeddingModelConfig(
        credentials=[
            EmbeddingCredential(
                id="a",
                provider="openai",
                model="text-embedding-3-small",
                api_key="sk",
            )
        ],
    )

    # Parent provider is the model default ("volcengine"), but the actual
    # request will go to OpenAI.
    assert cfg.provider == "volcengine"
    assert cfg._effective_provider() == "openai"
    assert cfg._effective_model() == "text-embedding-3-small"


def test_effective_model_prefers_credential_when_set():
    """_effective_model returns the first credential's model when given."""
    cfg = EmbeddingModelConfig(
        provider="openai",
        model="parent-model",
        api_key="sk",
        credentials=[
            EmbeddingCredential(id="a", model="cred-model"),
            EmbeddingCredential(id="b"),
        ],
    )
    assert cfg._effective_model() == "cred-model"


def test_effective_model_falls_back_to_parent_when_credential_omits_model():
    """When credentials omit model, parent's model is used."""
    cfg = EmbeddingModelConfig(
        provider="openai",
        model="parent-model",
        api_key="sk",
        credentials=[EmbeddingCredential(id="a", api_key="sk-a")],
    )
    assert cfg._effective_model() == "parent-model"


def test_get_effective_dimension_uses_credential_provider_and_model():
    """Regression: credentials-only OpenAI text-embedding-3-small must yield
    dimension=1536, not the default-provider fallback (2048).
    """
    cfg = EmbeddingModelConfig(
        credentials=[
            EmbeddingCredential(
                id="a",
                provider="openai",
                model="text-embedding-3-small",
                api_key="sk",
            )
        ],
    )
    # Was 2048 before the fix because parent provider defaulted to volcengine.
    assert cfg.get_effective_dimension() == 1536


def test_get_effective_dimension_credential_text_embedding_3_large():
    """text-embedding-3-large via credential => 3072 dim."""
    cfg = EmbeddingModelConfig(
        credentials=[
            EmbeddingCredential(
                id="a",
                provider="openai",
                model="text-embedding-3-large",
                api_key="sk",
            )
        ],
    )
    assert cfg.get_effective_dimension() == 3072


def test_get_effective_dimension_explicit_dimension_wins():
    """Explicit `dimension` always wins over provider/model heuristics."""
    cfg = EmbeddingModelConfig(
        dimension=4096,
        credentials=[
            EmbeddingCredential(
                id="a",
                provider="openai",
                model="text-embedding-3-small",
                api_key="sk",
            )
        ],
    )
    assert cfg.get_effective_dimension() == 4096


def test_credentials_with_conflicting_dimensions_raise():
    """Multiple credentials whose models map to different dimensions must
    fail at startup to prevent silent vector-store corruption.
    """
    import pytest

    with pytest.raises(ValueError, match="conflicting vector dimensions"):
        EmbeddingModelConfig(
            credentials=[
                EmbeddingCredential(
                    id="small",
                    provider="openai",
                    model="text-embedding-3-small",  # 1536
                    api_key="sk-1",
                ),
                EmbeddingCredential(
                    id="large",
                    provider="openai",
                    model="text-embedding-3-large",  # 3072
                    api_key="sk-2",
                ),
            ],
        )


def test_credentials_same_dimension_via_provider_and_model_pass():
    """Two credentials with identical provider+model resolve to same dimension."""
    cfg = EmbeddingModelConfig(
        credentials=[
            EmbeddingCredential(
                id="a",
                provider="openai",
                model="text-embedding-3-small",
                api_key="sk-1",
            ),
            EmbeddingCredential(
                id="b",
                provider="openai",
                model="text-embedding-3-small",
                api_key="sk-2",
            ),
        ],
    )
    assert cfg.get_effective_dimension() == 1536


def test_explicit_parent_dimension_overrides_per_cred_resolution():
    """If parent.dimension is set, all credentials are pinned to that
    dimension and per-cred heuristic resolution is bypassed - so even
    seemingly conflicting providers/models are accepted.
    """
    cfg = EmbeddingModelConfig(
        dimension=1024,
        credentials=[
            EmbeddingCredential(
                id="a",
                provider="openai",
                model="text-embedding-3-small",
                api_key="sk-1",
            ),
            EmbeddingCredential(
                id="b",
                provider="openai",
                model="text-embedding-3-large",
                api_key="sk-2",
            ),
        ],
    )
    assert cfg.get_effective_dimension() == 1024


def test_credentials_with_unknown_dimension_provider_pass():
    """Providers without a known dim lookup (e.g. volcengine) are skipped
    and do not block validation."""
    cfg = EmbeddingModelConfig(
        dimension=1024,
        credentials=[
            EmbeddingCredential(
                id="a",
                provider="volcengine",
                model="ep-A",
                api_key="ark-1",
            ),
            EmbeddingCredential(
                id="b",
                provider="volcengine",
                model="ep-B",
                api_key="ark-2",
            ),
        ],
    )
    assert cfg.get_effective_dimension() == 1024


def test_cross_provider_credentials_with_compatible_dimensions_pass():
    """Two credentials on different providers but with the same dimension
    (e.g. cohere 1024 + voyage 1024 via explicit parent.dimension) are
    accepted; vector-space compatibility is the user's responsibility."""
    cfg = EmbeddingModelConfig(
        dimension=1024,
        credentials=[
            EmbeddingCredential(
                id="vk",
                provider="volcengine",
                model="ep-X",
                api_key="ark",
            ),
            EmbeddingCredential(
                id="oai",
                provider="openai",
                model="text-embedding-3-small",
                api_key="sk",
            ),
        ],
    )
    # Explicit parent dimension wins; openai's natural 1536 is overridden.
    assert cfg.get_effective_dimension() == 1024
