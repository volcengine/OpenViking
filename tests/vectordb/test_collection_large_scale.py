# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Large scale scenario tests - Collection stress tests and performance tests
Tests for large data volumes, high-dimensional vectors, complex queries, etc.
"""

import gc
import random
import shutil
import time
import unittest

from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

# Test data path
TEST_DB_PATH = "./test_large_scale_collection/"


class TestLargeScaleScenarios(unittest.TestCase):
    """Large scale scenario tests"""

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
        gc.collect()
        time.sleep(0.1)
        shutil.rmtree(TEST_DB_PATH, ignore_errors=True)

    def register_collection(self, collection):
        """Register collection for automatic cleanup"""
        self.collections.append(collection)
        return collection

    # ==================== Large data volume tests ====================

    def test_01_large_batch_insert_10k(self):
        """Test large batch insert - 10,000 records"""
        print("\n=== Test 1: Large Batch Insert (10K records) ===")

        meta_data = {
            "CollectionName": "test_10k",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 128},
                {"FieldName": "category", "FieldType": "string"},
                {"FieldName": "score", "FieldType": "float32"},
                {"FieldName": "timestamp", "FieldType": "int64"},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        # Prepare 10K data
        print("Preparing 10,000 records...")
        random.seed(42)
        batch_size = 1000
        total_records = 10000

        start_time = time.time()

        # Batch insert
        for batch_idx in range(total_records // batch_size):
            batch_data = []
            for i in range(batch_size):
                record_id = batch_idx * batch_size + i + 1
                batch_data.append(
                    {
                        "id": record_id,
                        "vector": [random.gauss(0, 1) for _ in range(128)],
                        "category": f"cat_{record_id % 20}",
                        "score": random.uniform(0, 100),
                        "timestamp": int(time.time()) + record_id,
                    }
                )

            result = collection.upsert_data(batch_data)
            self.assertEqual(len(result.ids), batch_size)
            print(
                f"  Batch {batch_idx + 1}/{total_records // batch_size} inserted ({len(result.ids)} records)"
            )

        insert_time = time.time() - start_time
        print(f"✓ Inserted {total_records} records in {insert_time:.2f}s")
        print(f"  Average: {total_records / insert_time:.0f} records/sec")

        # Create index
        print("Creating index...")
        index_start = time.time()
        collection.create_index(
            "idx_10k",
            {
                "IndexName": "idx_10k",
                "VectorIndex": {"IndexType": "flat", "Distance": "ip"},
                "ScalarIndex": ["category", "score", "timestamp"],
            },
        )
        index_time = time.time() - index_start
        print(f"✓ Index created in {index_time:.2f}s")

        # Test search performance
        print("Testing search performance...")
        query_vec = [random.gauss(0, 1) for _ in range(128)]
        search_times = []

        for _ in range(10):
            search_start = time.time()
            result = collection.search_by_vector("idx_10k", dense_vector=query_vec, limit=100)
            search_time = time.time() - search_start
            search_times.append(search_time)
            self.assertEqual(len(result.data), 100)

        avg_search_time = sum(search_times) / len(search_times)
        print(f"✓ Average search time: {avg_search_time * 1000:.2f}ms (10 queries, top-100)")

        # Test filtered search
        print("Testing filtered search...")
        filter_start = time.time()
        result = collection.search_by_vector(
            "idx_10k",
            dense_vector=query_vec,
            limit=50,
            filters={"op": "range", "field": "score", "gte": 50.0},
        )
        filter_time = time.time() - filter_start
        print(
            f"✓ Filtered search completed in {filter_time * 1000:.2f}ms (returned {len(result.data)} results)"
        )

    def test_02_large_batch_insert_50k(self):
        """Test extra large batch insert - 50,000 records"""
        print("\n=== Test 2: Large Batch Insert (50K records) ===")

        meta_data = {
            "CollectionName": "test_50k",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 256},
                {"FieldName": "group", "FieldType": "string"},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        print("Preparing 50,000 records...")
        random.seed(123)
        batch_size = 2000
        total_records = 50000

        start_time = time.time()

        for batch_idx in range(total_records // batch_size):
            batch_data = []
            for i in range(batch_size):
                record_id = batch_idx * batch_size + i + 1
                batch_data.append(
                    {
                        "id": record_id,
                        "vector": [random.uniform(-1, 1) for _ in range(256)],
                        "group": f"group_{record_id % 50}",
                    }
                )

            collection.upsert_data(batch_data)
            if (batch_idx + 1) % 5 == 0:
                print(f"  Inserted {(batch_idx + 1) * batch_size} / {total_records} records")

        insert_time = time.time() - start_time
        print(f"✓ Inserted {total_records} records in {insert_time:.2f}s")
        print(f"  Average: {total_records / insert_time:.0f} records/sec")

        # Create index
        print("Creating index...")
        index_start = time.time()
        collection.create_index(
            "idx_50k",
            {
                "IndexName": "idx_50k",
                "VectorIndex": {"IndexType": "flat", "Distance": "l2"},
                "ScalarIndex": ["group"],
            },
        )
        index_time = time.time() - index_start
        print(f"✓ Index created in {index_time:.2f}s")

        # Test aggregation performance
        print("Testing aggregation on 50K records...")
        agg_start = time.time()
        result = collection.aggregate_data("idx_50k", op="count", field="group")
        agg_time = time.time() - agg_start
        print(f"✓ Aggregation completed in {agg_time:.2f}s")
        print(f"  Found {len(result.agg)} unique groups")

    def test_03_high_dimensional_vectors(self):
        """Test high-dimensional vectors - 1024-dim, 10,000 records"""
        print("\n=== Test 3: High Dimensional Vectors (1024-dim, 10K records) ===")

        meta_data = {
            "CollectionName": "test_high_dim",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 1024},
                {"FieldName": "label", "FieldType": "string"},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        print("Preparing 10,000 records with 1024-dim vectors...")
        random.seed(456)
        batch_size = 500
        total_records = 10000

        start_time = time.time()

        for batch_idx in range(total_records // batch_size):
            batch_data = []
            for i in range(batch_size):
                record_id = batch_idx * batch_size + i + 1
                # Generate high-dimensional vector
                vector = [random.gauss(0, 1) for _ in range(1024)]
                batch_data.append(
                    {
                        "id": record_id,
                        "vector": vector,
                        "label": f"label_{record_id % 10}",
                    }
                )

            collection.upsert_data(batch_data)
            if (batch_idx + 1) % 4 == 0:
                print(f"  Inserted {(batch_idx + 1) * batch_size} / {total_records} records")

        insert_time = time.time() - start_time
        print(f"✓ Inserted {total_records} high-dim records in {insert_time:.2f}s")

        # Create index并测试搜索
        print("Creating index and testing search...")
        collection.create_index(
            "idx_high_dim",
            {
                "IndexName": "idx_high_dim",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )

        query_vec = [random.gauss(0, 1) for _ in range(1024)]
        search_start = time.time()
        result = collection.search_by_vector("idx_high_dim", dense_vector=query_vec, limit=50)
        search_time = time.time() - search_start

        self.assertEqual(len(result.data), 50)
        print(f"✓ High-dim search completed in {search_time * 1000:.2f}ms (top-50)")

    def test_04_massive_updates(self):
        """Test massive updates - 10,000 records batch update"""
        print("\n=== Test 4: Massive Updates (10K records) ===")

        meta_data = {
            "CollectionName": "test_massive_updates",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 64},
                {"FieldName": "version", "FieldType": "int64"},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        # Initial insert
        print("Initial insert of 10,000 records...")
        random.seed(789)
        total_records = 10000

        data_list = [
            {
                "id": i,
                "vector": [random.random() for _ in range(64)],
                "version": 1,
            }
            for i in range(1, total_records + 1)
        ]

        insert_start = time.time()
        batch_size = 1000
        for i in range(0, len(data_list), batch_size):
            collection.upsert_data(data_list[i : i + batch_size])
        insert_time = time.time() - insert_start
        print(f"✓ Initial insert completed in {insert_time:.2f}s")

        # Create index
        collection.create_index(
            "idx_update",
            {
                "IndexName": "idx_update",
                "VectorIndex": {"IndexType": "flat"},
                "ScalarIndex": ["version"],
            },
        )

        # Batch update
        print("Performing massive updates...")
        update_data = [
            {
                "id": i,
                "vector": [random.random() for _ in range(64)],
                "version": 2,
            }
            for i in range(1, total_records + 1)
        ]

        update_start = time.time()
        for i in range(0, len(update_data), batch_size):
            collection.upsert_data(update_data[i : i + batch_size])
            if (i + batch_size) % 2000 == 0:
                print(f"  Updated {i + batch_size} / {total_records} records")
        update_time = time.time() - update_start
        print(f"✓ Massive update completed in {update_time:.2f}s")
        print(f"  Average: {total_records / update_time:.0f} updates/sec")

        # Verify update
        fetch_result = collection.fetch_data([1, 100, 1000, 5000, 10000])
        for item in fetch_result.items:
            self.assertEqual(item.fields["version"], 2)
        print("✓ Update verification passed")

    def test_05_massive_deletes(self):
        """Test massive deletes - delete 25,000 from 50,000 records"""
        print("\n=== Test 5: Massive Deletes (25K from 50K records) ===")

        meta_data = {
            "CollectionName": "test_massive_deletes",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 32},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        # Insert 50K data
        print("Inserting 50,000 records...")
        random.seed(111)
        total_records = 50000
        batch_size = 2000

        for batch_idx in range(total_records // batch_size):
            batch_data = [
                {
                    "id": batch_idx * batch_size + i + 1,
                    "vector": [random.random() for _ in range(32)],
                }
                for i in range(batch_size)
            ]
            collection.upsert_data(batch_data)

        print(f"✓ Inserted {total_records} records")

        # Create index
        collection.create_index("idx", {"IndexName": "idx", "VectorIndex": {"IndexType": "flat"}})

        # Delete first half (25K)
        print("Deleting 25,000 records...")
        delete_ids = list(range(1, 25001))
        delete_batch_size = 1000

        delete_start = time.time()
        for i in range(0, len(delete_ids), delete_batch_size):
            batch_ids = delete_ids[i : i + delete_batch_size]
            collection.delete_data(batch_ids)
            if (i + delete_batch_size) % 5000 == 0:
                print(f"  Deleted {i + delete_batch_size} / 25000 records")

        delete_time = time.time() - delete_start
        print(f"✓ Deleted 25,000 records in {delete_time:.2f}s")
        print(f"  Average: {25000 / delete_time:.0f} deletes/sec")

        # Verify deletion - Method 1: fetch_data
        fetch_result = collection.fetch_data([1, 100, 25000, 25001, 50000])
        self.assertEqual(len(fetch_result.items), 2)  # Only 25001 and 50000 exist
        self.assertEqual({item.id for item in fetch_result.items}, {25001, 50000})
        print("✓ Delete verification passed (fetch_data)")

        # Verify deletion - Method 2: verify actual retrievable record count through search
        print("Verifying deletion through search...")
        search_result = collection.search_by_vector(
            "idx",
            dense_vector=[random.random() for _ in range(32)],
            limit=30000,  # Request more than remaining count
        )
        actual_count = len(search_result.data)
        print(f"  Search returned {actual_count} records (expected ~25000)")

        # Index may have delayed updates, so we allow some margin
        # But should at least be less than original count
        self.assertLess(
            actual_count, 30000, "Search should return less than 30000 records after deletion"
        )

        # Verify deleted records are not in search results
        search_ids = {item.id for item in search_result.data}
        deleted_samples = [1, 100, 1000, 10000, 25000]
        for deleted_id in deleted_samples:
            self.assertNotIn(
                deleted_id, search_ids, f"Deleted ID {deleted_id} should not be in search results"
            )
        print(f"✓ Deletion verified through search: {actual_count} records remain")

        # Verify deletion - Method 3: aggregate_data to count remaining data
        print("Verifying deletion through aggregation...")
        agg_result = collection.aggregate_data("idx", op="count")
        agg_count = agg_result.agg.get("_total", 0)
        print(f"  Aggregate count: {agg_count}")
        self.assertEqual(
            agg_count,
            25000,
            f"Expected 25000 records after deletion, but aggregate_data returned {agg_count}",
        )
        print("✓ Aggregate count verification passed")

    def test_06_complex_multi_filter_large_scale(self):
        """Test large scale complex filter queries"""
        print("\n=== Test 6: Complex Multi-Filter on Large Scale ===")

        meta_data = {
            "CollectionName": "test_complex_filter",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 128},
                {"FieldName": "category", "FieldType": "string"},
                {"FieldName": "priority", "FieldType": "int64"},
                {"FieldName": "score", "FieldType": "float32"},
                {"FieldName": "status", "FieldType": "string"},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        # Insert 20K data
        print("Inserting 20,000 records with multiple fields...")
        random.seed(222)
        total_records = 20000
        batch_size = 1000

        categories = ["A", "B", "C", "D", "E"]
        statuses = ["active", "inactive", "pending"]

        for batch_idx in range(total_records // batch_size):
            batch_data = []
            for i in range(batch_size):
                record_id = batch_idx * batch_size + i + 1
                batch_data.append(
                    {
                        "id": record_id,
                        "vector": [random.gauss(0, 1) for _ in range(128)],
                        "category": random.choice(categories),
                        "priority": random.randint(1, 10),
                        "score": random.uniform(0, 100),
                        "status": random.choice(statuses),
                    }
                )
            collection.upsert_data(batch_data)

        print(f"✓ Inserted {total_records} records")

        # Create index
        collection.create_index(
            "idx_complex",
            {
                "IndexName": "idx_complex",
                "VectorIndex": {"IndexType": "flat", "Distance": "ip"},
                "ScalarIndex": ["category", "priority", "score", "status"],
            },
        )

        # Test complex filter queries
        print("Testing complex multi-filter queries...")

        # Query 1: category IN ["A", "B"] AND priority >= 7 AND score > 50 AND status="active"
        filter1 = {
            "op": "and",
            "conds": [
                {"op": "must", "field": "category", "conds": ["A", "B"]},
                {"op": "range", "field": "priority", "gte": 7},
                {"op": "range", "field": "score", "gt": 50.0},
                {"op": "must", "field": "status", "conds": ["active"]},
            ],
        }

        query_vec = [random.gauss(0, 1) for _ in range(128)]
        search_start = time.time()
        result1 = collection.search_by_vector(
            "idx_complex", dense_vector=query_vec, limit=100, filters=filter1
        )
        search_time1 = time.time() - search_start
        print(
            f"  Query 1 (4 conditions): {len(result1.data)} results in {search_time1 * 1000:.2f}ms"
        )

        # Query 2: (category="C" OR category="D") AND priority IN [3,5,7] AND status != "inactive"
        filter2 = {
            "op": "and",
            "conds": [
                {"op": "must", "field": "category", "conds": ["C", "D"]},
                {"op": "must", "field": "priority", "conds": [3, 5, 7]},
                {"op": "must_not", "field": "status", "conds": ["inactive"]},
            ],
        }

        search_start = time.time()
        result2 = collection.search_by_vector(
            "idx_complex", dense_vector=query_vec, limit=100, filters=filter2
        )
        search_time2 = time.time() - search_start
        print(
            f"  Query 2 (3 conditions): {len(result2.data)} results in {search_time2 * 1000:.2f}ms"
        )

        # Query 3: Range query + sort
        search_start = time.time()
        result3 = collection.search_by_scalar(
            "idx_complex",
            field="score",
            order="desc",
            limit=500,
            filters={"op": "range", "field": "priority", "gte": 5, "lte": 8},
        )
        search_time3 = time.time() - search_start
        print(
            f"  Query 3 (scalar sort): {len(result3.data)} results in {search_time3 * 1000:.2f}ms"
        )

        print("✓ Complex multi-filter queries completed")

    def test_07_large_scale_aggregation(self):
        """Test large scale aggregation statistics"""
        print("\n=== Test 7: Large Scale Aggregation ===")

        meta_data = {
            "CollectionName": "test_large_agg",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 64},
                {"FieldName": "country", "FieldType": "string"},
                {"FieldName": "city", "FieldType": "string"},
                {"FieldName": "product", "FieldType": "string"},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        # Insert 30K data, simulating many groups
        print("Inserting 30,000 records with high cardinality...")
        random.seed(333)
        total_records = 30000
        batch_size = 1000

        countries = [f"country_{i}" for i in range(50)]
        cities = [f"city_{i}" for i in range(200)]
        products = [f"product_{i}" for i in range(100)]

        for batch_idx in range(total_records // batch_size):
            batch_data = []
            for i in range(batch_size):
                record_id = batch_idx * batch_size + i + 1
                batch_data.append(
                    {
                        "id": record_id,
                        "vector": [random.random() for _ in range(64)],
                        "country": random.choice(countries),
                        "city": random.choice(cities),
                        "product": random.choice(products),
                    }
                )
            collection.upsert_data(batch_data)

        print(f"✓ Inserted {total_records} records")

        # Create index
        collection.create_index(
            "idx_agg",
            {
                "IndexName": "idx_agg",
                "VectorIndex": {"IndexType": "flat"},
                "ScalarIndex": ["country", "city", "product"],
            },
        )

        # Test aggregations with different cardinalities
        print("Testing aggregations with different cardinalities...")

        # Low cardinality aggregation (50 groups)
        agg_start = time.time()
        result1 = collection.aggregate_data("idx_agg", op="count", field="country")
        agg_time1 = time.time() - agg_start
        print(
            f"  Country aggregation (50 groups): {len(result1.agg)} groups in {agg_time1 * 1000:.2f}ms"
        )

        # Medium cardinality aggregation (100 groups)
        agg_start = time.time()
        result2 = collection.aggregate_data("idx_agg", op="count", field="product")
        agg_time2 = time.time() - agg_start
        print(
            f"  Product aggregation (100 groups): {len(result2.agg)} groups in {agg_time2 * 1000:.2f}ms"
        )

        # High cardinality aggregation (200 groups)
        agg_start = time.time()
        result3 = collection.aggregate_data("idx_agg", op="count", field="city")
        agg_time3 = time.time() - agg_start
        print(
            f"  City aggregation (200 groups): {len(result3.agg)} groups in {agg_time3 * 1000:.2f}ms"
        )

        # Filtered aggregation
        agg_start = time.time()
        result4 = collection.aggregate_data(
            "idx_agg",
            op="count",
            field="product",
            filters={
                "op": "must",
                "field": "country",
                "conds": ["country_0", "country_1", "country_2"],
            },
        )
        agg_time4 = time.time() - agg_start
        print(f"  Filtered aggregation: {len(result4.agg)} groups in {agg_time4 * 1000:.2f}ms")

        print("✓ Large scale aggregation tests completed")

    def test_08_persistence_with_large_data(self):
        """Test persistence and recovery with large data"""
        print("\n=== Test 8: Persistence with Large Data (20K records) ===")

        meta_data = {
            "CollectionName": "test_large_persist",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 256},
                {"FieldName": "metadata", "FieldType": "string"},
            ],
        }

        # Phase 1: Write data
        print("Phase 1: Writing 20,000 records...")
        collection1 = get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)

        random.seed(444)
        total_records = 20000
        batch_size = 1000

        write_start = time.time()
        for batch_idx in range(total_records // batch_size):
            batch_data = []
            for i in range(batch_size):
                record_id = batch_idx * batch_size + i + 1
                batch_data.append(
                    {
                        "id": record_id,
                        "vector": [random.uniform(-1, 1) for _ in range(256)],
                        "metadata": f"metadata_{record_id}",
                    }
                )
            collection1.upsert_data(batch_data)

        write_time = time.time() - write_start
        print(f"✓ Data written in {write_time:.2f}s")

        # Create index
        print("Creating index...")
        collection1.create_index(
            "idx_persist",
            {
                "IndexName": "idx_persist",
                "VectorIndex": {"IndexType": "flat", "Distance": "l2"},
                "ScalarIndex": ["metadata"],
            },
        )

        # Execute search and record results
        query_vec = [random.uniform(-1, 1) for _ in range(256)]
        result_before = collection1.search_by_vector(
            "idx_persist", dense_vector=query_vec, limit=50
        )
        ids_before = [item.id for item in result_before.data]

        # Close
        print("Closing collection...")
        close_start = time.time()
        collection1.close()
        close_time = time.time() - close_start
        print(f"✓ Collection closed in {close_time:.2f}s")

        # Phase 2: Reload
        print("\nPhase 2: Reloading from disk...")
        reload_start = time.time()
        collection2 = self.register_collection(get_or_create_local_collection(path=TEST_DB_PATH))
        reload_time = time.time() - reload_start
        print(f"✓ Collection reloaded in {reload_time:.2f}s")

        # Verify data integrity
        print("Verifying data integrity...")
        verify_ids = [1, 100, 1000, 5000, 10000, 15000, 20000]
        fetch_result = collection2.fetch_data(verify_ids)
        self.assertEqual(len(fetch_result.items), len(verify_ids))
        print(f"✓ Data verification passed ({len(verify_ids)} samples)")

        # Verify index and search results
        print("Verifying index and search results...")
        result_after = collection2.search_by_vector("idx_persist", dense_vector=query_vec, limit=50)
        ids_after = [item.id for item in result_after.data]
        self.assertEqual(ids_before, ids_after)
        print("✓ Search results consistent after reload")

        # Verify aggregation
        agg_result = collection2.aggregate_data("idx_persist", op="count")
        self.assertEqual(agg_result.agg["_total"], total_records)
        print(f"✓ Total count verified: {agg_result.agg['_total']}")

    def test_09_concurrent_operations_simulation(self):
        """Simulate concurrent operations scenario (serial simulation)"""
        print("\n=== Test 9: Concurrent Operations Simulation ===")

        meta_data = {
            "CollectionName": "test_concurrent",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 64},
                {"FieldName": "category", "FieldType": "string"},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        # Initial data
        print("Initial data insert...")
        random.seed(555)
        initial_data = [
            {
                "id": i,
                "vector": [random.random() for _ in range(64)],
                "category": f"cat_{i % 10}",
            }
            for i in range(1, 5001)
        ]
        collection.upsert_data(initial_data)

        collection.create_index(
            "idx_concurrent",
            {
                "IndexName": "idx_concurrent",
                "VectorIndex": {"IndexType": "flat"},
                "ScalarIndex": ["category"],
            },
        )
        print("✓ Initial setup completed (5000 records)")

        # Simulate mixed operations: insert, update, delete, search
        print("Simulating mixed operations...")
        operations = []

        # 100 searches
        for _ in range(100):
            query_vec = [random.random() for _ in range(64)]
            operations.append(("search", query_vec))

        # 50 inserts
        for i in range(50):
            new_id = 5001 + i
            operations.append(
                (
                    "insert",
                    {
                        "id": new_id,
                        "vector": [random.random() for _ in range(64)],
                        "category": f"cat_{new_id % 10}",
                    },
                )
            )

        # 30 updates
        for _ in range(30):
            update_id = random.randint(1, 5000)
            operations.append(
                (
                    "update",
                    {
                        "id": update_id,
                        "vector": [random.random() for _ in range(64)],
                        "category": "cat_updated",
                    },
                )
            )

        # 20 deletes
        for _ in range(20):
            delete_id = random.randint(1, 1000)
            operations.append(("delete", delete_id))

        # Execute mixed operations
        random.shuffle(operations)

        start_time = time.time()
        search_count = 0
        insert_count = 0
        update_count = 0
        delete_count = 0

        for op_type, op_data in operations:
            if op_type == "search":
                collection.search_by_vector("idx_concurrent", dense_vector=op_data, limit=10)
                search_count += 1
            elif op_type == "insert":
                collection.upsert_data([op_data])
                insert_count += 1
            elif op_type == "update":
                collection.upsert_data([op_data])
                update_count += 1
            elif op_type == "delete":
                collection.delete_data([op_data])
                delete_count += 1

        total_time = time.time() - start_time

        print(f"✓ Completed {len(operations)} mixed operations in {total_time:.2f}s")
        print(f"  - Searches: {search_count}")
        print(f"  - Inserts: {insert_count}")
        print(f"  - Updates: {update_count}")
        print(f"  - Deletes: {delete_count}")
        print(f"  Average: {len(operations) / total_time:.0f} ops/sec")

    def test_10_stress_test_continuous_operations(self):
        """Stress test: continuous mixed operations"""
        print("\n=== Test 10: Stress Test - Continuous Operations ===")

        meta_data = {
            "CollectionName": "test_stress",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 128},
            ],
        }

        collection = self.register_collection(
            get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        )

        # Initial data
        print("Initial data insert (10,000 records)...")
        random.seed(666)
        batch_size = 1000
        for batch_idx in range(10):
            batch_data = [
                {
                    "id": batch_idx * batch_size + i + 1,
                    "vector": [random.gauss(0, 1) for _ in range(128)],
                }
                for i in range(batch_size)
            ]
            collection.upsert_data(batch_data)

        collection.create_index(
            "idx_stress", {"IndexName": "idx_stress", "VectorIndex": {"IndexType": "flat"}}
        )
        print("✓ Initial setup completed")

        # Stress test: run for 5 seconds
        print("Running stress test for 5 seconds...")
        test_duration = 5  # seconds
        start_time = time.time()
        operation_count = 0
        search_times = []

        while time.time() - start_time < test_duration:
            # Randomly select operation type
            op = random.choice(
                ["search", "search", "search", "insert", "update"]
            )  # Higher weight for search

            if op == "search":
                query_vec = [random.gauss(0, 1) for _ in range(128)]
                search_start = time.time()
                collection.search_by_vector("idx_stress", dense_vector=query_vec, limit=20)
                search_time = time.time() - search_start
                search_times.append(search_time)
            elif op == "insert":
                new_id = random.randint(10001, 20000)
                collection.upsert_data(
                    [
                        {
                            "id": new_id,
                            "vector": [random.gauss(0, 1) for _ in range(128)],
                        }
                    ]
                )
            elif op == "update":
                update_id = random.randint(1, 10000)
                collection.upsert_data(
                    [
                        {
                            "id": update_id,
                            "vector": [random.gauss(0, 1) for _ in range(128)],
                        }
                    ]
                )

            operation_count += 1

        elapsed_time = time.time() - start_time

        print(f"✓ Completed {operation_count} operations in {elapsed_time:.2f}s")
        print(f"  Throughput: {operation_count / elapsed_time:.0f} ops/sec")

        if search_times:
            avg_search = sum(search_times) / len(search_times)
            min_search = min(search_times)
            max_search = max(search_times)
            print(
                f"  Search latency: avg={avg_search * 1000:.2f}ms, min={min_search * 1000:.2f}ms, max={max_search * 1000:.2f}ms"
            )


def run_large_scale_tests():
    """Run large scale tests"""
    suite = unittest.TestLoader().loadTestsFromTestCase(TestLargeScaleScenarios)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    print("Large Scale Test Summary:")
    print(f"Total tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("=" * 70)

    return result.wasSuccessful()


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("OpenViking Collection - Large Scale Scenario Tests")
    print("=" * 70)

    success = run_large_scale_tests()
    exit(0 if success else 1)
