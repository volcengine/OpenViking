# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import os
import uuid

import pytest

from openviking.storage.vectordb.collection.result import SearchResult
from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking.storage.vectordb_adapters.opengauss_adapter import (
    OpenGaussCollection,
    OpenGaussCollectionAdapter,
    _coerce_sql_value,
    _encode_scope_roots,
    _normalize_distance,
    _safe_identifier,
    _vector_literal,
)
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def _build_config() -> VectorDBBackendConfig:
    return VectorDBBackendConfig.model_validate(
        {
            "backend": "opengauss",
            "project": "default",
            "name": "context",
            "index_name": "default",
            "distance_metric": "cosine",
            "opengauss": {
                "host": "127.0.0.1",
                "port": 8888,
                "user": "omm",
                "password": "OpenGauss@123",
                "db_name": "postgres",
                "schema": "public",
                "mode": "standalone",
                "dense_vector_name": "vector",
                "sparse_vector_name": "sparse_vector",
            },
        }
    )


def test_opengauss_backend_config_validation():
    config = _build_config()

    assert config.backend == "opengauss"
    assert config.opengauss is not None
    assert config.opengauss.host == "127.0.0.1"
    assert config.opengauss.port == 8888
    assert config.opengauss.db_name == "postgres"
    assert config.opengauss.schema_name == "public"


def test_factory_creates_opengauss_adapter_without_connecting():
    adapter = create_collection_adapter(_build_config())

    assert isinstance(adapter, OpenGaussCollectionAdapter)
    assert adapter.mode == "opengauss"
    assert adapter.collection_name == "context"
    assert adapter.index_name == "default"
    assert adapter.physical_table_name == "ov_default_context"


def test_collection_filter_to_sql_for_path_scope():
    collection = object.__new__(OpenGaussCollection)
    collection._field_types = {"updated_at": "date_time"}

    clause, params = collection._compile_filter(
        {
            "op": "and",
            "conds": [
                {"op": "must", "field": "scope_roots", "conds": ["/resources/acme/docs"]},
                {
                    "op": "range",
                    "field": "updated_at",
                    "gte": "2026-05-01T00:00:00+00:00",
                    "lt": "2026-06-01T00:00:00+00:00",
                },
            ],
        }
    )

    assert '"scope_roots" LIKE %s' in clause
    assert '"updated_at" >= %s' in clause
    assert '"updated_at" < %s' in clause
    assert params == [
        "%\n/resources/acme/docs\n%",
        "2026-05-01T00:00:00+00:00",
        "2026-06-01T00:00:00+00:00",
    ]


def test_vector_literal_and_identifier_safety():
    assert _vector_literal([1, 2.5, float("nan")]) == "[1,2.5,0]"

    name = _safe_identifier("Project/With Space", "Context.Table", prefix="ov")
    assert name.startswith("ov_project_with_space_context_table")
    assert len(name.encode("utf-8")) <= 63


def test_opengauss_distance_validation_accepts_ip_and_rejects_dot():
    assert _normalize_distance("cosine") == "cosine"
    assert _normalize_distance("l2") == "l2"
    assert _normalize_distance("ip") == "ip"

    with pytest.raises(ValueError, match="supports only cosine, l2, and ip"):
        _normalize_distance("dot")

    with pytest.raises(ValueError, match="supports only cosine, l2, and ip"):
        _normalize_distance("euclid")


def test_distance_to_score_handles_inner_product_distance():
    assert OpenGaussCollection._distance_to_score(-0.75, "ip") == 0.75
    assert OpenGaussCollection._distance_to_score(0.25, "l2") == 0.8


def test_scope_roots_encoding_is_token_safe():
    encoded = _encode_scope_roots(["/a", "/a/b"])

    assert encoded == "\n/a\n/a/b\n"
    assert "\n/a\n" in encoded
    assert "\n/a/b\n" in encoded
    assert "\n/a/c\n" not in encoded


def test_bool_coercion_parses_string_values():
    assert _coerce_sql_value(True, "bool") is True
    assert _coerce_sql_value(False, "bool") is False
    assert _coerce_sql_value("true", "bool") is True
    assert _coerce_sql_value("1", "bool") is True
    assert _coerce_sql_value("false", "bool") is False
    assert _coerce_sql_value("0", "bool") is False
    assert _coerce_sql_value("unknown", "bool") is None


def test_drop_index_removes_vector_and_scalar_indexes():
    collection = object.__new__(OpenGaussCollection)
    collection._schema_name = "public"
    collection._table_name = "ov_test"
    statements = []

    collection.get_index_meta_data = lambda index_name: {
        "IndexName": index_name,
        "ScalarIndex": ["uri", "account_id"],
    }
    collection._delete_index_meta = lambda index_name: statements.append(
        ("delete_meta", index_name)
    )
    collection._execute = lambda sql, params=None, fetch=False: statements.append(sql)

    collection.drop_index("default")

    assert 'DROP INDEX IF EXISTS "public"."idx_ov_test_default_vec"' in statements
    assert 'DROP INDEX IF EXISTS "public"."idx_ov_test_default_uri"' in statements
    assert 'DROP INDEX IF EXISTS "public"."idx_ov_test_default_account_id"' in statements
    assert ("delete_meta", "default") in statements


def test_vector_index_creation_uses_hnsw_only():
    collection = object.__new__(OpenGaussCollection)
    collection._schema_name = "public"
    collection._table_name = "ov_test"
    collection._dense_vector_name = "vector"
    statements = []

    collection._table_ref = lambda: '"public"."ov_test"'
    collection._all_columns = lambda: ["id", "vector"]
    collection._execute = lambda sql, params=None, fetch=False: statements.append(sql)

    collection._create_vector_index(
        "default",
        "cosine",
        {"VectorIndex": {"IndexType": "flat", "Distance": "cosine"}},
    )

    sql = "\n".join(statements).lower()
    assert "using hnsw" in sql
    assert "vector_cosine_ops" in sql


def test_create_index_normalizes_metadata_to_hnsw():
    collection = object.__new__(OpenGaussCollection)
    saved = {}

    collection._create_vector_index = lambda index_name, distance, meta: saved.setdefault(
        "vector_meta", meta
    )
    collection._create_scalar_index = lambda index_name, field_name: None
    collection._save_index_meta = lambda index_name, meta: saved.setdefault("saved_meta", meta)

    collection.create_index(
        "default",
        {
            "IndexName": "default",
            "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            "ScalarIndex": ["uri"],
        },
    )

    assert saved["vector_meta"]["VectorIndex"]["IndexType"] == "hnsw"
    assert saved["saved_meta"]["VectorIndex"]["IndexType"] == "hnsw"


def test_create_index_does_not_save_metadata_when_vector_index_fails():
    collection = object.__new__(OpenGaussCollection)
    saved = {}

    def fail_vector_index(index_name, distance, meta):
        raise RuntimeError("boom")

    collection._create_vector_index = fail_vector_index
    collection._create_scalar_index = lambda index_name, field_name: saved.setdefault(
        "scalar", field_name
    )
    collection._save_index_meta = lambda index_name, meta: saved.setdefault("saved_meta", meta)

    with pytest.raises(RuntimeError, match="boom"):
        collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
                "ScalarIndex": ["uri"],
            },
        )

    assert saved == {}


def test_vector_search_binds_vector_before_filter_params():
    collection = object.__new__(OpenGaussCollection)
    collection._dense_vector_name = "vector"
    collection._distance_metric = "cosine"
    collection._select_columns = lambda output_fields, include_sparse=False: ["id"]
    collection._where_sql = lambda filters: (' WHERE "scope_roots" LIKE %s', ["%\n/a\n%"])
    collection._table_ref = lambda: '"public"."ov_test"'
    captured = {}

    def execute(sql, params=None, *, fetch=False):
        captured["sql"] = sql
        captured["params"] = params
        captured["fetch"] = fetch
        return []

    collection._execute = execute

    collection.search_by_vector(
        "default",
        dense_vector=[0.1, 0.2],
        filters={"op": "must", "field": "scope_roots", "conds": ["/a"]},
    )

    assert captured["fetch"] is True
    assert captured["params"] == ["[0.1,0.2]", "%\n/a\n%", "[0.1,0.2]", 10, 0]


@pytest.mark.skipif(
    not os.getenv("OPENVIKING_OPENGAUSS_HOST"),
    reason="set OPENVIKING_OPENGAUSS_HOST to run openGauss integration smoke test",
)
def test_opengauss_adapter_integration_smoke():
    suffix = uuid.uuid4().hex[:8]
    adapter = OpenGaussCollectionAdapter(
        host=os.environ["OPENVIKING_OPENGAUSS_HOST"],
        port=int(os.getenv("OPENVIKING_OPENGAUSS_PORT", "5432")),
        user=os.getenv("OPENVIKING_OPENGAUSS_USER", "omm"),
        password=os.getenv("OPENVIKING_OPENGAUSS_PASSWORD", ""),
        db_name=os.getenv("OPENVIKING_OPENGAUSS_DB", "postgres"),
        schema_name=os.getenv("OPENVIKING_OPENGAUSS_SCHEMA", "public"),
        project_name=f"pytest_{suffix}",
        collection_name="context",
        index_name="default",
        distance_metric="cosine",
        dense_vector_name="vector",
        sparse_vector_name="sparse_vector",
        connect_timeout=10,
        mode=os.getenv("OPENVIKING_OPENGAUSS_MODE", "standalone"),
        shard_count=4,
    )
    meta = {
        "CollectionName": "context",
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "uri", "FieldType": "path"},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 2},
            {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
            {"FieldName": "abstract", "FieldType": "string"},
        ],
    }

    collection = adapter._new_collection(meta)
    try:
        collection.create_remote_collection(meta)
        collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"},
                "ScalarIndex": ["uri", "parent_uri", "scope_roots"],
            },
        )
        collection.upsert_data(
            [
                adapter._normalize_record_for_write(
                    {
                        "id": "doc-1",
                        "uri": "viking://resources/acme/docs/a.md",
                        "vector": [0.1, 0.2],
                        "sparse_vector": {"quarter": 1.0},
                        "abstract": "quarterly report",
                    }
                ),
                adapter._normalize_record_for_write(
                    {
                        "id": "doc-2",
                        "uri": "viking://resources/acme/notes/b.md",
                        "vector": [0.2, 0.1],
                        "sparse_vector": {"notes": 1.0},
                        "abstract": "meeting notes",
                    }
                ),
            ]
        )

        result = collection.search_by_vector(
            "default",
            dense_vector=[0.1, 0.2],
            sparse_vector={"quarter": 1.0},
            limit=1,
            filters={"op": "must", "field": "scope_roots", "conds": ["/resources/acme/docs"]},
        )
        count = collection.aggregate_data("default")

        assert isinstance(result, SearchResult)
        assert [item.id for item in result.data] == ["doc-1"]
        assert count.agg["_total"] == 2
    finally:
        collection.drop()
        adapter.close()
