# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection


@pytest.fixture
def collection(tmp_path):
    schema = {
        "CollectionName": "partial_update",
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "name", "FieldType": "string"},
            {"FieldName": "count", "FieldType": "int64"},
            {"FieldName": "tags", "FieldType": "list<string>"},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
        ],
    }
    collection = get_or_create_local_collection(meta_data=schema, path=str(tmp_path))
    try:
        collection.upsert_data(
            [
                {
                    "id": "first",
                    "name": "alpha",
                    "count": 7,
                    "tags": ["one"],
                    "vector": [1.0, 0.0, 0.0, 0.0],
                },
                {
                    "id": "second",
                    "name": "beta",
                    "count": 9,
                    "tags": ["two"],
                    "vector": [0.0, 1.0, 0.0, 0.0],
                },
            ]
        )
        yield collection
    finally:
        collection.close()


def test_update_merges_each_record_and_preserves_omitted_fields(collection):
    result = collection.update_data(
        [{"id": "first", "name": "updated"}, {"id": "second", "tags": []}]
    )

    assert result.ids == ["first", "second"]
    rows = {row.id: row.fields for row in collection.fetch_data(result.ids).items}
    assert rows == {
        "first": {
            "id": "first",
            "name": "updated",
            "count": 7,
            "tags": ["one"],
            "vector": [1.0, 0.0, 0.0, 0.0],
        },
        "second": {
            "id": "second",
            "name": "beta",
            "count": 9,
            "tags": [],
            "vector": [0.0, 1.0, 0.0, 0.0],
        },
    }


@pytest.mark.parametrize(
    ("invalid_patch", "error"),
    [
        pytest.param({"name": "second update"}, "primary key", id="missing-key"),
        pytest.param({"id": "missing", "name": "new"}, "record not found", id="missing-row"),
        pytest.param({"id": "second", "name": None}, "valid string", id="invalid-null"),
    ],
)
def test_invalid_update_does_not_persist_any_part_of_the_batch(collection, invalid_patch, error):
    keys = ["first", "second", "missing"]
    before = collection.fetch_data(keys)

    with pytest.raises(ValueError, match=error):
        collection.update_data([{"id": "first", "name": "must not persist"}, invalid_patch])

    after = collection.fetch_data(keys)
    assert {row.id: row.fields for row in after.items} == {
        row.id: row.fields for row in before.items
    }
    assert after.ids_not_exist == ["missing"]
