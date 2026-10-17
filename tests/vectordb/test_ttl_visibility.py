# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from pathlib import Path

import pytest

from openviking.storage.vectordb.collection.local_collection import (
    get_or_create_local_collection,
)
from openviking.storage.vectordb.store.store_manager import StoreManager

META = {
    "CollectionName": "ttl_visibility",
    "Fields": [
        {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
        {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
        {"FieldName": "text", "FieldType": "string"},
    ],
}
BASE_TIME_NS = 1_800_000_000_000_000_000


@pytest.fixture
def clock(monkeypatch):
    current = {"ns": BASE_TIME_NS}
    monkeypatch.setattr("time.time_ns", lambda: current["ns"])
    return current


@pytest.fixture
def collection(clock):
    instance = get_or_create_local_collection(
        meta_data=META,
        config={"ttl_cleanup_seconds": 0},
    )
    try:
        yield instance
    finally:
        instance.close()


def row(record_id: int, text: str) -> dict:
    return {
        "id": record_id,
        "vector": [1.0, 0.0, 0.0, 0.0],
        "text": text,
    }


def inner(collection):
    return collection._Collection__collection


def ttl_rows(collection) -> list[tuple[str, bytes]]:
    return inner(collection).store_mgr.storage.read_all(StoreManager.TTLTable)


def test_expired_records_are_hidden_from_all_read_paths_without_periodic_cleanup(clock, collection):
    collection.upsert_data([row(1, "expired")], ttl=1)
    collection.upsert_data([row(2, "active")], ttl=60)
    collection.upsert_data([row(3, "permanent")])
    collection.create_index("idx", {"IndexName": "idx", "VectorIndex": {"IndexType": "flat"}})

    assert [item.id for item in collection.fetch_data([1, 2, 3]).items] == [1, 2, 3]
    clock["ns"] += 2_000_000_000

    fetched = collection.fetch_data([1, 2, 3])
    assert [item.id for item in fetched.items] == [2, 3]
    assert fetched.ids_not_exist == [1]
    assert [
        item.id for item in collection.search_by_vector("idx", [1.0, 0.0, 0.0, 0.0], limit=3).data
    ] == [2, 3]
    assert collection.search_by_id("idx", 1, limit=3).data == []
    assert collection.aggregate_data("idx").agg == {"_total": 2}


def test_one_ttl_row_is_recorded_for_each_item_in_a_batch(clock, collection):
    collection.upsert_data([row(1, "one"), row(2, "two")], ttl=5)

    entries = ttl_rows(collection)
    assert len(entries) == 2
    assert len({key for key, _ in entries}) == 2

    clock["ns"] += 6_000_000_000
    fetched = collection.fetch_data([1, 2])
    assert fetched.items == []
    assert fetched.ids_not_exist == [1, 2]


def test_stale_ttl_rows_do_not_override_later_upserts(clock, collection):
    collection.upsert_data([row(1, "temporary"), row(2, "first")], ttl=5)
    clock["ns"] += 2_000_000_000
    collection.upsert_data([row(1, "permanent")])
    collection.upsert_data([row(2, "extended")], ttl=10)

    clock["ns"] += 4_000_000_000
    fetched = collection.fetch_data([1, 2])
    assert [item.fields["text"] for item in fetched.items] == ["permanent", "extended"]
    assert inner(collection)._next_expire_ns == BASE_TIME_NS + 12_000_000_000

    clock["ns"] += 7_000_000_000
    fetched = collection.fetch_data([1, 2])
    assert [item.fields["text"] for item in fetched.items] == ["permanent"]
    assert fetched.ids_not_exist == [2]
    assert ttl_rows(collection) == []


def test_persistent_collection_restores_the_next_expiration(clock, tmp_path: Path):
    path = str(tmp_path / "ttl-db")
    collection = get_or_create_local_collection(
        meta_data=META,
        path=path,
        config={"ttl_cleanup_seconds": 0},
    )
    collection.upsert_data([row(1, "temporary")], ttl=5)
    collection.close()

    clock["ns"] += 6_000_000_000
    reopened = get_or_create_local_collection(
        path=path,
        config={"ttl_cleanup_seconds": 0},
    )
    try:
        fetched = reopened.fetch_data([1])
        assert fetched.items == []
        assert fetched.ids_not_exist == [1]
    finally:
        reopened.close()
