# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import shutil
import unittest

from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

TEST_DB_PATH = "./db_test_openviking_vectordb/"


def clean_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def make_vector(index: int, dim: int) -> list[float]:
    vector = [0.0] * dim
    pos = max(0, min(dim - 1, index - 1))
    vector[pos] = 1.0
    return vector


def in_time_range(value: str, gte: str, lte: str) -> bool:
    return (gte is None or value >= gte) and (lte is None or value <= lte)


class TestOpenVikingVectorDB(unittest.TestCase):
    def setUp(self):
        clean_dir(TEST_DB_PATH)
        self.collections = []
        self.data = []
        self.deleted_ids = set()

    def tearDown(self):
        for collection in self.collections:
            try:
                collection.drop()
            except Exception:
                pass
        self.collections.clear()
        clean_dir(TEST_DB_PATH)

    def _register(self, collection):
        self.collections.append(collection)
        return collection

    def _create_collection(self):
        vector_dim = 1024
        meta_data = {
            "CollectionName": "test_openviking_vectordb",
            "Description": "Unified context collection",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "uri", "FieldType": "path"},
                {"FieldName": "type", "FieldType": "string"},
                {"FieldName": "context_type", "FieldType": "string"},
                {"FieldName": "vector", "FieldType": "vector", "Dim": vector_dim},
                {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
                {"FieldName": "created_at", "FieldType": "date_time"},
                {"FieldName": "updated_at", "FieldType": "date_time"},
                {"FieldName": "active_count", "FieldType": "int64"},
                {"FieldName": "parent_uri", "FieldType": "path"},
                {"FieldName": "is_leaf", "FieldType": "bool"},
                {"FieldName": "name", "FieldType": "string"},
                {"FieldName": "description", "FieldType": "string"},
                {"FieldName": "tags", "FieldType": "string"},
                {"FieldName": "abstract", "FieldType": "string"},
            ],
        }
        collection = get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        return self._register(collection)

    def _generate_data(self, dim: int):
        groups = [
            {
                "type": "file",
                "context_type": "markdown",
                "parent_uri": "viking://resources/demo/",
                "ext": ".md",
                "tags": "tag_a;tag_b",
                "abstract": "quick brown",
                "desc_word": "hello",
            },
            {
                "type": "file",
                "context_type": "text",
                "parent_uri": "viking://resources/docs/",
                "ext": ".txt",
                "tags": "tag_b",
                "abstract": "lazy dog",
                "desc_word": "beta",
            },
            {
                "type": "image",
                "context_type": "image",
                "parent_uri": "viking://resources/images/",
                "ext": ".png",
                "tags": "tag_c",
                "abstract": "fox",
                "desc_word": "keyword",
            },
        ]

        data = []
        idx = 1
        month_by_group = ["01", "02", "03"]
        for group_idx, group in enumerate(groups):
            for j in range(10):
                day = 1 + j
                month = month_by_group[group_idx]
                created_at = f"2026-{month}-{day:02d}T10:00:00.{j + 1:06d}"
                updated_at = f"2026-{month}-{day:02d}T12:00:00.{j + 2:06d}"
                name = f"{group['context_type']}_{j}{group['ext']}"
                uri = f"{group['parent_uri']}{name}"
                data.append(
                    {
                        "id": f"res_{idx}",
                        "uri": uri,
                        "type": group["type"],
                        "context_type": group["context_type"],
                        "vector": make_vector(idx, dim),
                        "sparse_vector": {},
                        "created_at": created_at,
                        "updated_at": updated_at,
                        "active_count": idx * 3,
                        "parent_uri": group["parent_uri"],
                        "is_leaf": j % 2 == 0,
                        "name": name,
                        "description": f"{group['desc_word']} desc {j}",
                        "tags": group["tags"],
                        "abstract": group["abstract"],
                    }
                )
                idx += 1
        return data

    def _insert_data(self, collection):
        self.data = self._generate_data(1024)
        result = collection.upsert_data(self.data)
        self.assertEqual(len(result.ids), len(self.data))

    def _create_index(self, collection):
        index_meta = {
            "IndexName": "idx_filters",
            "VectorIndex": {"IndexType": "flat", "Distance": "l2"},
            "ScalarIndex": [
                "uri",
                "type",
                "context_type",
                "created_at",
                "updated_at",
                "active_count",
                "parent_uri",
                "is_leaf",
                "name",
                "description",
                "tags",
                "abstract",
            ],
        }
        collection.create_index("idx_filters", index_meta)

    def _search_ids(self, collection, filters, limit=100):
        result = collection.search_by_vector(
            "idx_filters", dense_vector=make_vector(1, 1024), limit=limit, filters=filters
        )
        return sorted([item.id for item in result.data])

    def _expected_ids(self, predicate):
        return sorted(
            [
                item["id"]
                for item in self.data
                if item["id"] not in self.deleted_ids and predicate(item)
            ]
        )

    def test_filters_update_delete_recall(self):
        collection = self._create_collection()
        self._insert_data(collection)
        self._create_index(collection)

        index_meta = collection.get_index_meta_data("idx_filters") or {}
        self.assertIn("type", index_meta.get("ScalarIndex", []))
        fetched = collection.fetch_data(["res_1"])
        self.assertEqual(fetched.items[0].fields.get("type"), "file")

        self.assertEqual(
            self._search_ids(
                collection, {"op": "must", "field": "context_type", "conds": ["markdown"]}
            ),
            self._expected_ids(lambda item: item["context_type"] == "markdown"),
        )
        self.assertEqual(
            self._search_ids(collection, {"op": "must", "field": "type", "conds": ["file"]}),
            self._expected_ids(lambda item: item["type"] == "file"),
        )
        self.assertEqual(
            self._search_ids(
                collection, {"op": "must_not", "field": "context_type", "conds": ["markdown"]}
            ),
            self._expected_ids(lambda item: item["context_type"] != "markdown"),
        )
        self.assertEqual(
            self._search_ids(collection, {"op": "must", "field": "tags", "conds": ["tag_b"]}),
            self._expected_ids(lambda item: "tag_b" in item["tags"]),
        )
        self.assertEqual(
            self._search_ids(
                collection,
                {"op": "prefix", "field": "uri", "prefix": "viking://resources/demo/"},
            ),
            self._expected_ids(lambda item: item["uri"].startswith("viking://resources/demo/")),
        )
        self.assertEqual(
            self._search_ids(
                collection,
                {"op": "prefix", "field": "parent_uri", "prefix": "viking://resources/docs/"},
            ),
            self._expected_ids(
                lambda item: item["parent_uri"].startswith("viking://resources/docs/")
            ),
        )
        self.assertEqual(
            self._search_ids(
                collection, {"op": "contains", "field": "description", "substring": "keyword"}
            ),
            self._expected_ids(lambda item: "keyword" in item["description"]),
        )
        self.assertEqual(
            self._search_ids(
                collection, {"op": "contains", "field": "abstract", "substring": "quick"}
            ),
            self._expected_ids(lambda item: "quick" in item["abstract"]),
        )
        self.assertEqual(
            self._search_ids(collection, {"op": "regex", "field": "name", "pattern": r".*\.txt$"}),
            self._expected_ids(lambda item: item["name"].endswith(".txt")),
        )
        self.assertEqual(
            self._search_ids(collection, {"op": "range", "field": "active_count", "gt": 60}),
            self._expected_ids(lambda item: item["active_count"] > 60),
        )
        self.assertEqual(
            self._search_ids(
                collection, {"op": "range_out", "field": "active_count", "gte": 10, "lte": 20}
            ),
            self._expected_ids(lambda item: item["active_count"] < 10 or item["active_count"] > 20),
        )
        self.assertEqual(
            self._search_ids(
                collection,
                {
                    "op": "time_range",
                    "field": "created_at",
                    "gte": "2026-02-03T00:00:00",
                    "lte": "2026-02-08T23:59:59",
                },
            ),
            self._expected_ids(
                lambda item: in_time_range(
                    item["created_at"], "2026-02-03T00:00:00", "2026-02-08T23:59:59"
                )
            ),
        )
        target_updated_at = self.data[0]["updated_at"]
        self.assertEqual(
            self._search_ids(
                collection,
                {"op": "must", "field": "updated_at", "conds": [target_updated_at]},
            ),
            self._expected_ids(lambda item: item["updated_at"] == target_updated_at),
        )
        self.assertEqual(
            self._search_ids(collection, {"op": "must", "field": "is_leaf", "conds": [True]}),
            self._expected_ids(lambda item: item["is_leaf"] is True),
        )
        self.assertEqual(
            self._search_ids(
                collection,
                {
                    "op": "and",
                    "conds": [
                        {"op": "must", "field": "context_type", "conds": ["text"]},
                        {"op": "must", "field": "tags", "conds": ["tag_b"]},
                        {"op": "must", "field": "is_leaf", "conds": [False]},
                    ],
                },
            ),
            self._expected_ids(
                lambda item: item["context_type"] == "text"
                and "tag_b" in item["tags"]
                and item["is_leaf"] is False
            ),
        )
        self.assertEqual(
            self._search_ids(
                collection,
                {
                    "op": "or",
                    "conds": [
                        {"op": "must", "field": "context_type", "conds": ["markdown"]},
                        {"op": "must", "field": "context_type", "conds": ["image"]},
                    ],
                },
            ),
            self._expected_ids(lambda item: item["context_type"] in ("markdown", "image")),
        )

        # Update: change active_count + name + updated_at for res_12
        target_id = "res_12"
        updated_payload = None
        for item in self.data:
            if item["id"] == target_id:
                item["active_count"] = 999
                item["name"] = "text_99.txt"
                item["updated_at"] = "2026-02-28T12:00:00.000000"
                updated_payload = dict(item)
                break
        self.assertIsNotNone(updated_payload)
        collection.upsert_data([updated_payload])

        self.assertEqual(
            self._search_ids(collection, {"op": "range", "field": "active_count", "gt": 900}),
            self._expected_ids(lambda item: item["active_count"] > 900),
        )
        self.assertEqual(
            self._search_ids(
                collection, {"op": "regex", "field": "name", "pattern": r"text_99\.txt"}
            ),
            self._expected_ids(lambda item: item["name"] == "text_99.txt"),
        )
        self.assertEqual(
            self._search_ids(
                collection,
                {
                    "op": "time_range",
                    "field": "updated_at",
                    "gte": "2026-02-28T00:00:00",
                    "lte": "2026-02-28T23:59:59",
                },
            ),
            self._expected_ids(
                lambda item: in_time_range(
                    item["updated_at"], "2026-02-28T00:00:00", "2026-02-28T23:59:59"
                )
            ),
        )

        # Delete: remove res_30
        self.deleted_ids.add("res_30")
        collection.delete_data(["res_30"])
        self.assertEqual(
            self._search_ids(collection, {"op": "must", "field": "tags", "conds": ["tag_c"]}),
            self._expected_ids(lambda item: item["tags"] == "tag_c"),
        )

        # Recall: exact vector should return res_1 at top-1
        recall = collection.search_by_vector(
            "idx_filters", dense_vector=make_vector(1, 1024), limit=1
        )
        self.assertEqual([item.id for item in recall.data], ["res_1"])

    def test_offset_collision_after_delete(self):
        """Test for regression of logical offset collision.

        Scenario:
        1. Insert A, B. (A=0, B=1)
        2. Delete A. (Count=1)
        3. Insert C. (Count=2, New Offset should not be 1 if B is still at 1)
        4. Search for B and C with filters to ensure no collision.
        """
        collection = self._create_collection()
        dim = 1024

        # 1. Insert A and B
        data_init = [
            {
                "id": "A",
                "vector": make_vector(1, dim),
                "sparse_vector": {},
                "type": "file",
                "context_type": "text",
                "tags": "tag_A",
                "created_at": "2026-01-01T00:00:00.000000",
                "updated_at": "2026-01-01T00:00:00.000000",
                "active_count": 10,
                "is_leaf": True,
                "name": "A.txt",
                "description": "desc A",
                "abstract": "abs A",
            },
            {
                "id": "B",
                "vector": make_vector(2, dim),
                "sparse_vector": {},
                "type": "file",
                "context_type": "text",
                "tags": "tag_B",
                "created_at": "2026-01-02T00:00:00.000000",
                "updated_at": "2026-01-02T00:00:00.000000",
                "active_count": 20,
                "is_leaf": True,
                "name": "B.txt",
                "description": "desc B",
                "abstract": "abs B",
            },
        ]
        collection.upsert_data(data_init)
        self._create_index(collection)

        # Verify initial state
        res_a = collection.search_by_vector(
            "idx_filters",
            make_vector(1, dim),
            limit=1,
            filters={"op": "must", "field": "tags", "conds": ["tag_A"]},
        )
        self.assertEqual(res_a.data[0].id, "A")

        res_b = collection.search_by_vector(
            "idx_filters",
            make_vector(2, dim),
            limit=1,
            filters={"op": "must", "field": "tags", "conds": ["tag_B"]},
        )
        self.assertEqual(res_b.data[0].id, "B")

        # 2. Delete A
        collection.delete_data(["A"])

        # 3. Insert C
        data_c = [
            {
                "id": "C",
                "vector": make_vector(3, dim),
                "sparse_vector": {},
                "type": "file",
                "context_type": "text",
                "tags": "tag_C",
                "created_at": "2026-01-03T00:00:00.000000",
                "updated_at": "2026-01-03T00:00:00.000000",
                "active_count": 30,
                "is_leaf": True,
                "name": "C.txt",
                "description": "desc C",
                "abstract": "abs C",
            }
        ]
        collection.upsert_data(data_c)

        # 4. Search for B (should still be found correctly)
        # If collision happens, searching for tag_B (offset 1) might point to C's vector or vice versa
        res_b_final = collection.search_by_vector(
            "idx_filters",
            make_vector(2, dim),
            limit=1,
            filters={"op": "must", "field": "tags", "conds": ["tag_B"]},
        )
        self.assertEqual(len(res_b_final.data), 1, "Should find B")
        self.assertEqual(res_b_final.data[0].id, "B", "Should match ID B")

        # 5. Search for C
        res_c_final = collection.search_by_vector(
            "idx_filters",
            make_vector(3, dim),
            limit=1,
            filters={"op": "must", "field": "tags", "conds": ["tag_C"]},
        )
        self.assertEqual(len(res_c_final.data), 1, "Should find C")
        self.assertEqual(res_c_final.data[0].id, "C", "Should match ID C")


class TestOpenVikingVectorDBIP(TestOpenVikingVectorDB):
    def setUp(self):
        super().setUp()
        global TEST_DB_PATH
        TEST_DB_PATH = "./db_test_openviking_vectordb_ip/"
        clean_dir(TEST_DB_PATH)

    def tearDown(self):
        super().tearDown()
        global TEST_DB_PATH
        clean_dir(TEST_DB_PATH)
        TEST_DB_PATH = "./db_test_openviking_vectordb/"  # Reset

    def _create_collection(self):
        vector_dim = 1024
        meta_data = {
            "CollectionName": "test_openviking_vectordb_ip",
            "Description": "Unified context collection IP",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "uri", "FieldType": "path"},
                {"FieldName": "type", "FieldType": "string"},
                {"FieldName": "context_type", "FieldType": "string"},
                {"FieldName": "vector", "FieldType": "vector", "Dim": vector_dim},
                {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
                {"FieldName": "created_at", "FieldType": "date_time"},
                {"FieldName": "updated_at", "FieldType": "date_time"},
                {"FieldName": "active_count", "FieldType": "int64"},
                {"FieldName": "parent_uri", "FieldType": "path"},
                {"FieldName": "is_leaf", "FieldType": "bool"},
                {"FieldName": "name", "FieldType": "string"},
                {"FieldName": "description", "FieldType": "string"},
                {"FieldName": "tags", "FieldType": "string"},
                {"FieldName": "abstract", "FieldType": "string"},
            ],
        }
        collection = get_or_create_local_collection(meta_data=meta_data, path=TEST_DB_PATH)
        return self._register(collection)

    def _create_index(self, collection):
        index_meta = {
            "IndexName": "idx_filters",
            "VectorIndex": {"IndexType": "flat", "Distance": "ip"},
            "ScalarIndex": [
                "uri",
                "type",
                "context_type",
                "created_at",
                "updated_at",
                "active_count",
                "parent_uri",
                "is_leaf",
                "name",
                "description",
                "tags",
                "abstract",
            ],
        }
        collection.create_index("idx_filters", index_meta)


if __name__ == "__main__":
    unittest.main()
