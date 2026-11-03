# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection
from openviking.storage.vectordb.store.store_manager import StoreManager

META = {
    "CollectionName": "delta_prune",
    "Fields": [
        {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
        {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
        {"FieldName": "text", "FieldType": "string"},
    ],
}


def _delta_count(col):
    store_mgr = col._Collection__collection.store_mgr
    return sum(1 for _ in store_mgr.storage.iter_all(StoreManager.DeltaTable))


def _upsert_round(col, round_no):
    col.upsert_data(
        [{"id": i, "vector": [0.1 * (round_no + 1)] * 4, "text": f"r{round_no}"} for i in range(5)]
    )


def test_persist_prunes_deltas_covered_by_snapshot(tmp_path):
    path = str(tmp_path / "db")
    col = get_or_create_local_collection(meta_data=META, path=path)
    col.create_index("idx", {"IndexName": "idx", "VectorIndex": {"IndexType": "flat"}})
    inner = col._Collection__collection

    for round_no in range(3):
        _upsert_round(col, round_no)
        assert _delta_count(col) == 5
        assert inner.flush_all_indexes() == 1
        assert _delta_count(col) == 0

    # Writes after the last snapshot keep their deltas for crash recovery.
    _upsert_round(col, 3)
    assert _delta_count(col) == 5
    col.close()

    col = get_or_create_local_collection(meta_data=META, path=path)
    try:
        assert _delta_count(col) == 0
        rows = col.fetch_data(list(range(5))).items
        assert sorted(row.fields["text"] for row in rows) == ["r3"] * 5
        assert len(col.search_by_vector("idx", [0.4] * 4, limit=10).data) == 5
    finally:
        col.close()
