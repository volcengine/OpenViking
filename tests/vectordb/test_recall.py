# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import random
import shutil
import unittest
from typing import List

from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

# Test data path
TEST_DB_PATH = "./test_recall_collection/"


def calculate_l2_distance(v1: List[float], v2: List[float]) -> float:
    return sum((a - b) ** 2 for a, b in zip(v1, v2))


def calculate_ip_distance(v1: List[float], v2: List[float]) -> float:
    return sum(a * b for a, b in zip(v1, v2))


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

    def test_exact_match_recall(self):
        """Test if the exact vector is recalled at rank 1"""
        print("\n=== Test: Exact Match Recall ===")

        dim = 64
        meta_data = {
            "CollectionName": "test_exact_match",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": dim},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        # Generate data
        random.seed(42)
        total_records = 1000
        data = []
        vectors = []
        for i in range(total_records):
            vec = [random.uniform(-1, 1) for _ in range(dim)]
            vectors.append(vec)
            data.append({"id": i, "vector": vec})

        collection.upsert_data(data)

        # Create Index (Flat index should give 100% recall)
        collection.create_index(
            "idx_flat",
            {
                "IndexName": "idx_flat",
                "VectorIndex": {"IndexType": "flat", "Distance": "l2"},
            },
        )

        # Query with an existing vector
        target_idx = 500
        query_vec = vectors[target_idx]

        result = collection.search_by_vector("idx_flat", dense_vector=query_vec, limit=10)

        self.assertTrue(len(result.data) > 0)
        # The first result should be the vector itself (id=500)
        # Note: Depending on floating point precision, distance might not be exactly 0.0,
        # but it should be the closest.
        self.assertEqual(
            result.data[0].id, target_idx, "The top result should be the query vector itself"
        )
        print("✓ Exact match verified")

    def test_l2_recall_topk(self):
        """Test Top-K recall for L2 distance"""
        print("\n=== Test: Top-K Recall (L2) ===")

        dim = 32
        total_records = 500
        meta_data = {
            "CollectionName": "test_l2_recall",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": dim},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        # Generate random data
        random.seed(100)
        vectors = []
        data = []
        for i in range(total_records):
            vec = [random.uniform(0, 1) for _ in range(dim)]
            vectors.append(vec)
            data.append({"id": i, "vector": vec})

        collection.upsert_data(data)

        collection.create_index(
            "idx_l2",
            {
                "IndexName": "idx_l2",
                "VectorIndex": {"IndexType": "flat", "Distance": "l2"},
            },
        )

        # Generate a query vector
        query_vec = [random.uniform(0, 1) for _ in range(dim)]

        # Calculate Ground Truth
        # (distance, id)
        distances = []
        for i, vec in enumerate(vectors):
            dist = calculate_l2_distance(query_vec, vec)
            distances.append((dist, i))

        # Sort by distance ascending (L2)
        distances.sort(key=lambda x: x[0])
        ground_truth_ids = [x[1] for x in distances[:10]]

        # Search
        result = collection.search_by_vector("idx_l2", dense_vector=query_vec, limit=10)
        result_ids = [item.id for item in result.data]

        print(f"Ground Truth IDs: {ground_truth_ids}")
        print(f"Search Result IDs: {result_ids}")

        # Calculate Recall@10
        intersection = set(ground_truth_ids) & set(result_ids)
        recall = len(intersection) / 10.0
        print(f"Recall@10: {recall}")

        self.assertEqual(recall, 1.0, "Recall@10 for Flat index should be 1.0")

        # Verify order matches
        self.assertEqual(
            result_ids, ground_truth_ids, "Result order should match ground truth for Flat index"
        )
        print("✓ L2 Recall verified")

    def test_ip_recall_topk(self):
        """Test Top-K recall for Inner Product (IP) distance"""
        print("\n=== Test: Top-K Recall (IP) ===")

        dim = 32
        total_records = 500
        meta_data = {
            "CollectionName": "test_ip_recall",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": dim},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        # Generate random data
        random.seed(200)
        vectors = []
        data = []
        for i in range(total_records):
            # Normalize vectors for IP to behave like Cosine Similarity if needed,
            # but IP itself is just dot product.
            vec = [random.uniform(-1, 1) for _ in range(dim)]
            vectors.append(vec)
            data.append({"id": i, "vector": vec})

        collection.upsert_data(data)

        collection.create_index(
            "idx_ip",
            {
                "IndexName": "idx_ip",
                "VectorIndex": {"IndexType": "flat", "Distance": "ip"},
            },
        )

        # Generate a query vector
        query_vec = [random.uniform(-1, 1) for _ in range(dim)]

        # Calculate Ground Truth
        # (score, id)
        scores = []
        for i, vec in enumerate(vectors):
            score = calculate_ip_distance(query_vec, vec)
            scores.append((score, i))

        # Sort by score descending (IP)
        scores.sort(key=lambda x: x[0], reverse=True)
        ground_truth_ids = [x[1] for x in scores[:10]]

        # Search
        result = collection.search_by_vector("idx_ip", dense_vector=query_vec, limit=10)
        result_ids = [item.id for item in result.data]

        print(f"Ground Truth IDs: {ground_truth_ids}")
        print(f"Search Result IDs: {result_ids}")

        # Calculate Recall@10
        intersection = set(ground_truth_ids) & set(result_ids)
        recall = len(intersection) / 10.0
        print(f"Recall@10: {recall}")

        self.assertEqual(recall, 1.0, "Recall@10 for Flat index should be 1.0")
        self.assertEqual(
            result_ids, ground_truth_ids, "Result order should match ground truth for Flat index"
        )
        print("✓ IP Recall verified")

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

        # NOTE: FetchDataResult structure: result_num, labels, scores, extra_json?
        # Actually fetch_data returns a list of results.

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

    def test_persistence_crud(self):
        """Test CRUD operations persist after collection close and reopen"""
        print("\n=== Test: Persistence CRUD ===")
        dim = 1024
        name = "test_persistence"
        meta_data = {
            "CollectionName": name,
            "Description": "Persistence test",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": dim},
                {"FieldName": "name", "FieldType": "string"},
            ],
        }

        # 1. Open and Add Data
        collection = get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        self.register_collection(collection)

        data = [
            {"id": "1", "vector": [0.1] * dim, "name": "Item 1"},
            {"id": "2", "vector": [0.2] * dim, "name": "Item 2"},
        ]
        collection.upsert_data(data)

        # Verify Add
        res = collection.fetch_data(["1", "2"])
        self.assertEqual(len(res.items), 2)

        # 2. Close Collection (Simulate restart)
        # Note: LocalCollection might not have an explicit close() that unloads everything from memory
        # if it's purely object based, but we can delete the object and re-instantiate.
        # The important part is that data is on disk (RocksDB/LevelDB).
        collection.close()
        del collection

        # 3. Reopen
        collection_new = get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        self.register_collection(collection_new)

        # Verify Data Exists
        res_reopen = collection_new.fetch_data(["1", "2"])
        self.assertEqual(len(res_reopen.items), 2)
        # Order is not guaranteed, so check by ID or sort
        ids = sorted([item.id for item in res_reopen.items])
        self.assertEqual(ids, ["1", "2"])

        # 4. Update Data
        update_data = [{"id": "1", "vector": [0.9] * dim, "name": "Item 1 Updated"}]
        collection_new.upsert_data(update_data)

        res_update = collection_new.fetch_data(["1"])
        self.assertEqual(len(res_update.items), 1)
        self.assertEqual(res_update.items[0].fields["name"], "Item 1 Updated")

        # 5. Delete Data
        collection_new.delete_data(["2"])

        res_del = collection_new.fetch_data(["2"])
        self.assertEqual(len(res_del.items), 0, "Deleted item should not be found")
        self.assertEqual(len(res_del.ids_not_exist), 1)

        # 6. Search on persisted data
        collection_new.create_index(
            "idx_persist",
            {"IndexName": "idx_persist", "VectorIndex": {"IndexType": "flat", "Distance": "l2"}},
        )
        search_res = collection_new.search_by_vector(
            "idx_persist", dense_vector=[0.9] * dim, limit=1
        )
        self.assertEqual(len(search_res.data), 1)
        self.assertEqual(search_res.data[0].id, "1")

        print("✓ Persistence verified")


if __name__ == "__main__":
    unittest.main()
