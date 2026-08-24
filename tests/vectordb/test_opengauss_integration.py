# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import patch
import os
import uuid
from urllib.parse import unquote, urlparse

import pytest

from openviking.storage.expr import Or
from openviking.storage.vectordb_adapters.opengauss_adapter import (
    OpenGaussCollectionAdapter,
)

pytestmark = pytest.mark.integration


def _build_adapter(collection_name: str, **overrides) -> OpenGaussCollectionAdapter:
    raw_dsn = os.getenv("OPENVIKING_OPENGAUSS_TEST_DSN") or os.getenv(
        "OPENVIKING_OPENGAUSS_STANDALONE_DSN"
    )
    if not raw_dsn:
        pytest.skip("OPENVIKING_OPENGAUSS_TEST_DSN is not configured")

    parsed = urlparse(raw_dsn)
    kwargs = {
        "collection_name": collection_name,
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 5432,
        "user": unquote(parsed.username or "gaussdb"),
        "password": unquote(parsed.password or ""),
        "db_name": parsed.path.lstrip("/") or "postgres",
        "index_name": "default",
        "distance_metric": "cosine",
        "index_type": "hnsw",
        "build_params": {"m": 16, "ef_construction": 64},
        "search_params": {"ef_search": 40},
    }
    kwargs.update(overrides)
    return OpenGaussCollectionAdapter(**kwargs)


def _context_schema(collection_name: str, dim: int = 3) -> dict:
    return {
        "CollectionName": collection_name,
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "uri", "FieldType": "path"},
            {"FieldName": "vector", "FieldType": "vector", "Dim": dim},
            {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
            {"FieldName": "created_at", "FieldType": "date_time"},
            {"FieldName": "updated_at", "FieldType": "date_time"},
            {"FieldName": "level", "FieldType": "int64"},
            {"FieldName": "search_tags", "FieldType": "list<string>"},
            {"FieldName": "abstract", "FieldType": "string"},
            {"FieldName": "content", "FieldType": "text"},
            {"FieldName": "account_id", "FieldType": "string"},
        ],
        "ScalarIndex": ["uri", "created_at", "level", "search_tags"],
    }


def _drop_quietly(adapter: OpenGaussCollectionAdapter | None) -> None:
    if adapter is None:
        return
    try:
        adapter.drop_collection()
    except Exception:
        pass
    try:
        adapter.close()
    except Exception:
        pass


def test_opengauss_context_contract_and_dense_retrieval():
    collection_name = f"ov_it_{uuid.uuid4().hex[:12]}"
    adapter = _build_adapter(collection_name)
    try:
        schema = _context_schema(collection_name)
        assert adapter.create_collection(
            collection_name,
            schema,
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )
        # ``content`` is dropped above the adapter by the storage backend when
        # ``USE_CONTENT_FIELD`` is False, so it never reaches ``adapter.upsert``.
        adapter.upsert(
            [
                {
                    "id": "closest",
                    "uri": "viking://resources/demo/closest",
                    "vector": [1.0, 0.0, 0.0],
                    "sparse_vector": {"alpha": 0.8},
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:01Z",
                    "level": 1,
                    "search_tags": ["env=prod", "team=search"],
                    "abstract": "closest",
                    "account_id": "acct-1",
                },
                {
                    "id": "farther",
                    "uri": "viking://resources/demo/farther",
                    "vector": [0.0, 1.0, 0.0],
                    "sparse_vector": {"beta": 0.4},
                    "created_at": 1767225602000,
                    "updated_at": 1767225603000,
                    "level": 2,
                    "search_tags": ["env=dev"],
                    "abstract": "farther",
                    "account_id": "acct-1",
                },
            ]
        )

        fetched = adapter.get(["closest"])
        assert fetched[0]["created_at"] == 1767225600000
        assert fetched[0]["search_tags"] == ["env=prod", "team=search"]
        assert fetched[0]["sparse_vector"] == {"alpha": 0.8}
        assert "content" not in fetched[0]
        assert isinstance(fetched[0]["vector"], list)
        assert fetched[0]["vector"] == [1.0, 0.0, 0.0]

        projected = adapter.query(
            query_vector=[1.0, 0.0, 0.0],
            limit=1,
            output_fields=["id", "uri", "content", "vector", "account_id"],
        )
        assert projected[0]["id"] == "closest"
        assert projected[0]["content"] is None
        assert isinstance(projected[0]["vector"], list)
        assert projected[0]["vector"] == [1.0, 0.0, 0.0]

        results = adapter.query(
            query_vector=[1.0, 0.0, 0.0],
            limit=2,
            output_fields=["uri", "level", "search_tags"],
        )
        assert [record["id"] for record in results] == ["closest", "farther"]
        assert results[0]["_score"] > results[1]["_score"]
        assert results[0]["_score"] == pytest.approx(1.0)

        filtered = adapter.query(
            query_vector=[1.0, 0.0, 0.0],
            filter={
                "op": "and",
                "conds": [
                    {
                        "op": "must",
                        "field": "uri",
                        "conds": ["viking://resources/demo"],
                        "para": "-d=1",
                    },
                    {"op": "must", "field": "search_tags", "conds": ["env=prod"]},
                    {
                        "op": "range",
                        "field": "created_at",
                        "gte": "2026-01-01T00:00:00Z",
                    },
                ],
            },
            limit=10,
            output_fields=["uri", "search_tags"],
        )
        assert [record["id"] for record in filtered] == ["closest"]
        assert adapter.count() == 2
        assert (
            adapter.query(
                query_vector=[1.0, 0.0, 0.0],
                filter=Or([]),
                limit=100000,
                output_fields=["id"],
            )
            == []
        )

        class _Embedding:
            dimension = 3

        class _Cfg:
            embedding = _Embedding()

        with patch(
            "openviking.storage.vectordb_adapters.base.get_openviking_config",
            return_value=_Cfg(),
        ):
            assert adapter.delete(filter=Or([])) == 0
        assert adapter.count() == 2

        cursor = adapter._conn.cursor()
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = %s AND indexdef ILIKE '%%USING hnsw%%'",
            (collection_name,),
        )
        assert cursor.fetchone() is not None
        cursor.close()

        assert adapter.delete(ids=["farther"]) == 1
        assert adapter.count() == 1
    finally:
        _drop_quietly(adapter)


def test_opengauss_reconnect_rebuilds_distance_operator_class():
    collection_name = f"ov_it_{uuid.uuid4().hex[:12]}"
    original = _build_adapter(collection_name, distance_metric="cosine")
    rebuilt = None
    try:
        assert original.create_collection(
            collection_name,
            _context_schema(collection_name),
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )
        original.upsert(
            [
                {
                    "id": "item",
                    "uri": "viking://resources/demo/item",
                    "vector": [1.0, 0.0, 0.0],
                    "account_id": "acct-1",
                }
            ]
        )
        cursor = original._conn.cursor()
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename=%s AND indexname=%s",
            (collection_name, f"idx_{collection_name}_default_vec"),
        )
        assert "vector_cosine_ops" in cursor.fetchone()[0]
        cursor.close()
        original.close()
        original = None

        rebuilt = _build_adapter(collection_name, distance_metric="l2")
        rebuilt.get_collection()
        cursor = rebuilt._conn.cursor()
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename=%s AND indexname=%s",
            (collection_name, f"idx_{collection_name}_default_vec"),
        )
        definition = cursor.fetchone()[0]
        cursor.close()
        assert "vector_l2_ops" in definition
        records = rebuilt.get(["item"])
        assert isinstance(records[0]["vector"], list)
        assert records[0]["vector"] == [1.0, 0.0, 0.0]
    finally:
        _drop_quietly(rebuilt)
        _drop_quietly(original)


def test_opengauss_long_collection_name_creates_bounded_index():
    collection_name = ("c" + uuid.uuid4().hex + "x" * 20)[:50]
    assert len(collection_name) == 50
    adapter = _build_adapter(collection_name)
    try:
        assert adapter.create_collection(
            collection_name,
            _context_schema(collection_name),
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )
        adapter.upsert(
            [
                {
                    "id": "item",
                    "uri": "viking://resources/demo/item",
                    "vector": [1.0, 0.0, 0.0],
                }
            ]
        )
        cursor = adapter._conn.cursor()
        cursor.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname=current_schema() AND tablename=%s",
            (collection_name,),
        )
        index_names = [row[0] for row in cursor.fetchall()]
        cursor.close()
        assert index_names
        assert all(len(name) <= 63 for name in index_names)
        raw_vector_index = f"idx_{collection_name}_default_vec"
        assert len(raw_vector_index) > 63
        assert raw_vector_index not in index_names
    finally:
        _drop_quietly(adapter)
