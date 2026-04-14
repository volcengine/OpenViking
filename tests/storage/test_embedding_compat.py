# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace

import pytest

from openviking.storage.embedding_compat import (
    ensure_embedding_collection_compatibility,
    load_embedding_metadata,
)
from openviking_cli.exceptions import EmbeddingCompatibilityError


class _FakeStorage:
    def __init__(self, collection_info):
        self._collection_info = collection_info

    async def get_collection_info(self):
        return self._collection_info


def _make_config(tmp_path, *, model: str, dimension: int = 2048, text_source: str = "summary_first"):
    vectordb = SimpleNamespace(
        backend="local",
        path=str(tmp_path),
        name="context",
    )
    storage = SimpleNamespace(vectordb=vectordb)
    embedding = SimpleNamespace(
        dimension=dimension,
        compatibility_identity=lambda: {
            "mode": "dense",
            "text_source": text_source,
            "dense": {
                "provider": "openai",
                "model": model,
                "dimension": dimension,
            },
            "sparse": None,
            "hybrid": None,
        },
    )
    return SimpleNamespace(storage=storage, embedding=embedding)


@pytest.mark.asyncio
async def test_ensure_embedding_collection_compatibility_writes_baseline(tmp_path):
    config = _make_config(tmp_path, model="text-embedding-3-small")
    storage = _FakeStorage({"vector_dim": 2048, "count": 0})

    meta_path = await ensure_embedding_collection_compatibility(
        storage,
        config,
        config_path="/tmp/test-ov.conf",
    )

    assert meta_path is not None
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["embedding"]["dense"]["model"] == "text-embedding-3-small"
    assert payload["embedding"]["dense"]["dimension"] == 2048
    assert load_embedding_metadata(config.storage.vectordb)["embedding"] == payload["embedding"]


@pytest.mark.asyncio
async def test_ensure_embedding_collection_compatibility_raises_on_model_mismatch(tmp_path):
    initial_config = _make_config(tmp_path, model="text-embedding-3-small")
    storage = _FakeStorage({"vector_dim": 2048, "count": 12})
    await ensure_embedding_collection_compatibility(storage, initial_config)

    new_config = _make_config(tmp_path, model="text-embedding-3-large")
    with pytest.raises(EmbeddingCompatibilityError) as exc_info:
        await ensure_embedding_collection_compatibility(
            storage,
            new_config,
            config_path="/tmp/test-ov.conf",
        )

    assert "Embedding configuration changed" in str(exc_info.value)
    assert "openviking-rebuild-vectors --all-accounts --config /tmp/test-ov.conf" in str(
        exc_info.value
    )


@pytest.mark.asyncio
async def test_ensure_embedding_collection_compatibility_raises_on_dimension_mismatch_without_metadata(
    tmp_path,
):
    config = _make_config(tmp_path, model="text-embedding-3-small", dimension=2048)
    storage = _FakeStorage({"vector_dim": 1536, "count": 9})

    with pytest.raises(EmbeddingCompatibilityError) as exc_info:
        await ensure_embedding_collection_compatibility(
            storage,
            config,
            config_path="/tmp/test-ov.conf",
        )

    assert "dimension does not match" in str(exc_info.value)
