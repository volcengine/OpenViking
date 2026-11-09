# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import os
import uuid
from urllib.parse import unquote, urlparse

import pytest

from openviking.storage.expr import Or
from openviking.storage.vectordb_adapters.opengauss_adapter import OpenGaussCollectionAdapter
from openviking_cli.utils.config.vectordb_config import OpenGaussConfig

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
    adapter_args = {
        key: kwargs.pop(key) for key in ("collection_name", "index_name", "distance_metric")
    }
    return OpenGaussCollectionAdapter(**adapter_args, config=OpenGaussConfig(**kwargs))


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


def test_opengauss_context_retrieval_and_mutation_contract():
    collection_name = f"ov_it_{uuid.uuid4().hex[:12]}"
    adapter = _build_adapter(collection_name)
    try:
        schema = _context_schema(collection_name)
        schema["FullText"] = [{"Field": "content"}]
        assert adapter.create_collection(
            collection_name,
            schema,
            distance="cosine",
            sparse_weight=0.5,
            index_name="default",
        )
        adapter.upsert(
            [
                {
                    "id": "closest",
                    "uri": "viking://resources/demo/closest",
                    "vector": [1.0, 0.0, 0.0],
                    "sparse_vector": {"alpha": 0.8},
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "level": 1,
                    "search_tags": ["env=prod", "team=search"],
                    "abstract": "closest",
                    "content": "retrieval database",
                    "account_id": "acct-1",
                },
                {
                    "id": "farther",
                    "uri": "viking://resources/demo/farther",
                    "vector": [0.0, 1.0, 0.0],
                    "sparse_vector": {"alpha": 2.0},
                    "created_at": 1767225602000,
                    "updated_at": 1768435200000,
                    "level": 2,
                    "search_tags": ["env=dev"],
                    "abstract": "farther",
                    "content": "retrieval database",
                    "account_id": "acct-1",
                },
                {
                    "id": "private",
                    "uri": "viking://resources/private",
                    "vector": [1.0, 0.0, 0.0],
                    "sparse_vector": {"alpha": 100.0},
                    "content": "retrieval database",
                    "account_id": "acct-2",
                },
            ]
        )
        scope = {"op": "must", "field": "account_id", "conds": ["acct-1"]}

        fetched = adapter.get(["closest"])
        assert fetched[0]["created_at"] == 1767225600000
        assert fetched[0]["search_tags"] == ["env=prod", "team=search"]
        assert fetched[0]["sparse_vector"] == {"alpha": 0.8}
        assert fetched[0]["content"] == "retrieval database"
        assert isinstance(fetched[0]["vector"], list)
        assert fetched[0]["vector"] == [1.0, 0.0, 0.0]

        projected = adapter.query(
            query_vector=[1.0, 0.0, 0.0],
            filter=scope,
            limit=1,
            output_fields=["id", "uri", "content", "vector", "account_id"],
        )
        assert projected[0]["id"] == "closest"
        assert projected[0]["content"] == "retrieval database"
        assert isinstance(projected[0]["vector"], list)
        assert projected[0]["vector"] == [1.0, 0.0, 0.0]

        results = adapter.query(
            query_vector=[1.0, 0.0, 0.0],
            filter=scope,
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
        assert adapter.count() == 3
        assert (
            adapter.query(
                query_vector=[1.0, 0.0, 0.0],
                filter=Or([]),
                limit=100000,
                output_fields=["id"],
            )
            == []
        )

        assert adapter.delete(filter=Or([])) == 0
        assert adapter.count() == 3

        assert [
            row["id"]
            for row in adapter.search_by_keywords(query="retrieval", filter=scope, limit=1)
        ] == ["closest"]
        assert [
            row["id"]
            for row in adapter.search_by_keywords(
                query="retrieval", filter=scope, limit=1, offset=1
            )
        ] == ["farther"]
        sparse = adapter.query(sparse_query_vector={"alpha": 1.0}, filter=scope, limit=2)
        assert [row["id"] for row in sparse] == ["farther", "closest"]
        assert [row["_score"] for row in sparse] == pytest.approx([2.0, 0.8])
        decayed = adapter.query(
            query_vector=[1.0, 0.0, 0.0],
            sparse_query_vector={"alpha": 1.0},
            filter=scope,
            limit=1,
            offset=1,
            advance={"time_decay": {"protection": "0", "origin": "2026-01-15T00:00:00Z"}},
        )
        assert decayed[0]["id"] == "closest"
        assert decayed[0]["_origin_score"] == pytest.approx(0.9)
        assert decayed[0]["_time_score"] == pytest.approx(0.25)
        assert decayed[0]["_score"] == pytest.approx(0.225)

        before = {row["id"]: row for row in adapter.get(["closest", "farther"])}
        with pytest.raises(ValueError, match="negative sparse weights"):
            adapter.update_data(
                [
                    {"id": "closest", "content": "must not persist"},
                    {"id": "farther", "sparse_vector": {"alpha": -1.0}},
                ]
            )
        assert {row["id"]: row for row in adapter.get(["closest", "farther"])} == before

        adapter.update_data([{"id": "closest", "content": ""}])
        assert adapter.get(["closest"])[0]["content"] == ""
        assert len(adapter.query(sparse_query_vector={"alpha": 1.0}, filter=scope)) == 2
        adapter.update_data([{"id": "closest", "sparse_vector": {}}])
        assert [
            row["id"] for row in adapter.query(sparse_query_vector={"alpha": 1.0}, filter=scope)
        ] == ["farther"]

        assert adapter.delete(filter={"op": "must", "field": "id", "conds": ["farther"]}) == 1
        assert adapter.search_by_keywords(query="retrieval", filter=scope) == []
        assert adapter.query(sparse_query_vector={"alpha": 1.0}, filter=scope) == []
        assert adapter.get(["private"])[0]["content"] == "retrieval database"
        assert adapter.delete(ids=["closest"]) == 1
        assert adapter.count() == 1
    finally:
        _drop_quietly(adapter)


def test_opengauss_reconnect_preserves_collection_data_and_indexes():
    collection_name = f"ov_it_{uuid.uuid4().hex[:12]}"
    original = _build_adapter(collection_name, distance_metric="cosine")
    reopened = None
    try:
        schema = _context_schema(collection_name)
        schema["FullText"] = [{"Field": "content"}]
        assert original.create_collection(
            collection_name,
            schema,
            distance="cosine",
            sparse_weight=0.5,
            index_name="default",
        )
        original.upsert(
            [
                {
                    "id": "item",
                    "uri": "viking://resources/demo/item",
                    "vector": [1.0, 0.0, 0.0],
                    "sparse_vector": {"alpha": 0.8},
                    "content": "retrieval persisted",
                    "account_id": "acct-1",
                }
            ]
        )
        cursor = original._conn.cursor()
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname=current_schema() AND tablename=%s ORDER BY indexname",
            (collection_name,),
        )
        indexes = cursor.fetchall()
        assert any("vector_cosine_ops" in definition for _, definition in indexes)
        assert any("USING bm25" in definition for _, definition in indexes)
        original._conn.commit()
        cursor.close()
        original.close()
        original = None

        reopened = _build_adapter(collection_name, distance_metric="cosine")
        reopened.get_collection()
        cursor = reopened._conn.cursor()
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname=current_schema() AND tablename=%s ORDER BY indexname",
            (collection_name,),
        )
        assert cursor.fetchall() == indexes
        reopened._conn.commit()
        cursor.close()
        records = reopened.get(["item"])
        assert isinstance(records[0]["vector"], list)
        assert records[0]["vector"] == [1.0, 0.0, 0.0]
        assert records[0]["content"] == "retrieval persisted"
        assert [row["id"] for row in reopened.search_by_keywords(query="retrieval")] == ["item"]
        assert [row["id"] for row in reopened.query(sparse_query_vector={"alpha": 1.0})] == ["item"]
    finally:
        _drop_quietly(reopened)
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
            "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() AND tablename=%s",
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
