from unittest.mock import MagicMock

from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def test_embedding_config_dimension_prefers_explicit_dimension(monkeypatch):
    get_embedder = MagicMock()
    monkeypatch.setattr(EmbeddingConfig, "get_embedder", get_embedder)

    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="test-key",
            dimension=768,
        )
    )

    assert config.get_dimension() == 768
    assert config.dimension == 768
    get_embedder.assert_not_called()


def test_embedding_config_dimension_detects_embedder_dimension_without_mutating_config(monkeypatch):
    fake_embedder = MagicMock()
    fake_embedder.get_dimension.return_value = 1024
    get_embedder = MagicMock(return_value=fake_embedder)
    monkeypatch.setattr(EmbeddingConfig, "get_embedder", get_embedder)

    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            model="Qwen/Qwen3-Embedding-0.6B",
            api_key="test-key",
        )
    )

    assert config.dense is not None
    assert config.dense.dimension is None
    assert config.get_dimension() == 1024
    assert config.dimension == 1024
    assert config.dense.dimension is None

    get_embedder.assert_called_once_with()
    fake_embedder.get_dimension.assert_called_once_with()