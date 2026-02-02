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


if __name__ == "__main__":
    unittest.main()
