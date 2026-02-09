# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import gc
import random
import shutil
import time
import unittest

from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

db_path_basic = "./db_test_filters_basic/"
db_path_complex = "./db_test_filters_complex/"
db_path_lifecycle = "./db_test_filters_lifecycle/"
db_path_scale = "./db_test_filters_scale/"


def clean_dir(path):
    shutil.rmtree(path, ignore_errors=True)


class TestFilterOpsBasic(unittest.TestCase):
    """Basic Filter operator tests"""

    def setUp(self):
        clean_dir(db_path_basic)
        self.path = db_path_basic
        self.collection = self._create_collection()
        self._insert_data()
        self._create_index()

    def tearDown(self):
        if self.collection:
            self.collection.drop()
        clean_dir(self.path)

    def _create_collection(self):
        collection_meta = {
            "CollectionName": "test_filters_basic",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "embedding", "FieldType": "vector", "Dim": 4},
                {"FieldName": "val_int", "FieldType": "int64"},
                {"FieldName": "val_float", "FieldType": "float32"},
                {"FieldName": "val_str", "FieldType": "string"},
            ],
        }
        return get_or_create_local_collection(meta_data=collection_meta, path=self.path)

    def _insert_data(self):
        data = [
            {
                "id": 1,
                "embedding": [1.0, 0, 0, 0],
                "val_int": 10,
                "val_float": 1.1,
                "val_str": "apple",
            },
            {
                "id": 2,
                "embedding": [1.0, 0, 0, 0],
                "val_int": 20,
                "val_float": 2.2,
                "val_str": "banana",
            },
            {
                "id": 3,
                "embedding": [1.0, 0, 0, 0],
                "val_int": 30,
                "val_float": 3.3,
                "val_str": "cherry",
            },
            {
                "id": 4,
                "embedding": [1.0, 0, 0, 0],
                "val_int": 40,
                "val_float": 4.4,
                "val_str": "date",
            },
            {
                "id": 5,
                "embedding": [1.0, 0, 0, 0],
                "val_int": 50,
                "val_float": 5.5,
                "val_str": "elderberry",
            },
        ]
        self.collection.upsert_data(data)

    def _create_index(self):
        index_meta = {
            "IndexName": "idx_basic",
            "VectorIndex": {"IndexType": "flat"},
            "ScalarIndex": ["id", "val_int", "val_float", "val_str"],
        }
        self.collection.create_index("idx_basic", index_meta)

    def _search(self, filters):
        res = self.collection.search_by_vector(
            "idx_basic", dense_vector=[1.0, 0, 0, 0], limit=100, filters=filters
        )
        return sorted([item.id for item in res.data])

    def test_basic_ops(self):
        # Must
        self.assertEqual(self._search({"op": "must", "field": "val_int", "conds": [20]}), [2])
        # Range
        self.assertEqual(self._search({"op": "range", "field": "val_int", "gt": 30}), [4, 5])
        # Prefix
        self.assertEqual(self._search({"op": "prefix", "field": "val_str", "prefix": "ap"}), [1])
        # Contains
        self.assertEqual(
            self._search({"op": "contains", "field": "val_str", "substring": "er"}), [3, 5]
        )  # chERry, eldERbERry


class TestFilterOpsComplex(unittest.TestCase):
    """Complex mixed logic tests"""

    def setUp(self):
        clean_dir(db_path_complex)
        self.path = db_path_complex
        self.collection = self._create_collection()
        self._insert_data()
        self._create_index()

    def tearDown(self):
        if self.collection:
            self.collection.drop()
        clean_dir(self.path)

    def _create_collection(self):
        collection_meta = {
            "CollectionName": "test_filters_complex",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "embedding", "FieldType": "vector", "Dim": 4},
                {"FieldName": "category", "FieldType": "string"},
                {"FieldName": "tags", "FieldType": "string"},
                {"FieldName": "price", "FieldType": "int64"},
                {
                    "FieldName": "rating_int",
                    "FieldType": "int64",
                },  # Use int64 instead of float32 to avoid potential bug
            ],
        }
        return get_or_create_local_collection(meta_data=collection_meta, path=self.path)

    def _insert_data(self):
        # Build some slightly complex data scenarios
        # id 1-10
        data = [
            {
                "id": 1,
                "embedding": [1.0, 0, 0, 0],
                "category": "electronics",
                "tags": "mobile,apple,new",
                "price": 8000,
                "rating_int": 48,
            },
            {
                "id": 2,
                "embedding": [1.0, 0, 0, 0],
                "category": "electronics",
                "tags": "mobile,android,sale",
                "price": 3000,
                "rating_int": 45,
            },
            {
                "id": 3,
                "embedding": [1.0, 0, 0, 0],
                "category": "electronics",
                "tags": "laptop,apple,pro",
                "price": 15000,
                "rating_int": 49,
            },
            {
                "id": 4,
                "embedding": [1.0, 0, 0, 0],
                "category": "electronics",
                "tags": "laptop,windows,budget",
                "price": 4000,
                "rating_int": 40,
            },
            {
                "id": 5,
                "embedding": [1.0, 0, 0, 0],
                "category": "home",
                "tags": "furniture,sofa",
                "price": 2000,
                "rating_int": 42,
            },
            {
                "id": 6,
                "embedding": [1.0, 0, 0, 0],
                "category": "home",
                "tags": "kitchen,blender",
                "price": 300,
                "rating_int": 38,
            },
            {
                "id": 7,
                "embedding": [1.0, 0, 0, 0],
                "category": "books",
                "tags": "fiction,sci-fi",
                "price": 50,
                "rating_int": 47,
            },
            {
                "id": 8,
                "embedding": [1.0, 0, 0, 0],
                "category": "books",
                "tags": "fiction,fantasy",
                "price": 60,
                "rating_int": 46,
            },
            {
                "id": 9,
                "embedding": [1.0, 0, 0, 0],
                "category": "clothing",
                "tags": "shirt,summer",
                "price": 100,
                "rating_int": 41,
            },
            {
                "id": 10,
                "embedding": [1.0, 0, 0, 0],
                "category": "clothing",
                "tags": "pants,winter",
                "price": 200,
                "rating_int": 43,
            },
        ]
        self.collection.upsert_data(data)

    def _create_index(self):
        index_meta = {
            "IndexName": "idx_complex",
            "VectorIndex": {"IndexType": "flat"},
            "ScalarIndex": ["id", "category", "tags", "price", "rating_int"],
        }
        self.collection.create_index("idx_complex", index_meta)

    def _search(self, filters):
        res = self.collection.search_by_vector(
            "idx_complex", dense_vector=[1.0, 0, 0, 0], limit=100, filters=filters
        )
        return sorted([item.id for item in res.data])

    def test_nested_and_or(self):
        """Test nested AND/OR logic"""
        # Find (electronics AND (apple products OR rating > 4.8))
        # Electronics: 1, 2, 3, 4
        # Apple: 1, 3 (contains "apple")
        # Rating > 48: 3 (49)
        # Apple OR Rating > 48: 1, 3
        # Electronics AND (Apple OR Rating > 48): 1, 3
        filters = {
            "op": "and",
            "conds": [
                {"op": "must", "field": "category", "conds": ["electronics"]},
                {
                    "op": "or",
                    "conds": [
                        {"op": "contains", "field": "tags", "substring": "apple"},
                        {"op": "range", "field": "rating_int", "gt": 48},
                    ],
                },
            ],
        }
        self.assertEqual(self._search(filters), [1, 3])

    def test_complex_exclusion(self):
        """Test complex exclusion logic (AND + MustNot + OR)"""
        # Find products with price < 5000, excluding (category is clothing OR rating < 40)
        # Price < 5000: 2, 4, 5, 6, 7, 8, 9, 10
        # Exclude:
        #   Category clothing: 9, 10
        #   Rating < 40: 6 (38)
        #   Total excluded: 6, 9, 10
        # Result: 2, 4, 5, 7, 8
        filters = {
            "op": "and",
            "conds": [
                {"op": "range", "field": "price", "lt": 5000},
                {"op": "must_not", "field": "category", "conds": ["clothing"]},
                {"op": "range", "field": "rating_int", "gte": 40},
            ],
        }
        # Price < 5000: 2, 4, 5, 6, 7, 8, 9, 10
        # Not clothing: 1, 2, 3, 4, 5, 6, 7, 8 (excludes 9, 10)
        # Rating >= 40: 1, 2, 3, 4, 5, 7, 8, 9, 10 (excludes 6)
        # Intersection: 2, 4, 5, 7, 8
        self.assertEqual(self._search(filters), [2, 4, 5, 7, 8])

    def test_range_and_prefix_mix(self):
        """Test Range and Prefix combination"""
        # Find Tags starting with "f" (furniture, fiction -> 5, 7, 8) AND price in [50, 100]
        # Tags prefix "f": 5, 7, 8
        # Price [50, 100]: 7(50), 8(60), 9(100)
        # Intersection: 7, 8
        filters = {
            "op": "and",
            "conds": [
                {"op": "prefix", "field": "tags", "prefix": "f"},
                {"op": "range", "field": "price", "gte": 50, "lte": 100},
            ],
        }
        self.assertEqual(self._search(filters), [7, 8])

    def test_deeply_nested_logic(self):
        """Test deeply nested logic ((A or B) and (C or (D and E)))"""
        # Logic:
        # ((Category="electronics" OR Category="home") AND
        #  (Price < 1000 OR (Tags contains "fiction" AND Rating > 4.5)))

        # A: Category="electronics" -> 1, 2, 3, 4
        # B: Category="home" -> 5, 6
        # A OR B -> 1, 2, 3, 4, 5, 6

        # C: Price < 1000 -> 6(300), 7(50), 8(60), 9(100), 10(200)
        # D: Tags contains "fiction" -> 7, 8
        # E: Rating > 4.5 -> 1(48), 3(49), 7(47), 8(46)
        # D AND E -> 7, 8
        # C OR (D AND E) -> 6, 7, 8, 9, 10 (7,8 already in C, so union is same as C)

        # Intersection (A OR B) AND (C OR (D AND E)):
        # {1, 2, 3, 4, 5, 6} INTERSECT {6, 7, 8, 9, 10}
        # Result: 6

        filters = {
            "op": "and",
            "conds": [
                {
                    "op": "or",
                    "conds": [
                        {"op": "must", "field": "category", "conds": ["electronics"]},
                        {"op": "must", "field": "category", "conds": ["home"]},
                    ],
                },
                {
                    "op": "or",
                    "conds": [
                        {"op": "range", "field": "price", "lt": 1000},
                        {
                            "op": "and",
                            "conds": [
                                {"op": "contains", "field": "tags", "substring": "fiction"},
                                {"op": "range", "field": "rating_int", "gt": 45},
                            ],
                        },
                    ],
                },
            ],
        }
        self.assertEqual(self._search(filters), [6])

    def test_range_out_logic(self):
        """Test range_out and its combinations"""
        # range_out: price < 3000 OR price > 8000
        # Prices: 8000(1), 3000(2), 15000(3), 4000(4), 2000(5), 300(6), 50(7), 60(8), 100(9), 200(10)
        # > 8000: 3 (15000)
        # < 3000: 5, 6, 7, 8, 9, 10
        # Result: 3, 5, 6, 7, 8, 9, 10
        # (Assuming range_out(gte=3000, lte=8000) means NOT [3000, 8000])

        filters = {"op": "range_out", "field": "price", "gte": 3000, "lte": 8000}
        res = self._search(filters)
        self.assertEqual(res, [3, 5, 6, 7, 8, 9, 10])

        # range_out combined with Must
        # (price < 3000 OR price > 8000) AND Category="electronics"
        # Electronics: 1, 2, 3, 4
        # Intersection with above: 3
        filters_combined = {
            "op": "and",
            "conds": [filters, {"op": "must", "field": "category", "conds": ["electronics"]}],
        }
        self.assertEqual(self._search(filters_combined), [3])

    def test_multi_layer_logic(self):
        """Test multi-layer logic structure (A OR (B AND (C OR D)))"""
        # (Category="books" OR (Category="clothing" AND (Price > 150 OR Rating > 4.2)))

        # A: Category="books" -> 7, 8
        # B: Category="clothing" -> 9, 10
        # C: Price > 150 -> 1, 2, 3, 4, 5, 6, 10
        # D: Rating > 4.2 -> 1, 2, 3, 7, 8, 10
        # C OR D -> 1, 2, 3, 4, 5, 6, 7, 8, 10
        # B AND (C OR D) -> {9, 10} INTERSECT {1..8, 10} -> {10}
        # A OR (B AND ...) -> {7, 8} UNION {10} -> {7, 8, 10}

        filters = {
            "op": "or",
            "conds": [
                {"op": "must", "field": "category", "conds": ["books"]},
                {
                    "op": "and",
                    "conds": [
                        {"op": "must", "field": "category", "conds": ["clothing"]},
                        {
                            "op": "or",
                            "conds": [
                                {"op": "range", "field": "price", "gt": 150},
                                {"op": "range", "field": "rating_int", "gt": 42},
                            ],
                        },
                    ],
                },
            ],
        }
        self.assertEqual(self._search(filters), [7, 8, 10])

    def test_mixed_type_logic(self):
        """Test mixed type field filtering (String Prefix + Int Range + Logic)"""
        # (Tags prefix "mobile" AND Price < 5000) OR (Tags prefix "kitchen" AND Price < 500)

        # Part 1: Tags prefix "mobile" -> 1, 2
        #         Price < 5000 -> 2, 4, 5, 6, 7, 8, 9, 10
        #         Intersection -> 2

        # Part 2: Tags prefix "kitchen" -> 6
        #         Price < 500 -> 6, 7, 8, 9, 10
        #         Intersection -> 6

        # Union -> 2, 6

        filters = {
            "op": "or",
            "conds": [
                {
                    "op": "and",
                    "conds": [
                        {"op": "prefix", "field": "tags", "prefix": "mobile"},
                        {"op": "range", "field": "price", "lt": 5000},
                    ],
                },
                {
                    "op": "and",
                    "conds": [
                        {"op": "prefix", "field": "tags", "prefix": "kitchen"},
                        {"op": "range", "field": "price", "lt": 500},
                    ],
                },
            ],
        }
        self.assertEqual(self._search(filters), [2, 6])

    def test_many_or_conditions(self):
        """Test multiple OR conditions"""
        # (Category="books" OR Category="clothing" OR Price > 10000)
        # Books: 7, 8
        # Clothing: 9, 10
        # Price > 10000: 3 (15000)
        # Union: 3, 7, 8, 9, 10
        filters = {
            "op": "or",
            "conds": [
                {"op": "must", "field": "category", "conds": ["books"]},
                {"op": "must", "field": "category", "conds": ["clothing"]},
                {"op": "range", "field": "price", "gt": 10000},
            ],
        }
        self.assertEqual(self._search(filters), [3, 7, 8, 9, 10])

    def test_must_not_combinations(self):
        """Test multiple MustNot combinations"""
        # MustNot(Category="electronics") AND MustNot(Price < 100)
        # NOT Electronics: 5, 6, 7, 8, 9, 10
        # NOT Price < 100: (Price >= 100) -> 1, 2, 3, 4, 5, 6, 9, 10 (excludes 7, 8 which are 50, 60)
        # Intersection: 5, 6, 9, 10
        filters = {
            "op": "and",
            "conds": [
                {"op": "must_not", "field": "category", "conds": ["electronics"]},
                {"op": "range", "field": "price", "gte": 100},  # Equivalent to MustNot(Price < 100)
            ],
        }
        self.assertEqual(self._search(filters), [5, 6, 9, 10])


class TestFilterOpsLifecycle(unittest.TestCase):
    """Insert/update/delete and restart tests"""

    def setUp(self):
        clean_dir(db_path_lifecycle)
        self.path = db_path_lifecycle
        self.collection_name = "test_lifecycle"
        self.collection = self._create_collection()
        self._insert_initial_data()
        self._create_index()

    def tearDown(self):
        if self.collection:
            self.collection.drop()
        clean_dir(self.path)

    def _create_collection(self):
        collection_meta = {
            "CollectionName": self.collection_name,
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "embedding", "FieldType": "vector", "Dim": 4},
                {"FieldName": "status", "FieldType": "string"},
                {"FieldName": "count", "FieldType": "int64"},
            ],
        }
        return get_or_create_local_collection(meta_data=collection_meta, path=self.path)

    def _insert_initial_data(self):
        data = [
            {"id": 1, "embedding": [1.0, 0, 0, 0], "status": "active", "count": 10},
            {"id": 2, "embedding": [1.0, 0, 0, 0], "status": "active", "count": 20},
            {"id": 3, "embedding": [1.0, 0, 0, 0], "status": "inactive", "count": 30},
        ]
        self.collection.upsert_data(data)

    def _create_index(self):
        index_meta = {
            "IndexName": "idx_lifecycle",
            "VectorIndex": {"IndexType": "flat"},
            "ScalarIndex": ["id", "status", "count"],
        }
        self.collection.create_index("idx_lifecycle", index_meta)

    def _search(self, filters, coll=None):
        c = coll if coll else self.collection
        res = c.search_by_vector(
            "idx_lifecycle", dense_vector=[1.0, 0, 0, 0], limit=100, filters=filters
        )
        return sorted([item.id for item in res.data])

    def test_update_impact(self):
        """Test Update impact on Filter"""
        # Initial state: status=active -> 1, 2
        self.assertEqual(
            self._search({"op": "must", "field": "status", "conds": ["active"]}), [1, 2]
        )

        # Update id=2 status to inactive
        # Update id=3 status to active
        updates = [
            {"id": 2, "embedding": [1.0, 0, 0, 0], "status": "inactive", "count": 25},
            {"id": 3, "embedding": [1.0, 0, 0, 0], "status": "active", "count": 35},
        ]
        self.collection.upsert_data(updates)

        # Verify after update: status=active -> 1, 3
        self.assertEqual(
            self._search({"op": "must", "field": "status", "conds": ["active"]}), [1, 3]
        )
        # Verify count update: count > 30 -> 3 (35)
        self.assertEqual(self._search({"op": "range", "field": "count", "gt": 30}), [3])

    def test_delete_impact(self):
        """Test Delete impact on Filter"""
        # Initial: count >= 10 -> 1, 2, 3
        self.assertEqual(self._search({"op": "range", "field": "count", "gte": 10}), [1, 2, 3])

        # Delete id=2
        self.collection.delete_data([2])

        # Verify: count >= 10 -> 1, 3
        self.assertEqual(self._search({"op": "range", "field": "count", "gte": 10}), [1, 3])

        # Confirm deleted id cannot be found via ID Filter
        self.assertEqual(self._search({"op": "must", "field": "id", "conds": [2]}), [])

    def test_persistence_restart(self):
        """Test if Filter works correctly after restart (reload Collection)"""
        # Insert new data
        new_data = [{"id": 4, "embedding": [1.0, 0, 0, 0], "status": "active", "count": 40}]
        self.collection.upsert_data(new_data)

        # Ensure data is written
        # Simulate restart: release old object, reload
        del self.collection
        self.collection = None  # Avoid tearDown accessing deleted attribute
        gc.collect()
        time.sleep(0.1)

        collection_new = get_or_create_local_collection(path=self.path)
        # Assign new object to self.collection for tearDown cleanup
        self.collection = collection_new

        # Verify Filter query
        # status=active -> 1, 2, 4 (3 is inactive)
        ids = self._search({"op": "must", "field": "status", "conds": ["active"]})
        self.assertEqual(ids, [1, 2, 4])

        # Verify Range
        # count > 25 -> 3(30), 4(40)
        ids = self._search({"op": "range", "field": "count", "gt": 25})
        self.assertEqual(ids, [3, 4])

        # tearDown will handle drop


class TestFilterOpsPath(unittest.TestCase):
    """Path type Filter tests"""

    def setUp(self):
        clean_dir("./db_test_filters_path/")
        self.path = "./db_test_filters_path/"
        self.collection = self._create_collection()
        self._insert_data()
        self._create_index()

    def tearDown(self):
        if self.collection:
            self.collection.drop()
        clean_dir(self.path)

    def _create_collection(self):
        collection_meta = {
            "CollectionName": "test_filters_path",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "embedding", "FieldType": "vector", "Dim": 4},
                {"FieldName": "file_path", "FieldType": "path"},
            ],
        }
        return get_or_create_local_collection(meta_data=collection_meta, path=self.path)

    def _insert_data(self):
        # Build path data
        data = [
            {"id": 1, "embedding": [1.0, 0, 0, 0], "file_path": "/a/b/c"},
            {"id": 2, "embedding": [1.0, 0, 0, 0], "file_path": "/a/b/d"},
            {"id": 3, "embedding": [1.0, 0, 0, 0], "file_path": "/a/e"},
            {"id": 4, "embedding": [1.0, 0, 0, 0], "file_path": "/f/g"},
            {"id": 5, "embedding": [1.0, 0, 0, 0], "file_path": "/f/h/i"},
        ]
        self.collection.upsert_data(data)

    def _create_index(self):
        index_meta = {
            "IndexName": "idx_path",
            "VectorIndex": {"IndexType": "flat"},
            "ScalarIndex": ["id", "file_path"],
        }
        self.collection.create_index("idx_path", index_meta)

    def _search(self, filters):
        res = self.collection.search_by_vector(
            "idx_path", dense_vector=[1.0, 0, 0, 0], limit=100, filters=filters
        )
        return sorted([item.id for item in res.data])

    def test_path_must(self):
        """Test Must matching path prefix"""
        # Must /a -> /a/b/c, /a/b/d, /a/e
        self.assertEqual(
            self._search({"op": "must", "field": "file_path", "conds": ["/a"]}), [1, 2, 3]
        )
        # Must /a/b -> /a/b/c, /a/b/d
        self.assertEqual(
            self._search({"op": "must", "field": "file_path", "conds": ["/a/b"]}), [1, 2]
        )
        # Must /f -> /f/g, /f/h/i
        self.assertEqual(
            self._search({"op": "must", "field": "file_path", "conds": ["/f"]}), [4, 5]
        )

    def test_path_must_not(self):
        """Test MustNot excluding path prefix"""
        # MustNot /a/b -> exclude 1, 2 -> remaining 3, 4, 5
        self.assertEqual(
            self._search({"op": "must_not", "field": "file_path", "conds": ["/a/b"]}), [3, 4, 5]
        )
        # MustNot /a -> exclude 1, 2, 3 -> remaining 4, 5
        self.assertEqual(
            self._search({"op": "must_not", "field": "file_path", "conds": ["/a"]}), [4, 5]
        )

    def test_path_must_normalize_leading_slash(self):
        """Test Must/MustNot when path values are missing leading '/'"""
        data = [
            {"id": 6, "embedding": [1.0, 0, 0, 0], "file_path": "a/b/c"},
            {"id": 7, "embedding": [1.0, 0, 0, 0], "file_path": "f/h/i"},
            {"id": 8, "embedding": [1.0, 0, 0, 0], "file_path": "a"},
            {"id": 9, "embedding": [1.0, 0, 0, 0], "file_path": "viking://resources/tmp/x"},
        ]
        self.collection.upsert_data(data)

        # Must /a -> /a/b/c, /a/b/d, /a/e, and /a
        self.assertEqual(
            self._search({"op": "must", "field": "file_path", "conds": ["/a"]}),
            [1, 2, 3, 6, 8],
        )
        # Must /a/b -> /a/b/c, /a/b/d
        self.assertEqual(
            self._search({"op": "must", "field": "file_path", "conds": ["/a/b"]}),
            [1, 2, 6],
        )
        # Must /f -> /f/g, /f/h/i
        self.assertEqual(
            self._search({"op": "must", "field": "file_path", "conds": ["/f"]}),
            [4, 5, 7],
        )
        # MustNot /a/b -> exclude 1, 2, 6
        self.assertEqual(
            self._search({"op": "must_not", "field": "file_path", "conds": ["/a/b"]}),
            [3, 4, 5, 7, 8, 9],
        )
        # Ensure scheme is preserved, only prefixed with '/'
        self.assertEqual(
            self._search({"op": "must", "field": "file_path", "conds": ["/viking://resources"]}),
            [9],
        )

    def test_path_depth(self):
        """Test path type depth parameter"""
        # Must /a with depth=1 (para="-d=1")
        # Should only match first-level children under /a?
        # /a/e (id 3) is first level?
        # /a/b (directory) is first level, but /a/b/c is second level?
        # Typical path index logic:
        # /a/e -> depth 1 (relative to /a)
        # /a/b/c -> depth 2 (relative to /a)

        # Test depth=1: should include /a/e (id 3)
        # Note: exact depth definition depends on implementation. Assume -d=1 means direct children.
        # /a/e is a direct child file.
        # /a/b/c is under /a/b, relative to /a it's second level.

        # Expected: [3] (/a/e)
        # If /a/b/d is also included, depth definition differs.
        # Let's refer to previous TestCollection test logic, depth=1 seems to include direct children.

        # Correction: based on previous TestCollection test logic:
        # /project (depth=0)
        # /project/readme.md (depth=1)
        # /project/src (depth=1)
        # /project/src/main.py (depth=2)

        # Here:
        # /a (base)
        # /a/e (depth 1)
        # /a/b (dir, depth 1) -> /a/b/c (depth 2)

        # So Must /a, depth=1 should match items with depth <= 1? Or depth == 1?
        # Usually it's recursive depth control.
        # If it's recursive depth control, depth=1 may mean only return up to first level children.
        # Verify:
        # Must /a, depth=1
        # /a/e -> Yes
        # /a/b/c -> No (depth 2)

        self.assertEqual(
            self._search({"op": "must", "field": "file_path", "conds": ["/a"], "para": "-d=1"}), [3]
        )

        # Must /a, depth=2 (default recursive all? Or specified depth)
        # /a/e (depth 1) -> Yes
        # /a/b/c (depth 2) -> Yes
        self.assertEqual(
            self._search({"op": "must", "field": "file_path", "conds": ["/a"], "para": "-d=2"}),
            [1, 2, 3],
        )


class TestFilterOpsScale(unittest.TestCase):
    """Scale tests"""

    def setUp(self):
        clean_dir(db_path_scale)
        self.path = db_path_scale
        self.collection = self._create_collection()
        self.data_count = 50000  # Reduced from 500k to 50k to avoid timeout/OOM in test env
        self._insert_large_data()
        self._create_index()

    def tearDown(self):
        if self.collection:
            self.collection.drop()
        clean_dir(self.path)

    def _create_collection(self):
        collection_meta = {
            "CollectionName": "test_scale",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "embedding", "FieldType": "vector", "Dim": 4},
                {"FieldName": "group", "FieldType": "string"},
                {"FieldName": "sub_group", "FieldType": "string"},
                {"FieldName": "score", "FieldType": "int64"},
                {"FieldName": "tag", "FieldType": "string"},
            ],
        }
        return get_or_create_local_collection(meta_data=collection_meta, path=self.path)

    def _insert_large_data(self):
        # Insert 500,000 records
        # Group: A (0-49%), B (50-99%)
        # SubGroup: X (even id), Y (odd id)
        # Score: id % 100
        # Tag: prefix_{id%1000}

        batch_size = 10000
        data = []
        print(f"Inserting {self.data_count} items...")
        start_time = time.time()

        for i in range(self.data_count):
            group = "A" if i < self.data_count / 2 else "B"
            sub_group = "X" if i % 2 == 0 else "Y"
            score = i % 100
            tag = f"tag_{i % 1000}"

            data.append(
                {
                    "id": i,
                    "embedding": [random.random() for _ in range(4)],
                    "group": group,
                    "sub_group": sub_group,
                    "score": score,
                    "tag": tag,
                }
            )

            if len(data) >= batch_size:
                self.collection.upsert_data(data)
                data = []
                # print(f"Inserted {i+1} items")

        if data:
            self.collection.upsert_data(data)

        print(f"Insert finished in {time.time() - start_time:.2f}s")

    def _create_index(self):
        print("Creating index...")
        start_time = time.time()
        index_meta = {
            "IndexName": "idx_scale",
            "VectorIndex": {"IndexType": "flat"},
            "ScalarIndex": ["id", "group", "sub_group", "score", "tag"],
        }
        self.collection.create_index("idx_scale", index_meta)
        print(f"Index created in {time.time() - start_time:.2f}s")

    def _search(self, filters, limit=10000):
        # For large scale test, we just check count mainly
        res = self.collection.search_by_vector(
            "idx_scale", dense_vector=[0.0, 0.0, 0.0, 0.0], limit=limit, filters=filters
        )
        return [item.id for item in res.data]

    def test_scale_filtering(self):
        """Large scale data filtering correctness"""
        # Filter 1: Group A AND Score > 90
        # Expected: in 0-249999, numbers where % 100 > 90
        # 91-99, 191-199... 9 numbers per 100.
        # 250000 / 100 * 9 = 22500
        print("Testing Filter 1: Group A AND Score > 90")
        start_time = time.time()
        filters = {
            "op": "and",
            "conds": [
                {"op": "must", "field": "group", "conds": ["A"]},
                {"op": "range", "field": "score", "gt": 90},
            ],
        }
        # limit needs to be large enough to catch all or we verify count returned is limit
        # If limit < expected, we get limit. If limit > expected, we get expected.
        # Let's set a large limit for counting test, but verify performance
        # 22500 results is large. Let's just sample top 100 but rely on a smaller range for exact count verification
        # or verify standard limit behavior.

        # Search with smaller result set for verification
        # Group A AND Score = 99 (1 per 100) -> 2500 expected (50k scale -> 250 expected)
        filters = {
            "op": "and",
            "conds": [
                {"op": "must", "field": "group", "conds": ["A"]},
                {"op": "must", "field": "score", "conds": [99]},
            ],
        }
        ids = self._search(filters, limit=5000)
        print(f"Filter 1 time: {time.time() - start_time:.4f}s")
        # 50k / 2 = 25k Group A. 25k / 100 = 250.
        self.assertEqual(len(ids), 250)
        for i in ids:
            self.assertTrue(i < self.data_count / 2)
            self.assertEqual(i % 100, 99)

        # Filter 2: Complex Logic
        # (Group A AND SubGroup X) OR (Group B AND Score < 5)
        # Group A (0-24999) AND X (Even) -> 12500 items
        # Group B (25000-49999) AND Score < 5 (0,1,2,3,4 -> 5 per 100) -> 250 * 5 = 1250 items
        # Total = 13750 items. Too many to fetch all.
        # Let's add more conditions to reduce result set.

        # (Group A AND SubGroup X AND Tag="tag_0")
        # Tag="tag_0" -> id % 1000 == 0.
        # Group A (0-24999): 25 items with tag_0.
        # SubGroup X (Even): tag_0 implies id%1000=0 which is even. So all 25 items match X.
        # Result: 25 items.

        # OR

        # (Group B AND Score=0 AND SubGroup Y)
        # Group B (25000-49999)
        # Score=0 -> id % 100 == 0.
        # SubGroup Y (Odd) -> id is Odd.
        # id % 100 == 0 implies id is Even. So (Even AND Odd) -> Empty.
        # Result: 0 items.

        # Total expected: 25 items.
        print("Testing Filter 2: Complex Nested Logic")
        filters = {
            "op": "or",
            "conds": [
                {
                    "op": "and",
                    "conds": [
                        {"op": "must", "field": "group", "conds": ["A"]},
                        {"op": "must", "field": "sub_group", "conds": ["X"]},
                        {"op": "must", "field": "tag", "conds": ["tag_0"]},
                    ],
                },
                {
                    "op": "and",
                    "conds": [
                        {"op": "must", "field": "group", "conds": ["B"]},
                        {"op": "must", "field": "score", "conds": [0]},
                        {"op": "must", "field": "sub_group", "conds": ["Y"]},
                    ],
                },
            ],
        }
        start_time = time.time()
        ids = self._search(filters, limit=1000)
        print(f"Filter 2 time: {time.time() - start_time:.4f}s")
        self.assertEqual(len(ids), 25)
        for i in ids:
            self.assertEqual(i % 1000, 0)
            self.assertTrue(i < self.data_count / 2)

        # Filter 3: Regex on Tag (Slow op check)
        # Tag ends with "999" -> tag_999
        # id % 1000 == 999.
        # Total 50000 / 1000 = 50 items.
        print("Testing Filter 3: Regex")
        filters = {"op": "regex", "field": "tag", "pattern": "999$"}
        start_time = time.time()
        ids = self._search(filters, limit=1000)
        print(f"Filter 3 time: {time.time() - start_time:.4f}s")
        self.assertEqual(len(ids), 50)
        for i in ids:
            self.assertEqual(i % 1000, 999)


class TestFilterOpsTypes(unittest.TestCase):
    """Comprehensive tests for various field types and operators"""

    def setUp(self):
        clean_dir("./db_test_filters_types/")
        self.path = "./db_test_filters_types/"
        self.collection = self._create_collection()
        self._insert_data()
        self._create_index()

    def tearDown(self):
        if self.collection:
            self.collection.drop()
        clean_dir(self.path)

    def _create_collection(self):
        collection_meta = {
            "CollectionName": "test_filters_types",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "embedding", "FieldType": "vector", "Dim": 4},
                {"FieldName": "f_int", "FieldType": "int64"},
                {"FieldName": "f_float", "FieldType": "float32"},
                {"FieldName": "f_bool", "FieldType": "bool"},
                {"FieldName": "f_str", "FieldType": "string"},
                {"FieldName": "f_list_str", "FieldType": "list<string>"},
                {"FieldName": "f_list_int", "FieldType": "list<int64>"},
                {"FieldName": "f_date", "FieldType": "date_time"},
                {"FieldName": "f_geo", "FieldType": "geo_point"},
            ],
        }
        return get_or_create_local_collection(meta_data=collection_meta, path=self.path)

    def _insert_data(self):
        self.data = [
            {
                "id": 1,
                "embedding": [1.0, 0, 0, 0],
                "f_int": 10,
                "f_float": 1.1,
                "f_bool": True,
                "f_str": "apple",
                "f_list_str": ["a", "b"],
                "f_list_int": [1, 2],
                "f_date": "2023-01-01T00:00:00+00:00",
                "f_geo": "0,0",
            },
            {
                "id": 2,
                "embedding": [1.0, 0, 0, 0],
                "f_int": 20,
                "f_float": 2.2,
                "f_bool": False,
                "f_str": "banana",
                "f_list_str": ["b", "c"],
                "f_list_int": [2, 3],
                "f_date": "2023-01-02T00:00:00+00:00",
                "f_geo": "10,10",
            },
            {
                "id": 3,
                "embedding": [1.0, 0, 0, 0],
                "f_int": 30,
                "f_float": 3.3,
                "f_bool": True,
                "f_str": "cherry",
                "f_list_str": ["c", "d"],
                "f_list_int": [3, 4],
                "f_date": "2023-01-03T00:00:00+00:00",
                "f_geo": "20,20",
            },
            {
                "id": 4,
                "embedding": [1.0, 0, 0, 0],
                "f_int": -10,
                "f_float": -1.1,
                "f_bool": False,
                "f_str": "date",
                "f_list_str": ["d", "e"],
                "f_list_int": [4, 5],
                "f_date": "2022-12-31T00:00:00+00:00",
                "f_geo": "-10,-10",
            },
            {
                "id": 5,
                "embedding": [1.0, 0, 0, 0],
                "f_int": 0,
                "f_float": 0.0,
                "f_bool": True,
                "f_str": "elderberry",
                "f_list_str": [],
                "f_list_int": [],
                "f_date": "2023-01-01T12:00:00+00:00",
                "f_geo": "179,89",
            },
        ]
        self.collection.upsert_data(self.data)

    def _create_index(self):
        index_meta = {
            "IndexName": "idx_types",
            "VectorIndex": {"IndexType": "flat"},
            "ScalarIndex": [
                "id",
                "f_int",
                "f_float",
                "f_bool",
                "f_str",
                "f_list_str",
                "f_list_int",
                "f_date",
                "f_geo",
            ],
        }
        self.collection.create_index("idx_types", index_meta)

    def _search(self, filters):
        res = self.collection.search_by_vector(
            "idx_types", dense_vector=[1.0, 0, 0, 0], limit=100, filters=filters
        )
        return sorted([item.id for item in res.data])

    def test_debug_float(self):
        # Check if data exists in storage
        res = self.collection.search_by_vector("idx_types", dense_vector=[0] * 4, limit=10)
        print("Stored Data:", [(item.id, item.fields.get("f_float")) for item in res.data])

    def test_numeric_ops(self):
        """Test numeric types (int64, float32)"""
        # Int eq
        self.assertEqual(self._search({"op": "must", "field": "f_int", "conds": [20]}), [2])
        # Int gt
        self.assertEqual(self._search({"op": "range", "field": "f_int", "gt": 0}), [1, 2, 3])
        # Int lt
        self.assertEqual(self._search({"op": "range", "field": "f_int", "lt": 0}), [4])
        # Int range
        self.assertEqual(
            self._search({"op": "range", "field": "f_int", "gte": 10, "lte": 20}), [1, 2]
        )

        # Float gt (approximate)
        # FIXME: Float range query fails, possibly due to C++ implementation issue
        self.assertEqual(self._search({"op": "range", "field": "f_float", "gt": 2.0}), [2, 3])
        # Float range
        self.assertEqual(
            self._search({"op": "range", "field": "f_float", "gte": -2.0, "lte": 0.0}), [4, 5]
        )

    def test_string_ops(self):
        """Test string types"""
        # Eq
        self.assertEqual(self._search({"op": "must", "field": "f_str", "conds": ["banana"]}), [2])
        # Prefix
        self.assertEqual(self._search({"op": "prefix", "field": "f_str", "prefix": "c"}), [3])
        # Contains
        self.assertEqual(
            self._search({"op": "contains", "field": "f_str", "substring": "erry"}), [3, 5]
        )
        # Regex (starts with 'a' or 'd')
        self.assertEqual(
            self._search({"op": "regex", "field": "f_str", "pattern": "^(a|d)"}), [1, 4]
        )

    def test_bool_ops(self):
        """Test boolean types"""
        # True
        self.assertEqual(
            self._search({"op": "must", "field": "f_bool", "conds": [True]}), [1, 3, 5]
        )
        # False
        self.assertEqual(self._search({"op": "must", "field": "f_bool", "conds": [False]}), [2, 4])

    def test_list_ops(self):
        """Test list types"""
        # List<String> contains
        # "a" is in [a, b] (id 1)
        self.assertEqual(self._search({"op": "must", "field": "f_list_str", "conds": ["a"]}), [1])
        # "b" is in id 1, 2
        self.assertEqual(
            self._search({"op": "must", "field": "f_list_str", "conds": ["b"]}), [1, 2]
        )

        # List<Int64> contains
        # 3 is in id 2, 3
        self.assertEqual(self._search({"op": "must", "field": "f_list_int", "conds": [3]}), [2, 3])

    def test_datetime_ops(self):
        """Test date_time types"""
        # Exact match (might be tricky due to ms conversion, use range preferred)
        # 2023-01-01T00:00:00+00:00

        # Range
        # > 2023-01-01
        self.assertEqual(
            self._search({"op": "range", "field": "f_date", "gt": "2023-01-01T10:00:00+00:00"}),
            [2, 3, 5],  # 2(Jan 2), 3(Jan 3), 5(Jan 1 12:00)
        )

        # Range with different format if supported (DataProcessor handles ISO)
        self.assertEqual(
            self._search({"op": "range", "field": "f_date", "lt": "2023-01-01T00:00:00+00:00"}),
            [4],  # 4(Dec 31)
        )

    def test_geo_ops(self):
        """Test geo_point types"""
        # Geo Range (Circle)
        # Center 0,0. Radius 100km.
        # id 1 is 0,0 -> Match
        # id 2 is 10,10 -> ~1500km away -> No match
        self.assertEqual(
            self._search({"op": "geo_range", "field": "f_geo", "center": "0,0", "radius": "100km"}),
            [1],
        )

        # Center 10,10. Radius 2000km.
        # id 1 (0,0) -> ~1500km -> Match
        # id 2 (10,10) -> 0km -> Match
        # id 3 (20,20) -> ~1500km from 10,10 -> Match
        # id 4 (-10,-10) -> ~3000km -> No match
        self.assertEqual(
            self._search(
                {"op": "geo_range", "field": "f_geo", "center": "10,10", "radius": "2000km"}
            ),
            [1, 2, 3],
        )

    def test_mixed_complex(self):
        """Test mixed complex logic"""
        # (f_bool=True AND f_int > 0) OR (f_str prefix "d")
        # Part 1: True & >0 -> 1(10), 3(30). (5 is True but int=0, so not >0 if strictly gt)
        # Part 2: prefix "d" -> 4("date")
        # Union: 1, 3, 4

        filters = {
            "op": "or",
            "conds": [
                {
                    "op": "and",
                    "conds": [
                        {"op": "must", "field": "f_bool", "conds": [True]},
                        {"op": "range", "field": "f_int", "gt": 0},
                    ],
                },
                {"op": "prefix", "field": "f_str", "prefix": "d"},
            ],
        }
        self.assertEqual(self._search(filters), [1, 3, 4])

    def test_persistence_queries(self):
        """Test if filters work correctly after persistence and restart"""
        # 1. Execute queries before close (verified by other tests, but good for baseline)

        def _verify_all_ops():
            # Int eq
            self.assertEqual(self._search({"op": "must", "field": "f_int", "conds": [20]}), [2])
            # Int range
            self.assertEqual(
                self._search({"op": "range", "field": "f_int", "gte": 10, "lte": 20}), [1, 2]
            )
            # String prefix
            self.assertEqual(self._search({"op": "prefix", "field": "f_str", "prefix": "c"}), [3])
            # List contains
            self.assertEqual(
                self._search({"op": "must", "field": "f_list_str", "conds": ["a"]}), [1]
            )
            # Date range
            self.assertEqual(
                self._search({"op": "range", "field": "f_date", "gt": "2023-01-01T10:00:00+00:00"}),
                [2, 3, 5],
            )
            # Mixed complex
            filters = {
                "op": "or",
                "conds": [
                    {
                        "op": "and",
                        "conds": [
                            {"op": "must", "field": "f_bool", "conds": [True]},
                            {"op": "range", "field": "f_int", "gt": 0},
                        ],
                    },
                    {"op": "prefix", "field": "f_str", "prefix": "d"},
                ],
            }
            self.assertEqual(self._search(filters), [1, 3, 4])

        print("Verifying before restart...")
        _verify_all_ops()

        # 2. Close and restart
        print("Closing collection...")
        self.collection.close()
        del self.collection
        self.collection = None
        gc.collect()

        print("Reopening collection...")
        # Re-open using the same path
        self.collection = get_or_create_local_collection(path=self.path)

        # 3. Verify queries after restart
        print("Verifying after restart...")
        _verify_all_ops()


class TestFilterOpsIP(TestFilterOpsBasic):
    """Basic Filter operator tests with Inner Product distance"""

    def setUp(self):
        self.path = "./db_test_filters_ip/"
        clean_dir(self.path)
        self.collection = self._create_collection()
        self._insert_data()
        self._create_index()

    def tearDown(self):
        if self.collection:
            self.collection.drop()
        clean_dir(self.path)

    def _create_index(self):
        index_meta = {
            "IndexName": "idx_basic_ip",
            "VectorIndex": {"IndexType": "flat", "Distance": "ip"},
            "ScalarIndex": ["id", "val_int", "val_float", "val_str"],
        }
        self.collection.create_index("idx_basic_ip", index_meta)

    def _search(self, filters):
        res = self.collection.search_by_vector(
            "idx_basic_ip", dense_vector=[1.0, 0, 0, 0], limit=100, filters=filters
        )
        return sorted([item.id for item in res.data])


if __name__ == "__main__":
    unittest.main()
