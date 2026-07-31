# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.models.embedder.local_embedders import (
    DEFAULT_BGE_ZH_QUERY_INSTRUCTION,
    DEFAULT_LOCAL_DENSE_MODEL,
    LocalDenseEmbedder,
)
from openviking.storage.errors import EmbeddingConfigurationError
from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=1024 * 1024):
        del chunk_size
        yield self.payload


class _FakeLlama:
    init_kwargs = []
    inputs = []

    def __init__(self, **kwargs):
        self.__class__.init_kwargs.append(kwargs)

    def create_embedding(self, payload):
        self.__class__.inputs.append(payload)
        return {"data": [{"embedding": [0.1] * 512}]}


@pytest.fixture(autouse=True)
def _reset_fake_llama():
    _FakeLlama.init_kwargs = []
    _FakeLlama.inputs = []


def test_embedding_config_defaults_to_local_dense():
    config = EmbeddingConfig()

    assert config.dense is not None
    assert config.dense.provider == "local"
    assert config.dense.model == DEFAULT_LOCAL_DENSE_MODEL
    assert config.dimension == 512


def test_local_embedding_config_rejects_unknown_model():
    with pytest.raises(ValueError, match="Unknown local embedding model"):
        EmbeddingModelConfig(
            provider="local",
            model="unknown-local-model",
        )


def test_local_embedder_requires_optional_dependency(monkeypatch, tmp_path):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"gguf")

    monkeypatch.setattr(
        "openviking.models.embedder.local_embedders.importlib.import_module",
        lambda _name: (_ for _ in ()).throw(ImportError("missing llama_cpp")),
    )

    with pytest.raises(EmbeddingConfigurationError, match="openviking\\[local-embed\\]"):
        LocalDenseEmbedder(model_path=str(model_path))


def test_local_embedder_uses_explicit_model_path(monkeypatch, tmp_path):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"gguf")

    monkeypatch.setattr(
        "openviking.models.embedder.local_embedders.importlib.import_module",
        lambda _name: SimpleNamespace(Llama=_FakeLlama),
    )

    embedder = LocalDenseEmbedder(model_path=str(model_path))

    assert Path(_FakeLlama.init_kwargs[-1]["model_path"]) == model_path.resolve()
    result = embedder.embed("你好", is_query=False)
    assert len(result.dense_vector) == 512
    assert _FakeLlama.inputs[-1] == "你好"


def test_local_embedder_downloads_default_model_and_prefixes_query(monkeypatch, tmp_path):
    downloaded = {"count": 0}

    def _fake_get(url, stream=True, timeout=(10, 300)):
        assert "bge-small-zh-v1.5-f16.gguf" in url
        assert stream is True
        assert timeout == (10, 300)
        downloaded["count"] += 1
        return _FakeResponse(b"gguf")

    monkeypatch.setattr(
        "openviking.models.embedder.local_embedders.importlib.import_module",
        lambda _name: SimpleNamespace(Llama=_FakeLlama),
    )
    monkeypatch.setattr("openviking.models.embedder.local_embedders.requests.get", _fake_get)

    embedder = LocalDenseEmbedder(cache_dir=str(tmp_path))

    assert downloaded["count"] == 1
    assert (tmp_path / "bge-small-zh-v1.5-f16.gguf").exists()

    result = embedder.embed("测试问题", is_query=True)
    assert len(result.dense_vector) == 512
    assert _FakeLlama.inputs[-1] == f"{DEFAULT_BGE_ZH_QUERY_INSTRUCTION}测试问题"
