# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.storage.vectordb.collection.local_collection import (
    get_or_create_local_collection,
)
from openviking.storage.vectordb.index import cuvs_index


class FakeCuVSRuntime:
    def __init__(self, metric):
        self.metric = metric

    def build(self, dataset):
        return [list(vector) for vector in dataset]

    def search(self, index, query, limit, mask):
        rows = []
        for offset, vector in enumerate(index):
            if mask is not None and not mask[offset]:
                continue
            if self.metric == "sqeuclidean":
                distance = sum(
                    (left - right) ** 2 for left, right in zip(query, vector, strict=True)
                )
                key = distance
            else:
                distance = sum(left * right for left, right in zip(query, vector, strict=True))
                key = -distance
            rows.append((key, offset, distance))
        rows.sort()
        rows = rows[:limit]
        return [row[1] for row in rows], [row[2] for row in rows]

    def close(self):
        pass


def patch_cuvs_runtime(monkeypatch):
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda algorithm, metric, build_params, search_params: FakeCuVSRuntime(metric),
    )


def test_local_collection_routes_dense_search_to_cuvs(monkeypatch):
    patch_cuvs_runtime(monkeypatch)
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "cuvs_integration",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "account_id", "FieldType": "string"},
                {"FieldName": "rank", "FieldType": "int64"},
                {"FieldName": "created_at", "FieldType": "date_time"},
            ],
        },
        config={
            "dense_search": {
                "backend": "cuvs",
                "algorithm": "brute_force",
            }
        },
    )
    try:
        collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
                "ScalarIndex": ["account_id", "rank", "created_at"],
            },
        )
        collection.upsert_data(
            [
                {
                    "id": "first",
                    "vector": [1, 0, 0, 0],
                    "account_id": "a",
                    "rank": 3,
                    "created_at": "2026-07-02T00:00:00Z",
                },
                {
                    "id": "second",
                    "vector": [0, 1, 0, 0],
                    "account_id": "a",
                    "rank": 2,
                    "created_at": "2026-07-01T00:00:00Z",
                },
                {
                    "id": "hidden",
                    "vector": [1, 0, 0, 0],
                    "account_id": "b",
                    "rank": 1,
                    "created_at": "2026-06-01T00:00:00Z",
                },
            ]
        )

        result = collection.search_by_vector(
            "default",
            dense_vector=[1, 0, 0, 0],
            limit=3,
            filters={"op": "must", "field": "account_id", "conds": ["a"]},
        )
        assert [item.id for item in result.data] == ["first", "second"]

        # date_time conversion is deliberately delegated to the native engine.
        result = collection.search_by_vector(
            "default",
            dense_vector=[1, 0, 0, 0],
            limit=3,
            filters={
                "op": "and",
                "conds": [
                    {"op": "must", "field": "account_id", "conds": ["a"]},
                    {
                        "op": "range",
                        "field": "created_at",
                        "gte": "2026-07-02T00:00:00Z",
                    },
                ],
            },
        )
        assert [item.id for item in result.data] == ["first"]
        assert len(collection.search_by_scalar("default", "rank", limit=3).data) == 3

        collection.update_data(
            [
                {
                    "id": "second",
                    "vector": [2, 0, 0, 0],
                    "account_id": "a",
                    "rank": 2,
                }
            ]
        )
        collection.delete_data(["first"])
        result = collection.search_by_vector("default", dense_vector=[1, 0, 0, 0], limit=3)
        assert [item.id for item in result.data] == ["second", "hidden"]
    finally:
        collection.close()


def test_persistent_collection_rehydrates_cuvs_from_local_store(monkeypatch, tmp_path):
    patch_cuvs_runtime(monkeypatch)
    path = str(tmp_path / "cuvs-persistent")
    config = {"dense_search": {"backend": "cuvs", "algorithm": "brute_force"}}
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "cuvs_persistent",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "account_id", "FieldType": "string"},
            ],
        },
        path=path,
        config=config,
    )
    collection.create_index(
        "default",
        {
            "IndexName": "default",
            "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            "ScalarIndex": ["account_id"],
        },
    )
    collection.upsert_data([{"id": "persisted", "vector": [1, 0, 0, 0], "account_id": "a"}])
    collection.close()

    reopened = get_or_create_local_collection(path=path, config=config)
    try:
        result = reopened.search_by_vector("default", dense_vector=[1, 0, 0, 0], limit=1)
        assert [item.id for item in result.data] == ["persisted"]
    finally:
        reopened.close()
