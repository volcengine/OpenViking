# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistent collection contracts for large batches and reopen."""

from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

SCHEMA = {
    "CollectionName": "batch_records",
    "Fields": [
        {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
        {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
        {"FieldName": "group", "FieldType": "string"},
    ],
}
INDEX = {
    "IndexName": "default",
    "VectorIndex": {"IndexType": "flat", "Distance": "l2"},
    "ScalarIndex": ["id", "group"],
}
QUERY = [0.0, 0.0, 0.0, 0.0]


def test_batch_delete_updates_read_search_and_aggregate(tmp_path):
    collection = get_or_create_local_collection(meta_data=SCHEMA, path=str(tmp_path))
    try:
        for start in range(0, 50_000, 2_000):
            ids = list(range(start, start + 2_000))
            result = collection.upsert_data(
                [
                    {"id": key, "vector": [key / 50_000, 0.0, 0.0, 0.0], "group": str(key % 100)}
                    for key in ids
                ]
            )
            assert result.ids == ids
        collection.create_index("default", INDEX)
        assert collection.aggregate_data("default").agg == {"_total": 50_000}

        for start in range(0, 25_000, 1_000):
            collection.delete_data(list(range(start, start + 1_000)))

        fetched = collection.fetch_data([0, 24_999, 25_000, 49_999])
        assert {item.id for item in fetched.items} == {25_000, 49_999}
        found = collection.search_by_vector("default", dense_vector=QUERY, limit=30_000)
        assert [item.id for item in found.data] == list(range(25_000, 50_000))
        assert collection.aggregate_data("default").agg == {"_total": 25_000}

        filters = {
            "op": "or",
            "conds": [
                {
                    "op": "and",
                    "conds": [
                        {"op": "range", "field": "id", "lt": 30_000},
                        {"op": "must", "field": "group", "conds": ["0"]},
                    ],
                },
                {"op": "regex", "field": "group", "pattern": "^99$"},
            ],
        }
        result = collection.search_by_vector(
            "default", dense_vector=QUERY, filters=filters, limit=400
        )
        expected = set(range(25_000, 30_000, 100)) | set(range(25_099, 50_000, 100))
        assert {item.id for item in result.data} == expected
        assert len(result.data) == len(expected)
    finally:
        collection.close()


def test_aggregate_data_groups_records_without_a_filter(tmp_path):
    collection = get_or_create_local_collection(meta_data=SCHEMA, path=str(tmp_path))
    try:
        collection.upsert_data(
            [{"id": key, "vector": QUERY, "group": group} for key, group in enumerate("aab")]
        )
        collection.create_index("default", INDEX)

        counts = {"a": 2, "b": 1}
        both_groups = {"op": "must", "field": "group", "conds": ["a", "b"]}
        assert collection.aggregate_data("default", field="group", filters=both_groups).agg == counts
        assert collection.aggregate_data("default", field="group").agg == counts
    finally:
        collection.close()


def test_reopen_preserves_search_after_batch_update(tmp_path):
    collection = get_or_create_local_collection(meta_data=SCHEMA, path=str(tmp_path))
    try:
        collection.upsert_data(
            [
                {"id": 1, "vector": [1.0, 0.0, 0.0, 0.0], "group": "old"},
                {"id": 2, "vector": [2.0, 0.0, 0.0, 0.0], "group": "old"},
                {"id": 3, "vector": [3.0, 0.0, 0.0, 0.0], "group": "unchanged"},
            ]
        )
        collection.create_index("default", INDEX)
        result = collection.upsert_data(
            [
                {"id": 1, "vector": [4.0, 0.0, 0.0, 0.0], "group": "updated"},
                {"id": 2, "vector": QUERY, "group": "updated"},
            ]
        )
        assert result.ids == [1, 2]
        collection.delete_data([3])
    finally:
        collection.close()

    reopened = get_or_create_local_collection(path=str(tmp_path))
    try:
        result = reopened.search_by_vector("default", dense_vector=QUERY, limit=3)
        assert [item.id for item in result.data] == [2, 1]
        assert {item.id: item.fields["group"] for item in reopened.fetch_data([1, 2, 3]).items} == {
            1: "updated",
            2: "updated",
        }
    finally:
        reopened.close()
