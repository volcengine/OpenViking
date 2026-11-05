# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from collections.abc import Iterator

import pytest

from openviking.storage.vectordb.collection.local_collection import (
    get_or_create_local_collection,
)


@pytest.fixture
def level_collection() -> Iterator:
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "aggregate_contract",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "uri", "FieldType": "path"},
                {"FieldName": "level", "FieldType": "int64"},
            ],
        }
    )
    vector = [1.0, 0.0, 0.0, 0.0]
    collection.upsert_data(
        [
            {"id": "l0", "vector": vector, "uri": "/docs/.abstract.md", "level": 0},
            {"id": "l1", "vector": vector, "uri": "/docs/.overview.md", "level": 1},
            {"id": "l2-a", "vector": vector, "uri": "/docs/a.md", "level": 2},
            {"id": "l2-b", "vector": vector, "uri": "/docs/sub/b.md", "level": 2},
            {"id": "legacy", "vector": vector, "uri": "/docs/legacy.md"},
            {"id": "outside", "vector": vector, "uri": "/other/c.md", "level": 2},
        ]
    )
    collection.create_index(
        "default",
        {
            "IndexName": "default",
            "VectorIndex": {"IndexType": "flat"},
            "ScalarIndex": ["uri", "level"],
        },
    )
    try:
        yield collection
    finally:
        collection.drop()


def _path_scope(path: str) -> dict:
    return {"op": "must", "field": "uri", "conds": [path], "para": "-d=-1"}


def test_local_grouped_count_returns_only_level_buckets(level_collection):
    result = level_collection.aggregate_data(
        "default",
        op="count",
        field="level",
        filters=_path_scope("/docs"),
    )

    assert result.agg == {"0": 2, "1": 1, "2": 2}


def test_local_unfiltered_grouped_count_honors_level_field(level_collection):
    result = level_collection.aggregate_data("default", op="count", field="level")

    assert result.agg == {"0": 2, "1": 1, "2": 3}


def test_local_ungrouped_count_keeps_total_contract(level_collection):
    result = level_collection.aggregate_data("default", op="count")

    assert result.agg == {"_total": 6}


def test_local_grouped_count_omits_zero_buckets(level_collection):
    result = level_collection.aggregate_data(
        "default",
        op="count",
        field="level",
        filters=_path_scope("/missing"),
    )

    assert result.agg == {}


def test_local_string_bucket_matching_field_name_is_preserved():
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "aggregate_string_contract",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "category", "FieldType": "string"},
            ],
        }
    )
    collection.upsert_data(
        [
            {
                "id": "same-as-field",
                "vector": [1.0, 0.0, 0.0, 0.0],
                "category": "category",
            }
        ]
    )
    collection.create_index(
        "default",
        {
            "IndexName": "default",
            "VectorIndex": {"IndexType": "flat"},
            "ScalarIndex": ["category"],
        },
    )
    try:
        result = collection.aggregate_data("default", op="count", field="category")
        assert result.agg == {"category": 1}
    finally:
        collection.drop()


def test_local_grouped_count_missing_index_raises():
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "aggregate_missing_index",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "level", "FieldType": "int64"},
            ],
        }
    )
    try:
        with pytest.raises(RuntimeError, match="Index 'missing' does not exist"):
            collection.aggregate_data("missing", op="count", field="level")
    finally:
        collection.drop()
