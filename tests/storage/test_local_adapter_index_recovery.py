# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import shutil

import pytest

from openviking.storage.vectordb.engine import ENGINE_VARIANT
from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter


def _schema() -> dict:
    return {
        "CollectionName": "context",
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            {"FieldName": "kind", "FieldType": "string"},
        ],
        "ScalarIndex": ["kind"],
    }


class _ExistingCollection:
    def __init__(self, *, indexes: set[str]) -> None:
        self.indexes = indexes
        self.created_indexes: list[tuple[str, dict]] = []

    def has_index(self, index_name: str) -> bool:
        return index_name in self.indexes

    def get_meta_data(self) -> dict:
        return _schema()

    def create_index(self, index_name: str, meta_data: dict) -> None:
        self.created_indexes.append((index_name, meta_data))
        self.indexes.add(index_name)


def test_existing_local_collection_rebuilds_missing_configured_index() -> None:
    adapter = LocalCollectionAdapter(
        collection_name="context",
        project_path="",
        index_name="configured_index",
    )
    collection = _ExistingCollection(indexes=set())
    adapter._collection = collection

    created = adapter.create_collection(
        "context",
        _schema(),
        distance="cosine",
        sparse_weight=0.25,
        index_name="configured_index",
    )

    assert created is False
    assert collection.created_indexes == [
        (
            "configured_index",
            {
                "IndexName": "configured_index",
                "VectorIndex": {
                    "IndexType": "flat_hybrid",
                    "Distance": "cosine",
                    "Quant": "int8",
                    "EnableSparse": True,
                    "SearchWithSparseLogitAlpha": 0.25,
                },
                "ScalarIndex": ["kind"],
            },
        )
    ]


def test_existing_local_collection_keeps_healthy_configured_index() -> None:
    adapter = LocalCollectionAdapter(
        collection_name="context",
        project_path="",
        index_name="configured_index",
    )
    collection = _ExistingCollection(indexes={"configured_index"})
    adapter._collection = collection

    created = adapter.create_collection(
        "context",
        _schema(),
        distance="cosine",
        sparse_weight=0.25,
        index_name="configured_index",
    )

    assert created is False
    assert collection.created_indexes == []


@pytest.mark.skipif(
    ENGINE_VARIANT == "unavailable",
    reason="vectordb native engine is not available",
)
def test_local_adapter_restores_missing_index_from_persisted_store(tmp_path) -> None:
    project_path = tmp_path / "vectordb"
    collection_path = project_path / "context"
    index_name = "configured_index"
    adapter = LocalCollectionAdapter(
        collection_name="context",
        project_path=str(project_path),
        index_name=index_name,
    )
    assert (
        adapter.create_collection(
            "context",
            _schema(),
            distance="cosine",
            sparse_weight=0.0,
            index_name=index_name,
        )
        is True
    )
    adapter.get_collection().upsert_data(
        [{"id": "record-1", "vector": [0.1, 0.2, 0.3, 0.4], "kind": "example"}]
    )
    adapter.close()

    shutil.rmtree(collection_path / "index")

    recovered = LocalCollectionAdapter(
        collection_name="context",
        project_path=str(project_path),
        index_name=index_name,
    )
    try:
        assert (
            recovered.create_collection(
                "context",
                _schema(),
                distance="cosine",
                sparse_weight=0.0,
                index_name=index_name,
            )
            is False
        )
        collection = recovered.get_collection()
        assert collection.has_index(index_name)
        assert collection.get_index_meta_data(index_name)["VectorIndex"]["Distance"] == "cosine"
        result = collection.search_by_vector(
            index_name,
            dense_vector=[0.1, 0.2, 0.3, 0.4],
            limit=1,
        )
        assert [item.id for item in result.data] == ["record-1"]
    finally:
        recovered.close()
