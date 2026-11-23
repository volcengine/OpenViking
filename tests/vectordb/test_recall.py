# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import shutil
import unittest

import pytest

from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

# Test data path
TEST_DB_PATH = "./test_data/test_recall_collection/"


@pytest.mark.parametrize(
    ("metric", "expected_ids"),
    [
        pytest.param("l2", ["exact", "near", "negative"], id="euclidean-exact-match-first"),
        pytest.param("ip", ["large", "near", "exact"], id="inner-product-prefers-larger-norm"),
    ],
)
def test_dense_search_uses_configured_metric_for_top_k(tmp_path, metric, expected_ids):
    schema = {
        "CollectionName": "dense_ranking",
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
        ],
    }
    collection = get_or_create_local_collection(meta_data=schema, path=str(tmp_path))
    try:
        collection.upsert_data(
            [
                {"id": "large", "vector": [4.0, 0.0, 0.0, 0.0]},
                {"id": "near", "vector": [2.0, 2.0, 0.0, 0.0]},
                {"id": "exact", "vector": [1.0, 0.0, 0.0, 0.0]},
                {"id": "negative", "vector": [-1.0, -2.0, 0.0, 0.0]},
                {"id": "orthogonal", "vector": [0.0, 3.0, 0.0, 0.0]},
            ]
        )
        collection.create_index(
            "default",
            {"IndexName": "default", "VectorIndex": {"IndexType": "flat", "Distance": metric}},
        )

        result = collection.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=3)
        assert [item.id for item in result.data] == expected_ids
    finally:
        collection.close()


class TestRecall(unittest.TestCase):
    """Test vector recall quality"""

    def setUp(self):
        """Clean environment before each test"""
        shutil.rmtree(TEST_DB_PATH, ignore_errors=True)
        self.collections = []

    def tearDown(self):
        """Clean resources after each test"""
        for collection in self.collections:
            try:
                collection.drop()
            except Exception:
                pass
        self.collections.clear()
        shutil.rmtree(TEST_DB_PATH, ignore_errors=True)

    def register_collection(self, collection):
        self.collections.append(collection)
        return collection

    def test_search_limit_zero(self):
        """Test search with limit=0 returns empty result without error"""
        print("\n=== Test: Search limit=0 ===")

        dim = 8
        meta_data = {
            "CollectionName": "test_limit_zero",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": dim},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        data = [{"id": 0, "vector": [0.1] * dim}, {"id": 1, "vector": [0.2] * dim}]
        collection.upsert_data(data)

        collection.create_index(
            "idx_limit_zero",
            {
                "IndexName": "idx_limit_zero",
                "VectorIndex": {"IndexType": "flat", "Distance": "l2"},
            },
        )

        result = collection.search_by_vector("idx_limit_zero", dense_vector=[0.1] * dim, limit=0)

        self.assertEqual(len(result.data), 0, "limit=0 should return empty results")
        print("✓ limit=0 returns empty results")

    def test_sparse_vector_recall(self):
        """Test sparse vector recall in hybrid index"""
        print("\n=== Test: Sparse Vector Recall ===")

        dim = 4
        meta_data = {
            "CollectionName": "test_sparse_recall",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": dim},
                {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        dense_vec = [0.1] * dim
        data = [
            {"id": 0, "vector": dense_vec, "sparse_vector": {"t1": 1.0}},
            {"id": 1, "vector": dense_vec, "sparse_vector": {"t1": 0.5}},
            {"id": 2, "vector": dense_vec, "sparse_vector": {"t2": 1.0}},
        ]
        collection.upsert_data(data)

        collection.create_index(
            "idx_sparse",
            {
                "IndexName": "idx_sparse",
                "VectorIndex": {
                    "IndexType": "flat_hybrid",
                    "Distance": "ip",
                    "SearchWithSparseLogitAlpha": 1.0,
                },
            },
        )

        result = collection.search_by_vector(
            "idx_sparse",
            dense_vector=dense_vec,
            sparse_vector={"t1": 1.0},
            limit=3,
        )
        result_ids = [item.id for item in result.data]

        self.assertEqual(result_ids, [0, 1, 2], "Sparse ranking should match dot product order")
        print("✓ Sparse vector recall verified", result)

    def test_sparse_vector_recall_l2(self):
        """Test sparse vector recall with L2 distance in hybrid index"""
        print("\n=== Test: Sparse Vector Recall (L2) ===")

        dim = 4
        meta_data = {
            "CollectionName": "test_sparse_recall_l2",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": dim},
                {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        dense_vec = [0.1] * dim
        data = [
            {"id": 0, "vector": dense_vec, "sparse_vector": {"t1": 1.0}},
            {"id": 1, "vector": dense_vec, "sparse_vector": {"t1": 0.5}},
            {"id": 2, "vector": dense_vec, "sparse_vector": {"t2": 1.0}},
        ]
        collection.upsert_data(data)

        collection.create_index(
            "idx_sparse_l2",
            {
                "IndexName": "idx_sparse_l2",
                "VectorIndex": {
                    "IndexType": "flat_hybrid",
                    "Distance": "l2",
                    "SearchWithSparseLogitAlpha": 1.0,
                },
            },
        )

        result = collection.search_by_vector(
            "idx_sparse_l2",
            dense_vector=dense_vec,
            sparse_vector={"t1": 1.0},
            limit=3,
        )
        result_ids = [item.id for item in result.data]

        self.assertEqual(result_ids, [0, 1, 2], "Sparse L2 ranking should favor closest match")
        print("✓ Sparse vector recall (L2) verified", result)

    def test_hybrid_dense_sparse_mix(self):
        """Test hybrid scoring combines dense and sparse signals"""
        print("\n=== Test: Hybrid Dense+Sparse Mix ===")

        dim = 4
        meta_data = {
            "CollectionName": "test_hybrid_mix",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": dim},
                {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        data = [
            {"id": 0, "vector": [0.9, 0.0, 0.0, 0.0], "sparse_vector": {"t1": 0.1}},
            {"id": 1, "vector": [0.2, 0.0, 0.0, 0.0], "sparse_vector": {"t1": 1.0}},
            {"id": 2, "vector": [0.1, 0.0, 0.0, 0.0], "sparse_vector": {"t1": 0.8}},
        ]
        collection.upsert_data(data)

        collection.create_index(
            "idx_hybrid_mix",
            {
                "IndexName": "idx_hybrid_mix",
                "VectorIndex": {
                    "IndexType": "flat_hybrid",
                    "Distance": "ip",
                    "SearchWithSparseLogitAlpha": 0.5,
                },
            },
        )

        result = collection.search_by_vector(
            "idx_hybrid_mix",
            dense_vector=[1.0, 0.0, 0.0, 0.0],
            sparse_vector={"t1": 1.0},
            limit=3,
        )
        result_ids = [item.id for item in result.data]

        self.assertEqual(
            result_ids,
            [1, 0, 2],
            "Hybrid ranking should reflect combined dense and sparse scores",
        )
        print("✓ Hybrid dense+sparse mix verified")

    def test_complex_schema_missing_fields(self):
        """Test adding data with missing optional fields using complex schema"""
        print("\n=== Test: Complex Schema Missing Fields ===")
        dim = 1024
        name = "test_complex_missing_fields"
        meta_data = {
            "CollectionName": name,
            "Description": "Unified context collection",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {
                    "FieldName": "uri",
                    "FieldType": "string",
                },  # Changed path to string for simplicity as 'path' might not be standard FieldType
                {"FieldName": "type", "FieldType": "string"},
                {"FieldName": "context_type", "FieldType": "string"},
                {"FieldName": "vector", "FieldType": "vector", "Dim": dim},
                {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
                {
                    "FieldName": "created_at",
                    "FieldType": "string",
                },  # Simulating date_time as string
                {"FieldName": "updated_at", "FieldType": "string"},
                {"FieldName": "active_count", "FieldType": "int64"},
                {"FieldName": "parent_uri", "FieldType": "string"},
                {"FieldName": "is_leaf", "FieldType": "bool"},
                {"FieldName": "name", "FieldType": "string"},
                {"FieldName": "description", "FieldType": "string"},
                {"FieldName": "tags", "FieldType": "string"},
                {"FieldName": "abstract", "FieldType": "string"},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        # 1. Full record
        full_record = {
            "id": "1",
            "uri": "/path/to/1",
            "type": "doc",
            "context_type": "text",
            "vector": [0.1] * dim,
            "sparse_vector": {"t1": 1.0},
            "created_at": "2023-01-01",
            "updated_at": "2023-01-02",
            "active_count": 10,
            "parent_uri": "/path/to/0",
            "is_leaf": True,
            "name": "Doc 1",
            "description": "A description",
            "tags": "tag1,tag2",
            "abstract": "An abstract",
        }

        # 2. Minimal record (Only ID and Vector are strictly required by engine for indexing usually, but let's see schema validation)
        # Assuming only PK and Vector are strictly mandatory for vector search index, others should be optional/default.
        minimal_record = {
            "id": "2",
            "vector": [0.2] * dim,
        }

        # 3. Partial record
        partial_record = {
            "id": "3",
            "vector": [0.3] * dim,
            "name": "Doc 3",
            "active_count": 5,
        }

        collection.upsert_data([full_record, minimal_record, partial_record])

        # Verify data via Fetch
        res_full = collection.fetch_data(["1"])
        self.assertEqual(len(res_full.items), 1)
        self.assertEqual(res_full.items[0].id, "1")
        # Check fields exist in extra_json or attributes depending on implementation
        # The result object structure depends on how LocalCollection returns data.
        # Typically it returns an object where fields are accessible or in 'fields' dict.
        # Let's assume standard behavior where defined fields are attributes or in a dictionary.
        # For LocalCollection, non-vector fields are often serialized into a 'fields' JSON string or accessible directly if mapped.
        # We need to check if the data came back.

        print(f"Full Record Fetch: {res_full.items[0]}")

        res_min = collection.fetch_data(["2"])
        self.assertEqual(len(res_min.items), 1)
        self.assertEqual(res_min.items[0].id, "2")
        print(f"Minimal Record Fetch: {res_min.items[0]}")

        res_part = collection.fetch_data(["3"])
        self.assertEqual(len(res_part.items), 1)
        self.assertEqual(res_part.items[0].id, "3")
        print(f"Partial Record Fetch: {res_part.items[0]}")

        print("✓ Missing fields handled correctly")

    def test_first_index_build_reads_records_written_before_reopen(self):
        schema = {
            "CollectionName": "index_after_reopen",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        }
        collection = get_or_create_local_collection(meta_data=schema, path=TEST_DB_PATH)
        try:
            collection.upsert_data(
                [
                    {"id": "first", "vector": [1.0, 0.0, 0.0, 0.0]},
                    {"id": "second", "vector": [0.0, 0.0, 0.0, 0.0]},
                ]
            )
        finally:
            collection.close()

        reopened = self.register_collection(get_or_create_local_collection(path=TEST_DB_PATH))
        reopened.create_index(
            "default", {"IndexName": "default", "VectorIndex": {"IndexType": "flat", "Distance": "l2"}}
        )
        found = reopened.search_by_vector("default", dense_vector=[0.0, 0.0, 0.0, 0.0], limit=2)
        self.assertEqual([item.id for item in found.data], ["second", "first"])


if __name__ == "__main__":
    unittest.main()
