# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

TEST_DB_PATH = "./test_data/db_test_openviking_vectordb/"


def clean_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def make_vector(index: int, dim: int) -> list[float]:
    vector = [0.0] * dim
    pos = max(0, min(dim - 1, index - 1))
    vector[pos] = 1.0
    return vector


class TestOpenVikingVectorDB(unittest.TestCase):
    def setUp(self):
        clean_dir(TEST_DB_PATH)
        self.collections = []

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

    def test_search_skips_candidate_with_corrupted_fields(self):
        """损坏 fields 的 candidate 应被跳过，查询不抛异常，且剩余结果 id/score/fields 对齐不错位。

        复现根因：search_by_vector 对每条 candidate 无条件 json.loads，单条坏数据
        （写入路径 uint16 截断产生的不完整 JSON）会让整个查询崩溃。修复后应跳过坏项、
        同步过滤 pk/score，保持各列表对齐。
        """
        collection = self._create_collection()

        # 4 条数据，向量第 0 位各不相同 -> 与 query 的距离唯一 -> top-k 与 score 确定
        docs = []
        for i in range(4):
            vec = [0.0] * 1024
            vec[0] = float(i + 1)
            docs.append(
                {
                    "id": f"doc_{i}",
                    "uri": f"viking://resources/t/doc_{i}.md",
                    "type": "file",
                    "context_type": "markdown",
                    "vector": vec,
                    "sparse_vector": {},
                    "created_at": "2026-01-01T10:00:00.000001",
                    "updated_at": "2026-01-01T12:00:00.000002",
                    "active_count": i,
                    "parent_uri": "viking://resources/t/",
                    "is_leaf": True,
                    "name": f"doc_{i}.md",
                    "description": f"desc {i}",
                    "tags": "t",
                    "abstract": f"abs {i}",
                }
            )
        self.assertEqual(len(collection.upsert_data(docs).ids), len(docs))
        self._create_index(collection)

        query = [0.0] * 1024
        query[0] = 5.0  # l2 距离: doc_3=1, doc_2=2, doc_1=3, doc_0=4（唯一）

        # 基准：未污染时的 id -> score 映射
        baseline = collection.search_by_vector("idx_filters", dense_vector=query, limit=10)
        baseline_scores = {item.id: item.score for item in baseline.data}
        self.assertEqual(len(baseline_scores), 4)

        # 在坏数据真实入口 fetch_cands_data 处，把最近的 doc_3 的 fields 改成未闭合 JSON
        inner = collection._Collection__collection
        real_fetch = inner.store_mgr.fetch_cands_data
        corrupted = {}

        def corrupting_fetch(label_list):
            cands = real_fetch(label_list)
            for c in cands:
                if c is None:
                    continue
                if json.loads(c.fields).get("id") == "doc_3":
                    corrupted["hit"] = True
                    c.fields = '{"id": "doc_3", "uri": "viking://resources/t/doc_3'
            return cands

        with patch.object(inner.store_mgr, "fetch_cands_data", side_effect=corrupting_fetch):
            result = collection.search_by_vector("idx_filters", dense_vector=query, limit=10)

        self.assertTrue(corrupted.get("hit"), "测试未能注入损坏数据")
        ids = sorted(item.id for item in result.data)
        self.assertEqual(ids, ["doc_0", "doc_1", "doc_2"], "坏项应被跳过，其余有效项全部返回")
        for item in result.data:
            # id 与 fields 内 id 自洽、score 与基准一致 —— 任一错位都会让断言失败
            self.assertEqual(item.id, item.fields.get("id"))
            self.assertEqual(item.score, baseline_scores[item.id])

    def test_index_rebuild_backfills_old_rows_and_preserves_previous_version(self):
        collection = self._create_collection()
        collection.upsert_data(
            [
                {"id": "first", "type": "file", "vector": make_vector(1, 1024)},
                {"id": "second", "type": "image", "vector": make_vector(2, 1024)},
            ]
        )
        self._create_index(collection)

        index_meta = collection.get_index_meta_data("idx_filters")
        index = collection.get_index("idx_filters")
        previous_version = index.get_newest_version()
        collection.update(
            fields=[{"FieldName": "schema_flag", "FieldType": "bool", "DefaultValue": False}]
        )
        collection.update_index(
            "idx_filters", scalar_index=[*index_meta["ScalarIndex"], "schema_flag"]
        )

        self.assertGreater(index.get_newest_version(), previous_version)
        self.assertTrue(Path(index.version_dir, str(previous_version)).is_dir())
        self.assertTrue(Path(index.version_dir, f"{previous_version}.write_done").is_file())
        default_flag = {"op": "must", "field": "schema_flag", "conds": [False]}
        self.assertEqual(self._search_ids(collection, default_flag), ["first", "second"])
        self.assertEqual(
            self._search_ids(collection, {"op": "must", "field": "type", "conds": ["image"]}),
            ["second"],
        )

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


if __name__ == "__main__":
    unittest.main()
