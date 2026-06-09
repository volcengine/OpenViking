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
    with patch.object(EmbeddingConfig, "_create_embedder", fake_create_embedder), patch(
        "openviking.models.embedder.FailoverEmbedder",
        lambda **kwargs: kwargs,
    ):
        cfg._create_failover_embedder("dense", parent)

    assert captured_models == ["endpoint-a", "parent-model"]
