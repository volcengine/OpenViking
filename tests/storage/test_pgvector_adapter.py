# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import os
import shutil
import types
import uuid

import pytest
from pydantic import ValidationError

from openviking.storage.vectordb.collection.collection import Collection, ICollection
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


@pytest.mark.parametrize(
    ("pool_size", "expect_pool"),
    [(1, False), (3, True)],
    ids=["single-conn", "threaded-pool"],
)
def test_register_vector_optional(monkeypatch, pool_size, expect_pool):
    connect_calls = []
    pool_calls = {}

    class _Conn:
        closed = 0

    def fake_connect(*args, **kwargs):
        connect_calls.append((args, kwargs))
        return _Conn()

    class _FakePool:
        def __init__(self, minconn, maxconn, *args, **kwargs):
            pool_calls["maxconn"] = maxconn
            pool_calls["args"] = args
            pool_calls["kwargs"] = kwargs

        def getconn(self):
            return _Conn()

    # No register_vector attribute on the fake driver: the %s::vector literal
    # path must work without any driver-level type binding.
    fake_psycopg2 = types.SimpleNamespace(
        connect=fake_connect,
        pool=types.SimpleNamespace(ThreadedConnectionPool=_FakePool),
    )
    monkeypatch.setattr(pgvector_module, "_import_psycopg2", lambda: fake_psycopg2, raising=False)

    config = VectorDBBackendConfig.model_validate(
        {"backend": "pgvector", "pgvector": {"host": "10.0.0.9", "pool_size": pool_size}}
    )
    adapter = create_collection_adapter(config)
    conn = adapter._connect()

    assert conn is not None
    if expect_pool:
        assert adapter._pool is not None
        assert pool_calls["maxconn"] == pool_size
        assert connect_calls == []  # pool path, no direct connect
    else:
        assert adapter._pool is None
        assert len(connect_calls) == 1


class _ExtCursor:
    def __init__(self, fail_create, ext_present):
        self._fail_create = fail_create
        self._ext_present = ext_present
        self._last = None
        self.create_attempted = False

    def execute(self, sql, params=None):
        self._last = sql
        if "CREATE EXTENSION" in sql:
            self.create_attempted = True
            if self._fail_create:
                raise RuntimeError("permission denied to create extension vector")

    def fetchone(self):
        if self._last and "pg_extension" in self._last:
            return (1,) if self._ext_present else None
        return None

    def close(self):
        pass


class _ExtConn:
    closed = 0

    def __init__(self, fail_create, ext_present):
        self._fail_create = fail_create
        self._ext_present = ext_present
        self.cursors = []

    def cursor(self):
        cur = _ExtCursor(self._fail_create, self._ext_present)
        self.cursors.append(cur)
        return cur

    def commit(self):
        pass

    def rollback(self):
        pass


@pytest.mark.parametrize(
    ("create_extension", "fail_create", "ext_present", "expect_raise", "expect_attempt"),
    [
        (True, False, False, False, True),
        (True, True, False, True, True),
        (True, True, True, False, True),
        (False, False, False, False, False),
    ],
    ids=["creates-ok", "fail-absent-raises", "fail-present-ok", "disabled-skips"],
)
def test_create_extension_failure_is_actionable(
    create_extension, fail_create, ext_present, expect_raise, expect_attempt
):
    config = VectorDBBackendConfig.model_validate(
        {
            "backend": "pgvector",
            "pgvector": {"host": "127.0.0.1", "create_extension": create_extension},
        }
    )
    adapter = create_collection_adapter(config)
    conn = _ExtConn(fail_create, ext_present)
    adapter._conn = conn

    if expect_raise:
        with pytest.raises(RuntimeError, match="CREATE EXTENSION vector"):
            adapter._ensure_extension()
    else:
        adapter._ensure_extension()

    assert any(c.create_attempted for c in conn.cursors) is expect_attempt


class _RecordingCursor:
    def __init__(self, log):
        self._log = log
        self._last = None

    def execute(self, sql, params=None):
        self._log.append(sql)
        self._last = sql

    def fetchone(self):
        if self._last and "extversion" in self._last:
            return ("0.8.2",)
        if self._last and "pg_extension" in self._last:
            return (1,)
        return None  # information_schema table-exists -> absent

    def fetchall(self):
        return []

    def close(self):
        pass


class _RecordingConn:
    closed = 0

    def __init__(self, log):
        self._log = log

    def cursor(self):
        return _RecordingCursor(self._log)

    def commit(self):
        pass

    def rollback(self):
        pass


def _fake_psycopg2(log):
    return types.SimpleNamespace(
        connect=lambda *a, **k: _RecordingConn(log),
        pool=types.SimpleNamespace(ThreadedConnectionPool=lambda *a, **k: None),
    )


def test_create_backend_collection_wires_pgvector_live_flow(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr(
        pgvector_module, "_import_psycopg2", lambda: _fake_psycopg2(log), raising=False
    )
    adapter = create_collection_adapter(_build_config())
    meta = {
        "CollectionName": "context",
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 2},
        ],
    }

    collection = adapter._create_backend_collection(meta)

    assert isinstance(collection, Collection)
    joined = " ".join(log)
    assert "__openviking_pgvector_collections" in joined
    assert "__openviking_pgvector_indexes" in joined
    assert "vector(2)" in joined
    # version gate ran during _ensure_ready (fake extversion 0.8.2)
    assert adapter._supports_iterative_scan is True


def test_new_collection_threads_feature_flags(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr(
        pgvector_module, "_import_psycopg2", lambda: _fake_psycopg2(log), raising=False
    )
    adapter = create_collection_adapter(_build_config())

    raw = adapter._new_collection()

    assert isinstance(raw, pgvector_module.PgVectorCollection)
    assert raw._iterative_scan_supported is True  # extversion 0.8.2 -> >= 0.8
    assert raw._create_extension is True
    assert raw._ef_search == 100
    assert raw._max_scan_tuples == 20000


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


# --- review-feedback regression tests (PR #18) ---------------------------------


class _PoolConn:
    def __init__(self, closed=0):
        self.closed = closed
        self.close_called = False

    def close(self):
        self.close_called = True
        self.closed = 1


class _RecordingPool:
    def __init__(self):
        self.putconn_calls = []
        self.closeall_called = False
        self.served = []

    def getconn(self):
        conn = _PoolConn()
        self.served.append(conn)
        return conn

    def putconn(self, conn, close=False):
        self.putconn_calls.append((conn, close))

    def closeall(self):
        self.closeall_called = True


def _pooled_adapter(monkeypatch, pool):
    fake_psycopg2 = types.SimpleNamespace(
        connect=lambda *a, **k: _PoolConn(),
        pool=types.SimpleNamespace(ThreadedConnectionPool=lambda *a, **k: pool),
    )
    monkeypatch.setattr(pgvector_module, "_import_psycopg2", lambda: fake_psycopg2, raising=False)
    config = VectorDBBackendConfig.model_validate(
        {"backend": "pgvector", "pgvector": {"host": "127.0.0.1", "pool_size": 3}}
    )
    return create_collection_adapter(config)


def test_import_psycopg2_exposes_pool_submodule():
    # `import psycopg2` does NOT auto-import psycopg2.pool; the helper must, or
    # ThreadedConnectionPool access AttributeErrors when pool_size > 1.
    pytest.importorskip("psycopg2")
    psycopg2 = pgvector_module._import_psycopg2()
    assert hasattr(psycopg2, "pool")
    assert hasattr(psycopg2.pool, "ThreadedConnectionPool")


def test_stale_pooled_connection_returned_via_putconn(monkeypatch):
    pool = _RecordingPool()
    adapter = _pooled_adapter(monkeypatch, pool)

    first = adapter._connect()
    assert adapter._pool is pool
    first.closed = 1  # simulate a dropped connection
    second = adapter._connect()

    assert (first, True) in pool.putconn_calls  # returned to pool, not just .close()'d
    assert first.close_called is False
    assert second is not first


def test_close_returns_pooled_conn_and_closes_pool(monkeypatch):
    pool = _RecordingPool()
    adapter = _pooled_adapter(monkeypatch, pool)
    conn = adapter._connect()

    adapter.close()

    assert (conn, True) in pool.putconn_calls
    assert pool.closeall_called is True
    assert adapter._conn is None
    assert adapter._pool is None
    assert adapter._ready is False


def test_close_single_connection_path(monkeypatch):
    monkeypatch.setattr(
        pgvector_module,
        "_import_psycopg2",
        lambda: types.SimpleNamespace(connect=lambda *a, **k: _PoolConn(), pool=None),
        raising=False,
    )
    adapter = create_collection_adapter(_build_config())  # pool_size default 1
    conn = adapter._connect()

    adapter.close()

    assert conn.close_called is True
    assert adapter._conn is None


def test_ensure_ready_bootstraps_once(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr(
        pgvector_module, "_import_psycopg2", lambda: _fake_psycopg2(log), raising=False
    )
    adapter = create_collection_adapter(_build_config())

    adapter._ensure_ready()
    adapter._ensure_ready()

    assert adapter._ready is True
    # extension/version/meta bootstrap ran exactly once despite two calls.
    assert sum("CREATE EXTENSION" in s for s in log) == 1


def test_detect_version_raises_when_extension_absent():
    adapter = create_collection_adapter(_build_config())
    adapter._conn = _FakeConn(None)  # no pg_extension row -> extension missing

    with pytest.raises(RuntimeError, match="not installed"):
        adapter._detect_version()


def _meta_collection():
    collection = object.__new__(PgVectorCollection)
    collection._schema_name = "public"
    collection._table_name = "ov_default_context"
    collection._logical_collection_name = "context"
    collection._project_name = "default"
    return collection


def test_save_collection_meta_uses_on_conflict_not_merge():
    collection = _meta_collection()
    captured = {}
    collection._execute = lambda sql, params=None, fetch=False: captured.update(
        sql=sql, params=params
    )

    collection._save_collection_meta({"CollectionName": "context"})

    sql = captured["sql"]
    assert "MERGE INTO" not in sql
    assert "INSERT INTO" in sql
    assert "ON CONFLICT (table_name) DO UPDATE SET" in sql
    assert captured["params"][0] == "ov_default_context"


def test_save_index_meta_uses_on_conflict_not_merge():
    collection = _meta_collection()
    captured = {}
    collection._execute = lambda sql, params=None, fetch=False: captured.update(
        sql=sql, params=params
    )

    collection._save_index_meta("default", {"IndexName": "default"})

    sql = captured["sql"]
    assert "MERGE INTO" not in sql
    assert "ON CONFLICT (table_name, index_name) DO UPDATE SET" in sql
    assert captured["params"][:2] == ["ov_default_context", "default"]


def test_update_data_rejects_missing_ids():
    collection = object.__new__(PgVectorCollection)
    collection.fetch_data = lambda ids: types.SimpleNamespace(ids_not_exist=["missing-1"])
    collection.upsert_data = lambda data_list: pytest.fail("must not upsert when ids are missing")

    with pytest.raises(ValueError, match="record not found"):
        collection.update_data([{"id": "missing-1", "content": "x"}])


def test_update_data_upserts_when_all_present():
    collection = object.__new__(PgVectorCollection)
    seen = {}

    def _fake_upsert(data_list):
        seen["data"] = data_list
        return "OK"

    collection.fetch_data = lambda ids: types.SimpleNamespace(ids_not_exist=[])
    collection.upsert_data = _fake_upsert

    result = collection.update_data([{"id": "a", "content": "x"}])

    assert result == "OK"
    assert seen["data"] == [{"id": "a", "content": "x"}]


def test_update_data_requires_primary_key():
    collection = object.__new__(PgVectorCollection)
    collection.fetch_data = lambda ids: types.SimpleNamespace(ids_not_exist=[])

    with pytest.raises(ValueError, match="primary key 'id' is required"):
        collection.update_data([{"content": "x"}])


@pytest.mark.parametrize(
    ("params", "expect_m", "expect_ef"),
    [
        ({"m": 24, "ef_construction": 128}, 24, 128),
        ({"M": 32, "EfConstruction": 200}, 32, 200),
    ],
    ids=["lowercase", "camelcase"],
)
def test_index_params_reach_default_index_meta(params, expect_m, expect_ef):
    config = VectorDBBackendConfig.model_validate(
        {"backend": "pgvector", "pgvector": {"host": "127.0.0.1", "index_params": params}}
    )
    adapter = create_collection_adapter(config)

    meta = adapter._build_default_index_meta(
        index_name="default",
        distance="cosine",
        use_sparse=False,
        sparse_weight=0.0,
        scalar_index_fields=[],
    )

    assert meta["VectorIndex"]["M"] == expect_m
    assert meta["VectorIndex"]["EfConstruction"] == expect_ef


def test_default_index_meta_omits_build_params_without_config():
    adapter = create_collection_adapter(_build_config())

    meta = adapter._build_default_index_meta(
        index_name="default",
        distance="cosine",
        use_sparse=False,
        sparse_weight=0.0,
        scalar_index_fields=[],
    )

    assert "M" not in meta["VectorIndex"]
    assert "EfConstruction" not in meta["VectorIndex"]


def test_coerce_int_actionable_error():
    assert pgvector_module._coerce_int(None, "m", 16) == 16
    assert pgvector_module._coerce_int("24", "m", 16) == 24
    with pytest.raises(ValueError, match=r"index_params\['m'\]"):
        pgvector_module._coerce_int("big", "m", 16)


@pytest.mark.parametrize("field", ["dense_vector_name", "sparse_vector_name"])
def test_pgvector_config_rejects_blank_vector_name(field):
    with pytest.raises(ValidationError, match="must not be empty"):
        VectorDBBackendConfig.model_validate(
            {"backend": "pgvector", "pgvector": {"host": "127.0.0.1", field: "   "}}
        )


def test_pgvector_config_rejects_non_int_index_params():
    with pytest.raises(ValidationError, match="must be an integer"):
        VectorDBBackendConfig.model_validate(
            {
                "backend": "pgvector",
                "pgvector": {"host": "127.0.0.1", "index_params": {"m": "big"}},
            }
        )


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


_SMOKE_HOST_ENV = {
    "pgvector": "OPENVIKING_PGVECTOR_HOST",
    "opengauss": "OPENVIKING_OPENGAUSS_HOST",
}


def _smoke_config(backend, project):
    base = {
        "backend": backend,
        "project": project,
        "name": "context",
        "index_name": "default",
        "distance_metric": "cosine",
        "dimension": 3,
    }
    if backend == "pgvector":
        base["pgvector"] = {
            "host": os.getenv("OPENVIKING_PGVECTOR_HOST", "127.0.0.1"),
            "port": int(os.getenv("OPENVIKING_PGVECTOR_PORT", "15432")),
            "user": os.getenv("OPENVIKING_PGVECTOR_USER", "postgres"),
            "password": os.getenv("OPENVIKING_PGVECTOR_PASSWORD", "postgres"),
            "db_name": os.getenv("OPENVIKING_PGVECTOR_DB", "postgres"),
            "schema": os.getenv("OPENVIKING_PGVECTOR_SCHEMA", "public"),
        }
    elif backend == "opengauss":
        base["opengauss"] = {
            "host": os.getenv("OPENVIKING_OPENGAUSS_HOST", "127.0.0.1"),
            "port": int(os.getenv("OPENVIKING_OPENGAUSS_PORT", "5432")),
            "user": os.getenv("OPENVIKING_OPENGAUSS_USER", "omm"),
            "password": os.getenv("OPENVIKING_OPENGAUSS_PASSWORD", ""),
            "db_name": os.getenv("OPENVIKING_OPENGAUSS_DB", "postgres"),
            "schema": os.getenv("OPENVIKING_OPENGAUSS_SCHEMA", "public"),
            "mode": os.getenv("OPENVIKING_OPENGAUSS_MODE", "standalone"),
        }
    return VectorDBBackendConfig.model_validate(base)


# A golden fixture whose nearest neighbour to the query [1, 0, 0] is unambiguous:
# doc-a is identical, doc-b is close, doc-c is orthogonal — all under the same
# scope so a scope_roots filter keeps all three.
_SMOKE_QUERY = [1.0, 0.0, 0.0]
_SMOKE_RECORDS = [
    ("doc-a", "viking://resources/acme/docs/a.md", [1.0, 0.0, 0.0]),
    ("doc-b", "viking://resources/acme/docs/b.md", [0.8, 0.2, 0.0]),
    ("doc-c", "viking://resources/acme/docs/c.md", [0.0, 1.0, 0.0]),
]
_SMOKE_META = {
    "CollectionName": "context",
    "Fields": [
        {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
        {"FieldName": "uri", "FieldType": "path"},
        {"FieldName": "vector", "FieldType": "vector", "Dim": 3},
        {"FieldName": "abstract", "FieldType": "string"},
    ],
}


def _run_pgvector_smoke(adapter):
    collection = adapter._new_collection(_SMOKE_META)
    try:
        collection.create_remote_collection(_SMOKE_META)
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
                    {"id": rid, "uri": uri, "vector": vec, "abstract": rid}
                )
                for rid, uri, vec in _SMOKE_RECORDS
            ]
        )

        result = collection.search_by_vector(
            "default",
            dense_vector=_SMOKE_QUERY,
            limit=3,
            filters={"op": "must", "field": "scope_roots", "conds": ["/resources/acme/docs"]},
        )
        count = collection.aggregate_data("default")

        ids = [item.id for item in result.data]
        assert ids[0] == "doc-a", ids  # exact nearest
        assert ids == ["doc-a", "doc-b", "doc-c"], ids  # cosine-distance order
        assert len(result.data) == 3  # min(K, limit) under a selective filter
        assert count.agg["_total"] == 3
    finally:
        collection.drop()
        adapter.close()


@pytest.mark.skipif(
    not os.getenv("OPENVIKING_PGVECTOR_HOST"),
    reason="set OPENVIKING_PGVECTOR_HOST to run the pgvector integration smoke test",
)
def test_pgvector_adapter_integration_smoke():
    suffix = uuid.uuid4().hex[:8]
    adapter = create_collection_adapter(_smoke_config("pgvector", f"pytest_{suffix}"))
    _run_pgvector_smoke(adapter)


@pytest.fixture(scope="module")
def pgvector_container():
    """A self-contained pgvector server started by testcontainers.

    Unlike the ``OPENVIKING_PGVECTOR_HOST`` tests (which target an externally-run
    container), this spins up ``pgvector/pgvector:pg16`` itself and tears it down
    after the module. Opt-in via ``OPENVIKING_PGVECTOR_TESTCONTAINERS`` so the
    default ``pytest tests/storage`` run never pulls an image; skips gracefully
    if Docker or testcontainers is unavailable. The adapter creates the ``vector``
    extension on connect (``create_extension=true``), so no init SQL is needed.
    """
    if not os.getenv("OPENVIKING_PGVECTOR_TESTCONTAINERS"):
        pytest.skip("set OPENVIKING_PGVECTOR_TESTCONTAINERS=1 to run the testcontainers smoke")
    if shutil.which("docker") is None:
        pytest.skip("docker is not available for the testcontainers pgvector run")
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed (pip install 'openviking[test]')")

    try:
        container = PostgresContainer(
            "pgvector/pgvector:pg16", username="postgres", password="postgres", dbname="postgres"
        )
        container.start()
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"could not start pgvector testcontainer: {exc}")

    try:
        yield container
    finally:
        container.stop()


def test_pgvector_integration_smoke_testcontainers(pgvector_container):
    config = VectorDBBackendConfig.model_validate(
        {
            "backend": "pgvector",
            "project": f"pytest_tc_{uuid.uuid4().hex[:8]}",
            "name": "context",
            "index_name": "default",
            "distance_metric": "cosine",
            "dimension": 3,
            "pgvector": {
                "host": pgvector_container.get_container_host_ip(),
                "port": int(pgvector_container.get_exposed_port(5432)),
                "user": "postgres",
                "password": "postgres",
                "db_name": "postgres",
                "schema": "public",
            },
        }
    )
    _run_pgvector_smoke(create_collection_adapter(config))


@pytest.mark.parametrize("backend", ["opengauss", "pgvector"], ids=["opengauss", "pgvector"])
def test_cross_backend_smoke_parity(backend):
    """The same golden fixture + assertions must hold for both SQL backends,
    proving openGauss<->pgvector parity. Each backend skips unless its host env
    var is set, so this runs against whichever container(s) are available."""
    host_env = _SMOKE_HOST_ENV[backend]
    if not os.getenv(host_env):
        pytest.skip(f"set {host_env} to run the {backend} parity smoke")
    suffix = uuid.uuid4().hex[:8]
    try:
        adapter = create_collection_adapter(_smoke_config(backend, f"pytest_{suffix}"))
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"{backend} adapter unavailable: {exc}")
    _run_pgvector_smoke(adapter)
