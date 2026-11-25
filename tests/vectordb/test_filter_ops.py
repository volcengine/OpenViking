# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import shutil
import unittest

from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

db_path_basic = "./test_data/db_test_filters_basic/"
db_path_complex = "./test_data/db_test_filters_complex/"


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

    def test_nested_filter_requires_a_matching_category_and_one_price_or_tag_branch(self):
        filters = {
            "op": "and",
            "conds": [
                {
                    "op": "or",
                    "conds": [
                        {"op": "must", "field": "category", "conds": ["electronics"]},
                        {"op": "must", "field": "category", "conds": ["books"]},
                    ],
                },
                {
                    "op": "or",
                    "conds": [
                        {"op": "range", "field": "price", "lt": 150},
                        {
                            "op": "and",
                            "conds": [
                                {"op": "contains", "field": "tags", "substring": "mobile"},
                                {"op": "range", "field": "rating_int", "gte": 48},
                            ],
                        },
                    ],
                },
            ],
        }
        # 1 needs the inner AND, 7 and 8 need the price branch. 9 meets the price
        # condition but fails the outer category check; 2 fails the rating.
        self.assertEqual(self._search(filters), [1, 7, 8])

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

    def test_union_accumulates_all_three_branches(self):
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
                {"op": "must", "field": "category", "conds": ["books"]},
            ],
        }
        self.assertEqual(self._search(filters), [2, 6, 7, 8])

    def test_negation_excludes_category_before_applying_price_range(self):
        filters = {
            "op": "and",
            "conds": [
                {"op": "must_not", "field": "category", "conds": ["electronics"]},
                {"op": "range", "field": "price", "gte": 100},
            ],
        }
        self.assertEqual(self._search(filters), [5, 6, 9, 10])


def test_scalar_filters_follow_updates_and_deletes_after_reopen(tmp_path):
    path = str(tmp_path)
    vector = [1.0, 0.0, 0.0, 0.0]
    active = {"op": "must", "field": "status", "conds": ["active"]}
    high_count = {"op": "range", "field": "count", "gt": 30}

    def matching_ids(collection, filters):
        result = collection.search_by_vector(
            "default", dense_vector=vector, filters=filters, limit=10
        )
        return sorted(item.id for item in result.data)

    collection = get_or_create_local_collection(
        path=path,
        meta_data={
            "CollectionName": "filter_lifecycle",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "embedding", "FieldType": "vector", "Dim": 4},
                {"FieldName": "status", "FieldType": "string"},
                {"FieldName": "count", "FieldType": "int64"},
            ],
        },
    )
    try:
        collection.upsert_data(
            [
                {"id": 1, "embedding": vector, "status": "active", "count": 40},
                {"id": 2, "embedding": vector, "status": "active", "count": 20},
                {"id": 3, "embedding": vector, "status": "inactive", "count": 30},
            ]
        )
        collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat"},
                "ScalarIndex": ["id", "status", "count"],
            },
        )
        assert matching_ids(collection, active) == [1, 2]
        assert matching_ids(collection, high_count) == [1]

        collection.update_data(
            [{"id": 2, "status": "inactive"}, {"id": 3, "status": "active", "count": 35}]
        )
        assert matching_ids(collection, active) == [1, 3]
        assert matching_ids(collection, high_count) == [1, 3]

        collection.delete_data([1])
        assert matching_ids(collection, active) == [3]
        assert matching_ids(collection, high_count) == [3]
    finally:
        collection.close()

    reopened = get_or_create_local_collection(path=path)
    try:
        assert matching_ids(reopened, active) == [3]
        assert matching_ids(reopened, high_count) == [3]
        assert matching_ids(reopened, {"op": "must", "field": "id", "conds": [1]}) == []
    finally:
        reopened.close()


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

    def test_path_must_not_after_another_condition(self):
        """Test MustNot excluding path prefix when it is not the first condition of an and"""
        # OpenViking always puts the tenant filter first and a caller's filter after it.
        every_record = {"op": "must", "field": "id", "conds": [1, 2, 3, 4, 5]}

        def after_every_record(condition):
            return {"op": "and", "conds": [every_record, condition]}

        # MustNot /a/b -> exclude 1, 2 -> remaining 3, 4, 5
        must_not_ab = {"op": "must_not", "field": "file_path", "conds": ["/a/b"]}
        self.assertEqual(self._search(after_every_record(must_not_ab)), [3, 4, 5])
        # MustNot /a with depth=1 -> exclude 3 -> remaining 1, 2, 4, 5
        must_not_a_d1 = {"op": "must_not", "field": "file_path", "conds": ["/a"], "para": "-d=1"}
        self.assertEqual(self._search(must_not_a_d1), [1, 2, 4, 5])
        self.assertEqual(self._search(after_every_record(must_not_a_d1)), [1, 2, 4, 5])

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
        # Multi-cond must on list<string> uses contains-any semantics
        self.assertEqual(
            self._search({"op": "must", "field": "f_list_str", "conds": ["b", "d"]}),
            [1, 2, 3, 4],
        )
        self.assertEqual(
            self._search({"op": "must", "field": "f_list_str", "conds": ["missing"]}),
            [],
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

    def test_reopen_preserves_list_postings_and_date_field_types(self):
        self.collection.close()
        self.collection = None
        self.collection = get_or_create_local_collection(path=self.path)

        self.assertEqual(
            self._search({"op": "must", "field": "f_list_str", "conds": ["b", "d"]}),
            [1, 2, 3, 4],
        )
        self.assertEqual(
            self._search(
                {"op": "must", "field": "f_date", "conds": ["2023-01-02T00:00:00+00:00"]}
            ),
            [2],
        )
        self.assertEqual(
            self._search(
                {
                    "op": "time_range",
                    "field": "f_date",
                    "gte": "2023-01-01T10:00:00+00:00",
                    "lte": "2023-01-02T00:00:00+00:00",
                }
            ),
            [2, 5],
        )


if __name__ == "__main__":
    unittest.main()
