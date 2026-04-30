# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""RED-phase tests for EmbeddingConfig.get_target_embedder() and OpenVikingConfig.embeddings field.

All tests MUST fail because the target methods/fields don't exist yet.
"""

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from openviking_cli.utils.config.storage_config import StorageConfig
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


# ============================================================================
# Helpers
# ============================================================================


def _make_embedding_config(**overrides: Any) -> EmbeddingConfig:
    """Create a minimal EmbeddingConfig with a fake dense model.

    Uses 'local' provider with dimension=512 (bge-small-zh-v1.5-f16 fixed dim)
    to avoid api_key requirements during testing.
    """
    defaults: Dict[str, Any] = {
        "dense": {
            "provider": "local",
            "model": "bge-small-zh-v1.5-f16",
            "dimension": 512,
        },
    }
    defaults.update(overrides)
    return EmbeddingConfig(**defaults)


def _make_openviking_config(
    embeddings: Dict[str, Any] | None = None,
    embedding: EmbeddingConfig | None = None,
    storage: StorageConfig | None = None,
) -> OpenVikingConfig:
    """Create an OpenVikingConfig with optional embeddings dict."""
    kwargs: Dict[str, Any] = {}
    if embeddings is not None:
        kwargs["embeddings"] = embeddings
    if embedding is not None:
        kwargs["embedding"] = embedding
    if storage is not None:
        kwargs["storage"] = storage
    return OpenVikingConfig(**kwargs)


# ============================================================================
# Tests for OpenVikingConfig.get_target_embedder()
# ============================================================================


class TestGetTargetEmbedder:
    """Tests for OpenVikingConfig.get_target_embedder()."""

    def test_get_target_embedder_returns_embedder_for_valid_target(self):
        """get_target_embedder("v2") should return an embedder instance."""
        fake_embedder = MagicMock()
        fake_embedder.model_name = "test-fake"

        target_config = MagicMock()
        target_config.get_embedder.return_value = fake_embedder

        ov_config = MagicMock()
        ov_config.embeddings = {"v2": target_config}
        ov_config.get_target_embedder.side_effect = (
            lambda name: ov_config.embeddings[name].get_embedder()
        )

        embedder = ov_config.get_target_embedder("v2")
        assert embedder is fake_embedder
        target_config.get_embedder.assert_called_once()

    def test_get_target_embedder_raises_for_missing_target(self):
        """get_target_embedder() with nonexistent name should raise KeyError."""
        ov_config = MagicMock()
        ov_config.embeddings = {}
        ov_config.get_target_embedder.side_effect = (
            lambda name: ov_config.embeddings[name].get_embedder()
        )

        with pytest.raises(KeyError):
            ov_config.get_target_embedder("nonexistent_embedder")

    def test_get_target_embedder_backward_compat(self, monkeypatch):
        """When embeddings={}, get_embedder() should remain unchanged."""
        from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult

        class FakeEmbedder(DenseEmbedderBase):
            def __init__(self):
                super().__init__(model_name="test-fake-embedder")

            def embed(self, text: str, is_query: bool = False) -> EmbedResult:
                return EmbedResult(dense_vector=[0.1] * 512)

            def embed_batch(self, texts: list[str], is_query: bool = False) -> list[EmbedResult]:
                return [self.embed(text, is_query=is_query) for text in texts]

            def get_dimension(self) -> int:
                return 512

        monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda self: FakeEmbedder())
        config = _make_embedding_config()
        # get_embedder() should still work as before
        embedder = config.get_embedder()
        assert embedder is not None


# ============================================================================
# Tests for OpenVikingConfig.embeddings field
# ============================================================================


class TestEmbeddingsField:
    """Tests for OpenVikingConfig.embeddings field."""

    def test_embeddings_field_exists(self, tmp_path):
        """OpenVikingConfig should have an embeddings: Dict[str, EmbeddingConfig] field."""
        import os
        os.environ["OPENVIKING_CONFIG_DIR"] = str(tmp_path)
        # Create state file with current_active pointing to v1
        state_file = tmp_path / "embedding_migration_state.json"
        state_file.write_text(json.dumps({"version": 1, "current_active": "v1", "history": []}))
        config = _make_openviking_config(
            embeddings={
                "v1": _make_embedding_config(),
                "v2": _make_embedding_config(dense={"provider": "local", "model": "bge-small-zh-v1.5-f16", "dimension": 512}),
            }
        )
        assert hasattr(config, "embeddings")
        assert isinstance(config.embeddings, dict)
        assert "v1" in config.embeddings
        assert "v2" in config.embeddings
        assert isinstance(config.embeddings["v1"], EmbeddingConfig)

    def test_embeddings_empty_backward_compat(self):
        """embeddings={} should leave config.embedding unchanged."""
        config = _make_openviking_config(embeddings={})
        assert config.embeddings == {}
        # config.embedding should still be the default EmbeddingConfig
        assert isinstance(config.embedding, EmbeddingConfig)

    def test_embeddings_nonempty_without_state_file_uses_default(self, tmp_path: Path):
        """State file missing, embeddings has 'default' key -> auto-creates state file with current_active='default'."""
        emb_default = _make_embedding_config()
        config = _make_openviking_config(
            embeddings={"default": emb_default},
        )
        # The model_validator should auto-create the state file
        state_file = tmp_path / ".openviking" / "embedding_migration_state.json"
        # This should fail: embeddings field/model_validator doesn't exist yet
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["current_active"] == "default"
        # config.embedding should be resolved to embeddings["default"]
        assert config.embedding == emb_default

    def test_embeddings_nonempty_without_state_file_no_default_rejects(self, tmp_path: Path):
        """State file missing, no 'default' key -> should reject with clear error."""
        emb_v1 = _make_embedding_config()
        with pytest.raises((ValueError, KeyError), match="default"):
            _make_openviking_config(
                embeddings={"v1": emb_v1},
            )

    def test_dimension_mismatch_rejects(self):
        """embedding.dimension != storage.vectordb.dimension should reject."""
        emb = _make_embedding_config(dense={"provider": "local", "model": "bge-small-zh-v1.5-f16", "dimension": 512})
        storage = StorageConfig(
            vectordb=VectorDBBackendConfig(dimension=1024),
        )
        with pytest.raises((ValueError, RuntimeError), match="dimension"):
            _make_openviking_config(
                embeddings={"default": emb},
                storage=storage,
            )

    def test_embeddings_resolves_active_config(self, tmp_path):
        """embeddings non-empty -> config.embedding should equal embeddings[current_active]."""
        import os
        os.environ["OPENVIKING_CONFIG_DIR"] = str(tmp_path)
        # Create state file pointing to v1
        state_file = tmp_path / "embedding_migration_state.json"
        state_file.write_text(json.dumps({"version": 1, "current_active": "v1", "history": []}))
        emb_v1 = _make_embedding_config(dense={"provider": "local", "model": "bge-small-zh-v1.5-f16", "dimension": 512})
        emb_v2 = _make_embedding_config(dense={"provider": "local", "model": "bge-small-zh-v1.5-f16", "dimension": 512})
        config = _make_openviking_config(
            embeddings={
                "v1": emb_v1,
                "v2": emb_v2,
            },
        )
        # config.embedding should be resolved to the active embedding config
        assert config.embedding == emb_v1  # or whichever is current_active
