# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0


import pytest

from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking.storage.vectordb_adapters.qdrant_adapter import QdrantCollectionAdapter
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def _build_config() -> VectorDBBackendConfig:
    return VectorDBBackendConfig.model_validate(
        {
            "backend": "qdrant",
            "project": "default",
            "name": "context",
            "index_name": "default",
            "distance_metric": "cosine",
            "qdrant": {
                "url": "http://qdrant:6333/",
                "api_key": "test-key",
                "timeout_seconds": 7,
                "dense_vector_name": "vector",
                "sparse_vector_name": "sparse_vector",
                "meta_collection_name": "__openviking_meta",
                "enable_text_index": True,
            },
        }
    )


def test_qdrant_backend_config_validation():
    config = _build_config()
    assert config.backend == "qdrant"
    assert config.qdrant is not None
    assert config.qdrant.url == "http://qdrant:6333"


def test_factory_creates_qdrant_adapter():
    adapter = create_collection_adapter(_build_config())
    assert isinstance(adapter, QdrantCollectionAdapter)
    assert adapter.mode == "qdrant"
    assert adapter.collection_name == "context"
    assert adapter.index_name == "default"
    assert adapter.physical_collection_name == "default__context"


def test_existing_physical_collection_without_metadata_is_rejected_and_closed(monkeypatch):
    class FakeQdrantCollection:
        def __init__(self) -> None:
            self.closed = False

        def collection_exists(self) -> bool:
            return True

        def has_openviking_metadata(self) -> bool:
            return False

        def close(self) -> None:
            self.closed = True

    candidate = FakeQdrantCollection()
    adapter = QdrantCollectionAdapter.from_config(_build_config())
    monkeypatch.setattr(adapter, "_new_qdrant_collection", lambda: candidate)

    with pytest.raises(RuntimeError, match="OpenViking metadata is missing"):
        adapter.collection_exists()

    assert candidate.closed is True
