# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openviking.storage.vectordb.collection.local_collection import LocalCollection
from openviking.storage.vectordb.collection.result import (
    DataItem,
    FetchDataInCollectionResult,
    UpsertDataResult,
)


def _build_local_collection(
    existing_by_id: dict[str, dict], captured: list[dict]
) -> LocalCollection:
    collection = object.__new__(LocalCollection)
    collection.meta = SimpleNamespace(primary_key="id")
    collection.data_processor = SimpleNamespace(
        validate_and_process=lambda raw: {
            **({"name": ""} if "name" not in raw else {}),
            **({"active_count": 0} if "active_count" not in raw else {}),
            **({"tags": []} if "tags" not in raw else {}),
            **raw,
        }
    )
    collection.fetch_data = lambda primary_keys: FetchDataInCollectionResult(
        items=[
            DataItem(id=key, fields=existing_by_id[key])
            for key in primary_keys
            if key in existing_by_id
        ],
        ids_not_exist=[key for key in primary_keys if key not in existing_by_id],
    )
    collection._write_data_list = lambda data_list, ttl=0: captured.append(
        {"data_list": data_list, "ttl": ttl}
    ) or UpsertDataResult(ids=[row["id"] for row in data_list])
    return collection


def test_local_collection_update_preserves_omitted_fields():
    captured = []
    collection = _build_local_collection(
        {
            "doc-1": {
                "id": "doc-1",
                "name": "before",
                "active_count": 7,
                "tags": ["alpha", "beta"],
            }
        },
        captured,
    )

    result = collection.update_data([{"id": "doc-1", "name": "after"}])

    assert result.ids == ["doc-1"]
    assert captured == [
        {
            "data_list": [
                {
                    "id": "doc-1",
                    "name": "after",
                    "active_count": 7,
                    "tags": ["alpha", "beta"],
                }
            ],
            "ttl": 0,
        }
    ]


def test_local_collection_update_overwrites_explicit_empty_values():
    captured = []
    collection = _build_local_collection(
        {
            "doc-1": {
                "id": "doc-1",
                "name": "before",
                "active_count": 7,
                "tags": ["alpha", "beta"],
            }
        },
        captured,
    )

    result = collection.update_data([{"id": "doc-1", "tags": []}])

    assert result.ids == ["doc-1"]
    assert captured[0]["data_list"][0] == {
        "id": "doc-1",
        "name": "before",
        "active_count": 7,
        "tags": [],
    }


def test_local_collection_update_overwrites_explicit_none_values():
    captured = []
    collection = _build_local_collection(
        {
            "doc-1": {
                "id": "doc-1",
                "name": "before",
                "active_count": 7,
                "tags": ["alpha", "beta"],
            }
        },
        captured,
    )

    result = collection.update_data([{"id": "doc-1", "name": None}])

    assert result.ids == ["doc-1"]
    assert captured[0]["data_list"][0] == {
        "id": "doc-1",
        "name": None,
        "active_count": 7,
        "tags": ["alpha", "beta"],
    }


def test_local_collection_update_validates_after_merge_for_required_fields():
    captured = []
    collection = _build_local_collection(
        {
            "doc-1": {
                "id": "doc-1",
                "name": "before",
                "vector": [0.1, 0.2, 0.3, 0.4],
                "active_count": 7,
                "tags": ["alpha"],
            }
        },
        captured,
    )

    collection.data_processor = SimpleNamespace(
        validate_and_process=lambda raw: (
            (_ for _ in ()).throw(ValueError("vector required")) if "vector" not in raw else raw
        )
    )

    result = collection.update_data([{"id": "doc-1", "name": "after"}])

    assert result.ids == ["doc-1"]
    assert captured[0]["data_list"][0]["vector"] == [0.1, 0.2, 0.3, 0.4]
    assert captured[0]["data_list"][0]["name"] == "after"


def test_local_collection_update_merges_multiple_rows_independently():
    captured = []
    collection = _build_local_collection(
        {
            "doc-1": {
                "id": "doc-1",
                "name": "before-1",
                "active_count": 7,
                "tags": ["alpha"],
            },
            "doc-2": {
                "id": "doc-2",
                "name": "before-2",
                "active_count": 9,
                "tags": ["beta"],
            },
        },
        captured,
    )

    result = collection.update_data(
        [
            {"id": "doc-1", "name": "after-1"},
            {"id": "doc-2", "tags": []},
        ]
    )

    assert result.ids == ["doc-1", "doc-2"]
    assert captured == [
        {
            "data_list": [
                {
                    "id": "doc-1",
                    "name": "after-1",
                    "active_count": 7,
                    "tags": ["alpha"],
                },
                {
                    "id": "doc-2",
                    "name": "before-2",
                    "active_count": 9,
                    "tags": [],
                },
            ],
            "ttl": 0,
        }
    ]


def test_local_collection_update_requires_existing_record():
    collection = _build_local_collection({}, [])

    with pytest.raises(ValueError, match="not found"):
        collection.update_data([{"id": "missing", "name": "after"}])


def test_local_collection_update_rejects_batch_when_any_record_is_missing():
    captured = []
    collection = _build_local_collection(
        {
            "doc-1": {
                "id": "doc-1",
                "name": "before-1",
                "active_count": 7,
                "tags": ["alpha"],
            }
        },
        captured,
    )

    with pytest.raises(ValueError, match="not found"):
        collection.update_data(
            [
                {"id": "doc-1", "name": "after-1"},
                {"id": "doc-404", "name": "after-404"},
            ]
        )

    assert captured == []


def test_local_collection_update_requires_primary_key():
    collection = _build_local_collection({}, [])

    with pytest.raises(ValueError, match="primary key"):
        collection.update_data([{"name": "after"}])
