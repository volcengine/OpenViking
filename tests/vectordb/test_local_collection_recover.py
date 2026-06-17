# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for local_collection._recover() self-healing.

Covers issue #2118: when the index directory is empty but the store still
holds candidate records, _recover() must rebuild a default index instead of
returning silently.
"""
import os
import shutil
import unittest

from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection
from openviking.storage.vectordb.engine import ENGINE_VARIANT

DB_PATH = "./test_data/test_local_collection_recover"


def _meta():
    return {
        "CollectionName": "recover_test_col",
        "Fields": [
            {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            {"FieldName": "data", "FieldType": "string"},
        ],
    }


def _wipe_index_dir(path: str) -> None:
    """Simulate the bug from #2118: store survives, index directory does not."""
    index_dir = os.path.join(path, "index")
    if os.path.isdir(index_dir):
        shutil.rmtree(index_dir)


@unittest.skipIf(
    ENGINE_VARIANT == "unavailable",
    "vectordb native engine not built in this environment",
)
class TestLocalCollectionRecover(unittest.TestCase):
    def setUp(self):
        if os.path.exists(DB_PATH):
            shutil.rmtree(DB_PATH)

    def tearDown(self):
        if os.path.exists(DB_PATH):
            shutil.rmtree(DB_PATH)

    def test_recover_rebuilds_index_when_store_has_records_and_index_dir_is_empty(self):
        col = get_or_create_local_collection(meta_data=_meta(), path=DB_PATH)
        col.create_index(
            "idx_main",
            {"IndexName": "idx_main", "VectorIndex": {"IndexType": "flat", "Distance": "l2"}},
        )
        records = [
            {"id": i, "vector": [0.1] * 4, "data": f"row_{i}"} for i in range(10)
        ]
        col.upsert_data(records)
        col.close()

        # Reproduce the bug: the store survives, the index directory does not.
        _wipe_index_dir(DB_PATH)

        col2 = get_or_create_local_collection(meta_data=_meta(), path=DB_PATH)
        try:
            self.assertEqual(
                len(col2.fetch_data(list(range(10))).items),
                10,
                "store records should still be intact after wiping only the index dir",
            )

            indexes = col2.list_indexes()
            self.assertTrue(
                indexes,
                "a default index must be auto-rebuilt when the store has records but no index",
            )

            search_res = col2.search_by_vector(indexes[0], dense_vector=[0.1] * 4, limit=10)
            self.assertGreater(
                len(search_res.data),
                0,
                "rebuilt default index must return results from the recovered store",
            )
        finally:
            col2.close()

    def test_recover_no_op_when_both_store_and_index_are_empty(self):
        col = get_or_create_local_collection(meta_data=_meta(), path=DB_PATH)
        col.close()
        _wipe_index_dir(DB_PATH)

        col2 = get_or_create_local_collection(meta_data=_meta(), path=DB_PATH)
        try:
            self.assertEqual(
                col2.list_indexes(),
                [],
                "no default index should be created when the store is empty",
            )
        finally:
            col2.close()

    def test_recover_does_not_double_rebuild_when_index_already_exists(self):
        col = get_or_create_local_collection(meta_data=_meta(), path=DB_PATH)
        col.create_index(
            "idx_main",
            {"IndexName": "idx_main", "VectorIndex": {"IndexType": "flat", "Distance": "l2"}},
        )
        col.upsert_data(
            [{"id": i, "vector": [0.1] * 4, "data": f"row_{i}"} for i in range(5)]
        )
        col.close()

        # Reopen without wiping anything: the healthy "idx_main" must come back
        # alone, and the rebuild path must NOT add a phantom "default" index.
        col2 = get_or_create_local_collection(meta_data=_meta(), path=DB_PATH)
        try:
            indexes = col2.list_indexes()
            self.assertIn("idx_main", indexes)
            self.assertNotIn(
                "default",
                indexes,
                "auto-rebuild must not run when a healthy index already recovered",
            )
        finally:
            col2.close()


if __name__ == "__main__":
    unittest.main()
