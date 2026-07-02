# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for #1381.

A local vectordb collection can be persisted with store content but no usable
index (the ``index/`` directory emptied, or an index subdir whose
``index_meta.json`` was lost/corrupted). ``PersistCollection._recover()`` only
restores indexes that still carry valid on-disk metadata, so such dirty states
used to leave the collection with no searchable index: ``add_resource`` and
``reindex`` reported success, yet ``search``/``find`` silently returned 0.

``LocalCollectionAdapter`` now rebuilds the missing default index from the store
on load, restoring searchability.
"""

import os
import shutil
import tempfile
import unittest

from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter

COLLECTION_NAME = "context"
INDEX_NAME = "default"
SCHEMA = {
    "CollectionName": COLLECTION_NAME,
    "Fields": [
        {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
        {"FieldName": "uri", "FieldType": "string"},
        {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
    ],
}
VECTOR = [0.1, 0.2, 0.3, 0.4]


def _new_adapter(project_path: str) -> LocalCollectionAdapter:
    return LocalCollectionAdapter(
        collection_name=COLLECTION_NAME,
        project_path=project_path,
        index_name=INDEX_NAME,
    )


class TestLocalAdapterIndexRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ov1381-")
        self.project_path = os.path.join(self.tmp, "vectordb")
        self.collection_path = os.path.join(self.project_path, COLLECTION_NAME)
        self.index_dir = os.path.join(self.collection_path, "index")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_collection(self):
        adapter = _new_adapter(self.project_path)
        adapter.create_collection(
            COLLECTION_NAME,
            SCHEMA,
            distance="cosine",
            sparse_weight=0.0,
            index_name=INDEX_NAME,
        )
        adapter.upsert({"id": "a", "uri": "viking://resources/demo", "vector": VECTOR})
        # Sanity: data is searchable before we corrupt anything.
        results = adapter.query(query_vector=VECTOR, limit=10)
        self.assertEqual(len(results), 1)
        adapter.close()

    def _query_after_reload(self) -> int:
        adapter = _new_adapter(self.project_path)
        try:
            return len(adapter.query(query_vector=VECTOR, limit=10))
        finally:
            adapter.close()

    def test_recovers_when_index_dir_emptied(self):
        """index/ wiped entirely -> rebuilt from store on load."""
        self._seed_collection()
        for entry in os.listdir(self.index_dir):
            path = os.path.join(self.index_dir, entry)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        self.assertEqual(os.listdir(self.index_dir), [])

        self.assertEqual(self._query_after_reload(), 1)

    def test_recovers_when_index_meta_missing(self):
        """index/<name>/ survives but index_meta.json lost -> rebuilt from store."""
        self._seed_collection()
        meta_path = os.path.join(self.index_dir, INDEX_NAME, "index_meta.json")
        self.assertTrue(os.path.exists(meta_path))
        os.remove(meta_path)

        self.assertEqual(self._query_after_reload(), 1)

    def test_healthy_collection_still_loads(self):
        """A clean persisted collection keeps working across reloads."""
        self._seed_collection()
        self.assertEqual(self._query_after_reload(), 1)


if __name__ == "__main__":
    unittest.main()
