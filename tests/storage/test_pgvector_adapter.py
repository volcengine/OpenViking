# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import types

import pytest
from pydantic import ValidationError

from openviking.storage.vectordb.collection.collection import ICollection
from openviking.storage.vectordb_adapters import pgvector_adapter as pgvector_module
from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking.storage.vectordb_adapters.opengauss_adapter import (
    _safe_identifier,
    _vector_literal,
)
from openviking.storage.vectordb_adapters.pgvector_adapter import (
    PgVectorCollection,
    PgVectorCollectionAdapter,
    _normalize_distance,
)
from openviking_cli.utils.config.vectordb_config import (
    PgVectorConfig,
    VectorDBBackendConfig,
)


def _build_config() -> VectorDBBackendConfig:
    return VectorDBBackendConfig.model_validate(
        {
            "backend": "pgvector",
            "project": "default",
            "name": "context",
            "index_name": "default",
            "distance_metric": "cosine",
            "pgvector": {
                "host": "127.0.0.1",
                "port": 5432,
                "user": "postgres",
                "password": "postgres",
                "db_name": "postgres",
                "schema": "public",
                "dense_vector_name": "vector",
                "sparse_vector_name": "sparse_vector",
            },
        }
    )


def test_pgvector_backend_config_validation():
    config = _build_config()

    assert config.backend == "pgvector"
    assert config.pgvector is not None
    assert isinstance(config.pgvector, PgVectorConfig)
    assert config.pgvector.host == "127.0.0.1"
    assert config.pgvector.port == 5432
    assert config.pgvector.db_name == "postgres"
    assert config.pgvector.schema_name == "public"


def test_vector_literal_and_identifier_safety():
    assert _vector_literal([1, 2.5, float("nan")]) == "[1,2.5,0]"

    name = _safe_identifier("Project/With Space", "Context.Table", prefix="ov")
    assert name.startswith("ov_project_with_space_context_table")
    assert len(name.encode("utf-8")) <= 63

    # PgVectorCollection re-targets OpenGaussCollection and is-a ICollection.
    assert issubclass(PgVectorCollection, ICollection)


@pytest.mark.parametrize(
    ("metric", "valid"),
    [
        ("cosine", True),
        ("l2", True),
        ("ip", True),
        ("dot", False),
        ("euclid", False),
    ],
    ids=["cosine", "l2", "ip", "reject-dot", "reject-euclid"],
)
def test_pgvector_distance_validation(metric, valid):
    if valid:
        assert _normalize_distance(metric) == metric
    else:
        with pytest.raises(ValueError, match="supports only cosine, l2, and ip"):
            _normalize_distance(metric)


@pytest.mark.parametrize(
    ("create_extension", "expect_ext"),
    [(True, True), (False, False)],
    ids=["create-extension", "pre-provisioned"],
)
def test_create_extension_emitted_before_vector_ddl(create_extension, expect_ext):
    collection = object.__new__(PgVectorCollection)
    collection._schema_name = "public"
    collection._table_name = "ov_test"
    collection._dense_vector_name = "vector"
    collection._vector_dim = 3
    collection._create_extension = create_extension

    meta = {
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 3},
        ]
    }
    statements = collection._build_create_ddl(meta)

    assert any("vector(3)" in s for s in statements)
    if expect_ext:
        assert statements[0] == "CREATE EXTENSION IF NOT EXISTS vector"
        ext_idx = next(i for i, s in enumerate(statements) if "CREATE EXTENSION" in s)
        vec_idx = next(i for i, s in enumerate(statements) if "vector(3)" in s)
        assert ext_idx < vec_idx
    else:
        assert all("CREATE EXTENSION" not in s for s in statements)


@pytest.mark.parametrize(
    ("distance", "opclass"),
    [
        ("cosine", "vector_cosine_ops"),
        ("l2", "vector_l2_ops"),
        ("ip", "vector_ip_ops"),
    ],
    ids=["cosine", "l2", "ip"],
)
def test_vector_index_creation_supports_hnsw(distance, opclass):
    collection = object.__new__(PgVectorCollection)
    collection._schema_name = "public"
    collection._table_name = "ov_test"
    collection._dense_vector_name = "vector"
    statements: list[str] = []
    collection._all_columns = lambda: ["id", "vector"]
    collection._execute = lambda sql, params=None, fetch=False: statements.append(sql)

    collection._create_vector_index(
        "default",
        distance,
        {
            "VectorIndex": {
                "IndexType": "hnsw",
                "Distance": distance,
                "M": 24,
                "EfConstruction": 128,
            }
        },
    )

    sql = " ".join(statements).lower()
    assert "using hnsw" in sql
    assert opclass in sql
    assert "m = 24" in sql
    assert "ef_construction = 128" in sql


def test_vector_search_binds_vector_before_filter_params():
    collection = object.__new__(PgVectorCollection)
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


@pytest.mark.parametrize(
    ("columns", "values", "expect_update"),
    [
        (["id", "content", "vector"], ["doc-1", "hi", "[0.1,0.2]"], True),
        (["id"], ["doc-1"], False),
    ],
    ids=["multi-col-do-update", "id-only-do-nothing"],
)
def test_upsert_uses_on_conflict(columns, values, expect_update):
    collection = object.__new__(PgVectorCollection)
    collection._schema_name = "public"
    collection._table_name = "ov_test"
    collection._dense_vector_name = "vector"
    captured = {}
    collection._table_ref = lambda: '"public"."ov_test"'

    def execute(sql, params=None, *, fetch=False):
        captured["sql"] = sql
        captured["params"] = params
        return []

    collection._execute = execute
    collection._upsert_row(columns, values)

    sql = captured["sql"]
    assert "INSERT INTO" in sql
    assert captured["params"] == values
    if expect_update:
        assert "ON CONFLICT (id) DO UPDATE SET" in sql
        assert '"content" = EXCLUDED."content"' in sql
        assert '"vector" = EXCLUDED."vector"' in sql
        assert "%s::vector" in sql  # dense vector cast in VALUES
        assert 'EXCLUDED."id"' not in sql  # id is the conflict key, never updated
    else:
        assert "ON CONFLICT (id) DO NOTHING" in sql


@pytest.mark.parametrize(
    ("payload", "expected_fragments", "expected_params"),
    [
        ({"op": "must", "field": "account_id", "conds": ["acme"]}, ['"account_id" = %s'], ["acme"]),
        (
            {"op": "must", "field": "account_id", "conds": ["a", "b"]},
            ['"account_id" IN (%s, %s)'],
            ["a", "b"],
        ),
        (
            {"op": "must", "field": "scope_roots", "conds": ["/resources/acme/docs"]},
            ['"scope_roots" LIKE %s'],
            ["%\n/resources/acme/docs\n%"],
        ),
        (
            {
                "op": "range",
                "field": "updated_at",
                "gte": "2026-05-01T00:00:00+00:00",
                "lt": "2026-06-01T00:00:00+00:00",
            },
            ['"updated_at" >= %s', '"updated_at" < %s'],
            ["2026-05-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00"],
        ),
        (
            {
                "op": "time_range",
                "field": "updated_at",
                "gte": "2026-05-01T00:00:00+00:00",
                "lte": "2026-06-01T00:00:00+00:00",
            },
            ['"updated_at" >= %s', '"updated_at" <= %s'],
            ["2026-05-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00"],
        ),
        (
            {"op": "contains", "field": "abstract", "substring": "report"},
            ['"abstract" LIKE %s'],
            ["%report%"],
        ),
        ({"op": "prefix", "field": "uri", "prefix": "/r"}, ['"uri" LIKE %s'], ["/r%"]),
        (
            {
                "op": "and",
                "conds": [
                    {"op": "must", "field": "account_id", "conds": ["acme"]},
                    {"op": "contains", "field": "abstract", "substring": "report"},
                ],
            },
            ['"account_id" = %s', '"abstract" LIKE %s', " AND "],
            ["acme", "%report%"],
        ),
    ],
    ids=["eq", "in", "scope_roots", "range", "time_range", "contains", "prefix", "and"],
)
def test_collection_filter_to_sql(payload, expected_fragments, expected_params):
    collection = object.__new__(PgVectorCollection)
    collection._field_types = {"updated_at": "date_time"}

    clause, params = collection._compile_filter(payload)

    for fragment in expected_fragments:
        assert fragment in clause
    assert params == expected_params


@pytest.mark.parametrize(
    ("supported", "expect_guc"),
    [(True, True), (False, False)],
    ids=["v0.8-iterative-scan", "pre-0.8-fallback"],
)
def test_iterative_scan_set_when_filtered(supported, expect_guc):
    collection = object.__new__(PgVectorCollection)
    collection._schema_name = "public"
    collection._table_name = "ov_test"
    collection._dense_vector_name = "vector"
    collection._distance_metric = "cosine"
    collection._iterative_scan_supported = supported
    collection._select_columns = lambda output_fields, include_sparse=False: ["id"]
    collection._where_sql = lambda filters: (' WHERE "scope_roots" LIKE %s', ["%\n/a\n%"])
    collection._table_ref = lambda: '"public"."ov_test"'
    collection._row_to_payload = lambda row, cols: (row[0], {})
    captured = {}

    def execute(sql, params=None, *, fetch=False):
        captured["sql"] = sql
        captured["params"] = params
        return [(f"id-{i}", 0.1) for i in range(3)]  # full LIMIT of rows

    collection._execute = execute

    result = collection.search_by_vector(
        "default",
        dense_vector=[0.1, 0.2],
        limit=3,
        filters={"op": "must", "field": "scope_roots", "conds": ["/a"]},
    )

    sql = captured["sql"]
    if expect_guc:
        assert "SET LOCAL hnsw.iterative_scan = strict_order" in sql
        assert "SET LOCAL hnsw.ef_search" in sql
        assert "SET LOCAL hnsw.max_scan_tuples" in sql
        assert "SET LOCAL enable_seqscan = off" in sql
    else:
        assert "SET LOCAL hnsw.iterative_scan" not in sql

    # B3.5 param order preserved regardless of the GUC prefix.
    assert captured["params"] == ["[0.1,0.2]", "%\n/a\n%", "[0.1,0.2]", 3, 0]
    # Full LIMIT returned under a selective filter.
    assert len(result.data) == 3


@pytest.mark.parametrize(
    ("pgvector_cfg", "sslmode", "expect_url"),
    [
        ({"host": "10.0.0.5", "sslmode": "require"}, "require", False),
        ({"url": "postgresql://u:p@h:5432/db", "sslmode": "prefer"}, "prefer", True),
    ],
    ids=["discrete-require", "url-prefer"],
)
def test_sslmode_passed_to_connect(monkeypatch, pgvector_cfg, sslmode, expect_url):
    captured = {}

    class FakeConn:
        closed = 0

    def fake_connect(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeConn()

    monkeypatch.setattr(
        pgvector_module,
        "_import_psycopg2",
        lambda: types.SimpleNamespace(connect=fake_connect),
        raising=False,
    )

    config = VectorDBBackendConfig.model_validate({"backend": "pgvector", "pgvector": pgvector_cfg})
    adapter = create_collection_adapter(config)
    adapter._connect()

    assert captured["kwargs"]["sslmode"] == sslmode
    if expect_url:
        assert captured["args"][0] == "postgresql://u:p@h:5432/db"
    else:
        assert captured["kwargs"]["host"] == "10.0.0.5"


class _FakeCursor:
    def __init__(self, extversion):
        self._extversion = extversion
        self.executed = None
        self.closed = False

    def execute(self, sql, params=None):
        self.executed = sql

    def fetchone(self):
        return (self._extversion,) if self._extversion is not None else None

    def close(self):
        self.closed = True


class _FakeConn:
    closed = 0

    def __init__(self, extversion):
        self._extversion = extversion
        self.last_cursor = None

    def cursor(self):
        self.last_cursor = _FakeCursor(self._extversion)
        return self.last_cursor


@pytest.mark.parametrize(
    ("extversion", "hnsw", "halfvec", "iterative"),
    [
        ("0.8.2", True, True, True),
        ("0.7.0", True, True, False),
        ("0.5.1", True, False, False),
    ],
    ids=["v0.8-iterative", "v0.7-halfvec", "v0.5-hnsw-only"],
)
def test_version_gate_reads_extversion(extversion, hnsw, halfvec, iterative):
    adapter = create_collection_adapter(_build_config())
    adapter._conn = _FakeConn(extversion)

    adapter._detect_version()

    assert "extversion" in adapter._conn.last_cursor.executed
    assert "extname" in adapter._conn.last_cursor.executed
    assert adapter._supports_hnsw is hnsw
    assert adapter._supports_halfvec is halfvec
    assert adapter._supports_iterative_scan is iterative
    assert adapter._pgvector_version == extversion


def test_version_gate_rejects_pre_hnsw_pgvector():
    adapter = create_collection_adapter(_build_config())
    adapter._conn = _FakeConn("0.4.4")

    with pytest.raises(RuntimeError, match="0.5"):
        adapter._detect_version()


def test_factory_creates_pgvector_adapter_without_connecting():
    adapter = create_collection_adapter(_build_config())

    assert isinstance(adapter, PgVectorCollectionAdapter)
    assert adapter.mode == "pgvector"
    assert adapter.collection_name == "context"
    assert adapter.index_name == "default"
    assert adapter.physical_table_name == "ov_default_context"


def test_from_config_reads_top_level_index_distance_dimension():
    config = VectorDBBackendConfig.model_validate(
        {
            "backend": "pgvector",
            "project": "acme",
            "name": "docs",
            "index_name": "hnsw_idx",
            "distance_metric": "l2",
            "dimension": 384,
            "pgvector": {"host": "127.0.0.1"},
        }
    )
    adapter = create_collection_adapter(config)

    assert adapter.index_name == "hnsw_idx"
    assert adapter._distance_metric == "l2"
    assert adapter._dimension == 384
    assert adapter.physical_table_name == "ov_acme_docs"


def test_pgvector_config_new_field_defaults():
    pg = _build_config().pgvector

    assert pg.url is None
    assert pg.sslmode == "prefer"
    assert pg.index_type == "hnsw"
    assert pg.index_params == {}
    assert pg.pool_size == 1
    assert pg.create_extension is True


def test_pgvector_backend_requires_url_or_host():
    # A url-only config validates (discrete host cleared).
    url_only = VectorDBBackendConfig.model_validate(
        {
            "backend": "pgvector",
            "pgvector": {"url": "postgresql://u:p@db.example:5432/app", "host": None},
        }
    )
    assert url_only.pgvector.url == "postgresql://u:p@db.example:5432/app"

    # A discrete-field config (host, no url) also validates.
    discrete = VectorDBBackendConfig.model_validate(
        {"backend": "pgvector", "pgvector": {"host": "10.0.0.1"}}
    )
    assert discrete.pgvector.host == "10.0.0.1"

    # Neither url nor host is a hard error.
    with pytest.raises(ValidationError, match="requires 'url' or 'host'"):
        VectorDBBackendConfig.model_validate(
            {"backend": "pgvector", "pgvector": {"url": None, "host": None}}
        )


def test_pgvector_backend_url_priority_and_whitespace_normalization():
    # url wins when both are set; both are stripped.
    both = VectorDBBackendConfig.model_validate(
        {
            "backend": "pgvector",
            "pgvector": {"url": "  postgresql://h/db  ", "host": "  10.0.0.2  "},
        }
    )
    assert both.pgvector.url == "postgresql://h/db"
    assert both.pgvector.host == "10.0.0.2"

    # Whitespace-only url + empty host normalizes to empty -> clear error.
    with pytest.raises(ValidationError, match="requires 'url' or 'host'"):
        VectorDBBackendConfig.model_validate(
            {"backend": "pgvector", "pgvector": {"url": "   ", "host": ""}}
        )
