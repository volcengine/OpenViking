# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real pgvector integration tests. Set OV_PGVECTOR_TEST_DSN to opt in."""

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from openviking.storage.expr import And, Eq, Or, PathScope
from openviking.storage.vectordb_adapters import pgvector_adapter
from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking_cli.exceptions import FailedPreconditionError
from openviking_cli.utils.config.vectordb_config import PgvectorConfig, VectorDBBackendConfig


@pytest.fixture(params=["flat", "hnsw"])
def adapter(request):
    if not os.environ.get("OV_PGVECTOR_TEST_DSN"):
        pytest.skip("Set OV_PGVECTOR_TEST_DSN for real PostgreSQL tests")
    a = create_collection_adapter(
        VectorDBBackendConfig(
            backend="pgvector",
            name="test_pg_" + uuid.uuid4().hex[:12],
            dimension=3,
            sparse_weight=0.5,
            pgvector={"dsn_env": "OV_PGVECTOR_TEST_DSN", "index_type": request.param},
        )
    )
    schema = {
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 3},
            {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
            {"FieldName": "uri", "FieldType": "path"},
            {"FieldName": "account_id", "FieldType": "string"},
            {"FieldName": "updated_at", "FieldType": "date_time"},
            {"FieldName": "search_tags", "FieldType": "list<string>"},
            {"FieldName": "content", "FieldType": "text"},
        ],
        "ScalarIndex": ["account_id", "uri"],
    }
    try:
        a.create_collection(
            a.collection_name, schema, distance="cosine", sparse_weight=0.5, index_name="default"
        )
        yield a
    finally:
        a.drop_collection()
        a.close()


def seed(a):
    a.upsert(
        [
            {
                "id": "a",
                "vector": [1, 0, 0],
                "sparse_vector": {"hello": 2},
                "uri": "viking://resources/a.md",
                "account_id": "one",
                "updated_at": "2026-11-12T00:00:00Z",
                "search_tags": ["abc"],
                "content": "complete content",
            },
            {
                "id": "b",
                "vector": [0, 1, 0],
                "sparse_vector": {"hello": 1},
                "uri": "viking://resources/sub/b.md",
                "account_id": "two",
            },
            {
                "id": "c",
                "vector": [0, 0, 1],
                "sparse_vector": {"other": 1},
                "uri": "viking://resources/sub/c.md",
                "account_id": "one",
            },
        ]
    )


def test_crud_filter_reload(adapter):
    seed(adapter)
    assert adapter.count() == 3
    assert adapter.get(["a"])[0]["vector"] == [1, 0, 0]
    assert adapter.get(["a"])[0]["content"] == "complete content"
    scope = And([Eq("account_id", "one"), PathScope("uri", "viking://resources", -1)])
    result = adapter.query(query_vector=[1, 0, 0], filter=scope, limit=10)
    assert [r["id"] for r in result] == ["a", "c"]
    assert result[0]["_score"] == pytest.approx(1)
    assert adapter.query(query_vector=[1, 0, 0], filter=Or([])) == []
    assert adapter.count(Eq("search_tags", "abc")) == 1
    adapter.update_data([{"id": "a", "account_id": "changed"}])
    assert adapter.get(["a"])[0]["vector"] == [1, 0, 0]
    adapter._collection = None
    assert adapter.collection_exists()
    assert adapter.query(query_vector=[1, 0, 0])[0]["id"] == "a"
    assert adapter.delete(filter=Eq("account_id", "two")) == 1
    assert adapter.count() == 2


def test_sparse_hybrid_and_atomic_batch(adapter):
    seed(adapter)
    assert adapter.query(sparse_query_vector={"hello": 1})[0]["id"] == "a"
    assert adapter.query(query_vector=[1, 0, 0], sparse_query_vector={"hello": 1})[0]["id"] == "a"
    adapter.upsert({"id": "a", "sparse_vector": {"new": 2}})
    assert [r["id"] for r in adapter.query(sparse_query_vector={"hello": 1})] == ["b"]
    with pytest.raises(ValueError):
        adapter.upsert([{"id": "good", "vector": [1, 0, 0]}, {"id": "bad", "vector": [1, 0]}])
    assert adapter.get(["good"]) == []
    adapter.delete(ids=["b"])
    assert adapter.query(sparse_query_vector={"hello": 1}) == []


def test_dense_equal_distance_pagination(adapter):
    ids = [f"tie_{i:02d}" for i in range(12)]
    adapter.upsert([{"id": item_id, "vector": [1, 0, 0]} for item_id in reversed(ids)])
    for reload in (False, True):
        if reload:
            adapter._collection = None
        pages = [
            adapter.query(query_vector=[1, 0, 0], limit=3, offset=offset)
            for offset in range(0, len(ids), 3)
        ]
        assert [row["id"] for page in pages for row in page] == ids


def test_dense_missing_index_raises_failed_precondition(adapter):
    with pytest.raises(FailedPreconditionError, match="missing or invalid"):
        adapter.get_collection().search_by_vector(index_name="missing", dense_vector=[1, 0, 0])


def test_pgvector_config_rejects_4096_hnsw():
    with pytest.raises(ValueError, match="2000"):
        VectorDBBackendConfig(backend="pgvector", dimension=4096, pgvector={"index_type": "hnsw"})


def test_4096_flat_roundtrip():
    if not os.environ.get("OV_PGVECTOR_TEST_DSN"):
        pytest.skip("Set OV_PGVECTOR_TEST_DSN for real PostgreSQL tests")
    config = VectorDBBackendConfig(
        backend="pgvector",
        name="test_pg_" + uuid.uuid4().hex[:12],
        dimension=4096,
        pgvector={"dsn_env": "OV_PGVECTOR_TEST_DSN"},
    )
    a = create_collection_adapter(config)
    try:
        schema = {
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4096},
            ]
        }
        a.create_collection(
            a.collection_name, schema, distance="cosine", sparse_weight=0, index_name="default"
        )
        vector = [1.0] + [0.0] * 4095
        a.upsert({"id": "wide", "vector": vector})
        assert a.get(["wide"])[0]["vector"] == vector
        assert a.query(query_vector=vector)[0]["_score"] == pytest.approx(1)
        a._collection = None
        assert a.query(query_vector=vector)[0]["id"] == "wide"
    finally:
        a.drop_collection()
        a.close()


@pytest.mark.parametrize(
    "settings",
    [
        {"connection_pool_min_size": 3, "connection_pool_max_size": 2},
        {"m": 40, "ef_construction": 64},
        {"ef_search": 0},
        {"candidate_limit": 10001},
        {"index_type": "ivfflat"},
        {"dsn_env": ""},
        {"unknown_option": True},
        {"filtered_knn": "off"},
        {"filtered_knn": "auto"},
        {"filtered_knn": "required"},
        {"unfiltered_use_native": False},
        {"unfiltered_use_native": True},
        {"unfiltered_use_native": "false"},
        {"unfiltered_use_native": 1},
        {"search_implementation": "auto"},
        {"search_implementation": "off"},
        {"search_implementation": "required"},
        {"search_implementation": True},
        {"search_implementation": None},
    ],
)
def test_invalid_pgvector_settings(settings):
    with pytest.raises(ValueError):
        PgvectorConfig(**settings)


def test_search_implementation_default_and_choices():
    assert PgvectorConfig().search_implementation == "native"
    for implementation in ("native", "enhanced"):
        assert (
            PgvectorConfig(search_implementation=implementation).search_implementation
            == implementation
        )


@pytest.fixture
def dense_pagination_collection(monkeypatch):
    collection = pgvector_adapter.PgvectorCollection.__new__(pgvector_adapter.PgvectorCollection)
    collection._name = "pagination"
    collection._dim = 3
    collection._array_fields = set()
    collection._search_implementation = "enhanced"
    collection._filtered_knn_schema = "public"
    collection._filtered_knn_options = PgvectorConfig().filtered_knn_options
    collection._indexes = {
        "default": {
            "_pg_index_type": "hnsw",
            "_distance": "cosine",
            "_pg_index_name": "pagination_idx",
        }
    }
    monkeypatch.setattr(collection, "has_index", Mock(return_value=True))
    monkeypatch.setattr(collection, "_validate_ranked_filters", Mock())
    monkeypatch.setattr(collection, "_normalize_filter_date_times", lambda filters: filters)
    monkeypatch.setattr(collection, "_resolve_distance_and_op", lambda name: ("cosine", "<=>"))
    monkeypatch.setattr(collection, "_select_output_columns", lambda fields: "id")
    monkeypatch.setattr(collection, "_query_rows", Mock(return_value=(["id", "_ov_distance"], [])))
    return collection


@pytest.mark.parametrize("mode", ["adaptive", "exact"])
@pytest.mark.parametrize("offset", [99991, 2**31])
def test_enhanced_dense_pagination_rejects_excessive_budget(
    dense_pagination_collection, mode, offset
):
    collection = dense_pagination_collection
    collection._filtered_knn_options = PgvectorConfig(
        filtered_knn_options={"mode": mode}
    ).filtered_knn_options
    with pytest.raises(ValueError, match=r"candidate budget.*100000.*limit \+ offset"):
        collection.search_by_vector("default", [1, 0, 0], limit=10, offset=offset)
    collection._query_rows.assert_not_called()


@pytest.mark.parametrize("offset", [100, 99990])
def test_enhanced_dense_pagination_accepts_budget_and_raises_width(
    dense_pagination_collection, offset
):
    collection = dense_pagination_collection
    collection.search_by_vector("default", [1, 0, 0], limit=10, offset=offset)
    sql, params, _ = collection._query_rows.call_args.args
    assert "ov_filtered_knn(" in sql
    assert params[3] == 10 + offset
    assert json.loads(params[4])["search_width"] == 10 + offset
    assert params[-2:] == [10, offset]


def test_native_dense_pagination_does_not_apply_extension_budget(
    dense_pagination_collection,
):
    collection = dense_pagination_collection
    collection._search_implementation = "native"
    collection._filtered_knn_schema = None
    collection.search_by_vector("default", [1, 0, 0], limit=10, offset=100000)
    sql, params, _ = collection._query_rows.call_args.args
    assert "ov_filtered_knn(" not in sql
    assert params[-2:] == [10, 100000]


@pytest.mark.parametrize("implementation", ["native", "enhanced"])
@pytest.mark.parametrize("mode", ["adaptive", "exact"])
@pytest.mark.parametrize(
    "filters",
    [None, {}, {"op": "must", "field": "account_id", "conds": ["tenant-a"]}],
)
def test_search_implementation_independent_of_filters(
    dense_pagination_collection, implementation, mode, filters
):
    collection = dense_pagination_collection
    collection._search_implementation = implementation
    collection._filtered_knn_options = PgvectorConfig(
        filtered_knn_options={"mode": mode}
    ).filtered_knn_options
    collection.search_by_vector("default", [1, 0, 0], limit=10, filters=filters)
    sql, params, _ = collection._query_rows.call_args.args
    assert ("ov_filtered_knn(" in sql) == (implementation == "enhanced")
    if implementation == "enhanced":
        assert params[2] == (None if filters is None else json.dumps(filters))
    elif filters:
        assert '"account_id"' in sql
        assert "tenant-a" in params

    # Hybrid and time-decay reuse this candidate hook.
    ctes, params = collection._dense_candidate_ctes("default", "[1,0,0]", 20, filters)
    sql = " ".join(ctes)
    assert ("ov_filtered_knn(" in sql) == (implementation == "enhanced")
    if implementation == "enhanced":
        assert params[2] == (None if filters is None else json.dumps(filters))
    elif filters:
        assert '"account_id"' in sql
        assert "tenant-a" in params


@pytest.mark.parametrize("unsupported", ["capability", "flat", "l1"])
def test_enhanced_search_rejects_unavailable_or_unsupported(
    dense_pagination_collection, unsupported
):
    collection = dense_pagination_collection
    if unsupported == "capability":
        collection._filtered_knn_schema = None
    elif unsupported == "flat":
        collection._indexes["default"]["_pg_index_type"] = "flat"
    else:
        collection._indexes["default"]["_distance"] = "l1"
    with pytest.raises(FailedPreconditionError, match="enhanced.*HNSW"):
        collection.search_by_vector("default", [1, 0, 0])
    with pytest.raises(FailedPreconditionError, match="enhanced.*HNSW"):
        collection._dense_candidate_ctes("default", "[1,0,0]", 20, None)
    collection._query_rows.assert_not_called()


@pytest.mark.parametrize(
    "row,version,error",
    [
        (None, None, "capability"),
        (("public", True, True, True), 2, "capability"),
        (("public", False, True, True), 1, "permissions"),
        (("public", True, False, True), 1, "permissions"),
        (("public", True, True, False), 1, "permissions"),
    ],
)
def test_enhanced_capability_probe_fails_explicitly(row, version, error):
    adapter = pgvector_adapter.PgvectorCollectionAdapter.__new__(
        pgvector_adapter.PgvectorCollectionAdapter
    )
    cursor = Mock()
    cursor.fetchone.side_effect = [row, (version,)]
    with pytest.raises(FailedPreconditionError, match=error):
        adapter._discover_filtered_knn(cursor)


def test_enhanced_dense_zero_limit_skips_budget_check(dense_pagination_collection):
    collection = dense_pagination_collection
    assert collection.search_by_vector("default", [1, 0, 0], limit=0, offset=100001).data == []
    collection._query_rows.assert_not_called()


def test_missing_driver_dsn(monkeypatch):
    monkeypatch.delenv("OV_PGVECTOR_MISSING_DSN", raising=False)
    with pytest.raises(ValueError, match="OV_PGVECTOR_MISSING_DSN"):
        create_collection_adapter(
            VectorDBBackendConfig(
                backend="pgvector",
                pgvector={"dsn_env": "OV_PGVECTOR_MISSING_DSN"},
            )
        )


@pytest.mark.parametrize(
    "failure_stage", ["proxy", "checkout", "cursor", "version", "metadata", "close"]
)
def test_connect_failure_closes_pool(monkeypatch, failure_stage):
    error = RuntimeError(f"{failure_stage} failed")
    cursor = Mock()
    cursor.fetchone.return_value = ("0.8.0",)
    connection = Mock(closed=False)
    connection.cursor.return_value = cursor
    pool = Mock()
    pool.getconn.return_value = connection
    driver = Mock()
    driver.pool.ThreadedConnectionPool.return_value = pool
    ensure_meta = Mock()
    if failure_stage == "proxy":
        monkeypatch.setattr(pgvector_adapter, "_PooledConnectionProxy", Mock(side_effect=error))
    elif failure_stage == "checkout":
        pool.getconn.side_effect = error
    elif failure_stage == "cursor":
        connection.cursor.side_effect = error
    elif failure_stage == "version":
        cursor.execute.side_effect = error
    elif failure_stage == "metadata":
        ensure_meta.side_effect = error
    else:
        cursor.close.side_effect = error
    monkeypatch.setenv("OV_PGVECTOR_TEST_DSN", "unused mock DSN")
    monkeypatch.setattr(pgvector_adapter, "_import_psycopg2", lambda: driver)
    monkeypatch.setattr(pgvector_adapter, "_ensure_meta_table", ensure_meta)
    adapter = pgvector_adapter.PgvectorCollectionAdapter.__new__(
        pgvector_adapter.PgvectorCollectionAdapter
    )
    adapter._config = PgvectorConfig(dsn_env="OV_PGVECTOR_TEST_DSN")

    with pytest.raises(RuntimeError) as caught:
        adapter._connect()
    assert caught.value is error
    pool.closeall.assert_called_once_with()
    assert adapter._conn is None
    assert adapter._pool is None
    if failure_stage not in {"proxy", "checkout", "cursor"}:
        cursor.close.assert_called_once_with()


def _make_mock_pgvector_driver():
    cursor = Mock()
    cursor.fetchone.return_value = ("0.8.0",)
    connection = Mock(closed=False)
    connection.cursor.return_value = cursor
    pool = Mock()
    pool.getconn.return_value = connection
    driver = Mock()
    driver.pool.ThreadedConnectionPool.return_value = pool
    return driver, pool, cursor


def test_split_field_mode_passes_password_verbatim(monkeypatch):
    driver, pool, _ = _make_mock_pgvector_driver()
    monkeypatch.setattr(pgvector_adapter, "_import_psycopg2", lambda: driver)
    monkeypatch.setattr(pgvector_adapter, "_ensure_meta_table", Mock())
    adapter = pgvector_adapter.PgvectorCollectionAdapter.__new__(
        pgvector_adapter.PgvectorCollectionAdapter
    )
    adapter._config = PgvectorConfig(
        host="pg.example.com", user="ov", password="p@ss:w/rd' #?", db_name="openviking"
    )
    adapter._connect()
    _, kwargs = driver.pool.ThreadedConnectionPool.call_args
    assert kwargs["password"] == "p@ss:w/rd' #?"
    assert kwargs["host"] == "pg.example.com"
    assert kwargs["dbname"] == "openviking"
    assert "dsn" not in kwargs


def test_split_field_mode_forwards_sslmode(monkeypatch):
    driver, pool, _ = _make_mock_pgvector_driver()
    monkeypatch.setattr(pgvector_adapter, "_import_psycopg2", lambda: driver)
    monkeypatch.setattr(pgvector_adapter, "_ensure_meta_table", Mock())
    adapter = pgvector_adapter.PgvectorCollectionAdapter.__new__(
        pgvector_adapter.PgvectorCollectionAdapter
    )
    adapter._config = PgvectorConfig(host="pg.example.com", sslmode="require")
    adapter._connect()
    _, kwargs = driver.pool.ThreadedConnectionPool.call_args
    assert kwargs["sslmode"] == "require"


def test_split_field_mode_omits_sslmode_when_none(monkeypatch):
    driver, pool, _ = _make_mock_pgvector_driver()
    monkeypatch.setattr(pgvector_adapter, "_import_psycopg2", lambda: driver)
    monkeypatch.setattr(pgvector_adapter, "_ensure_meta_table", Mock())
    adapter = pgvector_adapter.PgvectorCollectionAdapter.__new__(
        pgvector_adapter.PgvectorCollectionAdapter
    )
    adapter._config = PgvectorConfig(host="pg.example.com")
    adapter._connect()
    _, kwargs = driver.pool.ThreadedConnectionPool.call_args
    assert "sslmode" not in kwargs


@pytest.mark.parametrize("failure_stage", ["table", "metadata", "wrap"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_creation_cleanup_preserves_original_error(monkeypatch, failure_stage, cleanup_fails):
    creation_error = ValueError(f"{failure_stage} creation failed")
    cleanup_error = RuntimeError("cleanup failed")
    adapter = pgvector_adapter.PgvectorCollectionAdapter.__new__(
        pgvector_adapter.PgvectorCollectionAdapter
    )
    adapter._conn = Mock()
    adapter._collection_name = "test_pg_cleanup"
    adapter._index_type = "flat"
    adapter._distance_metric = "cosine"
    adapter._execute_cleanup = Mock(side_effect=cleanup_error if cleanup_fails else None)
    meta = {"Fields": [{"FieldName": "vector", "FieldType": "vector", "Dim": 3}]}

    def create_table(*args, on_table_created, **kwargs):
        on_table_created()
        if failure_stage == "table":
            raise creation_error

    save_meta = Mock(
        return_value=meta, side_effect=creation_error if failure_stage == "metadata" else None
    )
    adapter._wrap_collection = Mock(side_effect=creation_error if failure_stage == "wrap" else None)
    monkeypatch.setattr(pgvector_adapter, "_create_collection_table", create_table)
    monkeypatch.setattr(pgvector_adapter, "_save_collection_meta", save_meta)
    logger = Mock()
    monkeypatch.setattr(pgvector_adapter, "logger", logger)

    with pytest.raises(ValueError) as caught:
        adapter._create_backend_collection(meta)
    assert caught.value is creation_error
    adapter._execute_cleanup.assert_called_once_with()
    assert adapter._initializing_collection is None
    if cleanup_fails:
        from openviking.observability.log_safety import short_hash

        logger.info.assert_called_once()
        assert logger.info.call_args.args[1] == short_hash(adapter._collection_name)
    else:
        logger.info.assert_not_called()


def test_concurrent_initialization_and_writes(adapter):
    name = "test_pg_" + uuid.uuid4().hex[:12]
    config = VectorDBBackendConfig(
        backend="pgvector",
        name=name,
        dimension=3,
        pgvector={"dsn_env": "OV_PGVECTOR_TEST_DSN", "index_type": adapter._config.index_type},
    )

    def write(index):
        other = create_collection_adapter(config)
        try:
            created = other.create_collection(
                other.collection_name,
                adapter.get_collection_info(),
                distance="cosine",
                sparse_weight=0.5,
                index_name="default",
            )
            other.upsert({"id": f"thread_{index}", "vector": [1, 0, 0]})
            assert other.get([f"thread_{index}"])[0]["id"] == f"thread_{index}"
            return created
        finally:
            other.close()

    owner = create_collection_adapter(config)
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            created = list(executor.map(write, range(8)))
        assert sum(created) == 1
        assert owner.count() == 8
    finally:
        owner.drop_collection()
        owner.close()


def test_persisted_contract_mismatch(adapter):
    seed(adapter)
    for override, message in [
        ({"dimension": 4}, "dimension"),
        ({"distance_metric": "l2"}, "distance"),
        ({"index_name": "missing"}, "missing"),
        (
            {
                "pgvector": {
                    "dsn_env": "OV_PGVECTOR_TEST_DSN",
                    "index_type": "hnsw" if adapter._config.index_type == "flat" else "flat",
                }
            },
            "index_type",
        ),
    ]:
        config = {
            "backend": "pgvector",
            "name": adapter.collection_name,
            "dimension": 3,
            "pgvector": {
                "dsn_env": "OV_PGVECTOR_TEST_DSN",
                "index_type": adapter._config.index_type,
            },
            **override,
        }
        other = create_collection_adapter(VectorDBBackendConfig(**config))
        try:
            with pytest.raises((ValueError, RuntimeError), match=message):
                other.collection_exists()
        finally:
            other.close()


def test_sql_failure_rolls_back_batch_and_connection_survives(adapter):
    from psycopg2.errors import StringDataRightTruncation

    seed(adapter)
    # id is VARCHAR(256). This reaches PostgreSQL after the first row has
    # executed, so it tests the transaction rollback rather than prevalidation.
    with pytest.raises(StringDataRightTruncation):
        adapter.upsert(
            [{"id": "first", "vector": [1, 0, 0]}, {"id": "x" * 257, "vector": [1, 0, 0]}]
        )
    assert adapter.get(["first"]) == []
    assert adapter.count() == 3
    adapter.upsert({"id": "after", "vector": [1, 0, 0]})
    assert adapter.count() == 4


def test_ttl_rejected_without_writes(adapter):
    with pytest.raises(NotImplementedError, match="TTL"):
        adapter.get_collection().upsert_data([{"id": "ttl", "vector": [1, 0, 0]}], ttl=10)
    assert adapter.count() == 0


@pytest.mark.parametrize("adapter", ["hnsw"], indirect=True)
def test_hnsw_physical_plan_and_missing_index(adapter):
    seed(adapter)
    meta = adapter.get_collection().get_index_meta_data("default")
    name = meta["_pg_index_name"]
    cursor = adapter._conn.cursor()
    try:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(
            f'EXPLAIN SELECT id FROM "{adapter.collection_name}" '
            "ORDER BY vector <=> '[1,0,0]'::vector, id LIMIT 2"
        )
        assert name in "\n".join(row[0] for row in cursor.fetchall())
        adapter._conn.rollback()
        cursor.execute(f'DROP INDEX "{name}"')
        adapter._conn.commit()
        with pytest.raises(FailedPreconditionError, match="missing or invalid"):
            adapter.query(query_vector=[1, 0, 0])
        # Restore it so normal fixture cleanup can load the collection.
        cursor.execute(
            f'CREATE INDEX "{name}" ON "{adapter.collection_name}" '
            "USING hnsw (vector vector_cosine_ops)"
        )
        adapter._conn.commit()
    finally:
        cursor.close()


@pytest.mark.parametrize("physical_index", ["missing", "wrong_operator_class"])
@pytest.mark.parametrize("adapter", ["hnsw"], indirect=True)
def test_drop_collection_with_unavailable_hnsw_after_reload(adapter, physical_index):
    seed(adapter)
    collection = adapter.get_collection()._Collection__collection
    name = collection.get_index_meta_data("default")["_pg_index_name"]
    tables = [
        adapter.collection_name,
        collection._index_catalog_table(),
        collection._sparse_posting_table_name(),
    ]
    cursor = adapter._conn.cursor()
    try:
        cursor.execute(f'DROP INDEX "{name}"')
        if physical_index == "wrong_operator_class":
            cursor.execute(
                f'CREATE INDEX "{name}" ON "{adapter.collection_name}" '
                "USING hnsw (vector vector_l2_ops)"
            )
        adapter._conn.commit()
    finally:
        cursor.close()

    with pytest.raises(FailedPreconditionError, match="missing or invalid"):
        adapter.query(query_vector=[1, 0, 0])

    reloaded = create_collection_adapter(
        VectorDBBackendConfig(
            backend="pgvector",
            name=adapter.collection_name,
            dimension=3,
            pgvector={"dsn_env": "OV_PGVECTOR_TEST_DSN", "index_type": "hnsw"},
        )
    )
    try:
        with pytest.raises(RuntimeError, match="missing|invalid"):
            reloaded.query(query_vector=[1, 0, 0])
        assert reloaded.drop_collection() is True
        assert reloaded.drop_collection() is False
        assert reloaded.collection_exists() is False
        cursor = reloaded._conn.cursor()
        try:
            for table in tables:
                cursor.execute("SELECT to_regclass(%s)", (table,))
                assert cursor.fetchone()[0] is None
            cursor.execute(
                'SELECT 1 FROM "_ov_collection_meta" WHERE table_name = %s',
                (adapter.collection_name,),
            )
            assert cursor.fetchone() is None
        finally:
            cursor.close()
    finally:
        reloaded.close()


@pytest.mark.parametrize("metric", ["cosine", "l2", "ip", "l1"])
def test_distance_metrics(adapter, metric):
    name = "test_pg_" + uuid.uuid4().hex[:12]
    other = create_collection_adapter(
        VectorDBBackendConfig(
            backend="pgvector",
            name=name,
            dimension=3,
            distance_metric=metric,
            pgvector={"dsn_env": "OV_PGVECTOR_TEST_DSN", "index_type": adapter._config.index_type},
        )
    )
    try:
        schema = {
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 3},
            ]
        }
        other.create_collection(
            name, schema, distance=metric, sparse_weight=0, index_name="default"
        )
        other.upsert([{"id": "near", "vector": [1, 0, 0]}, {"id": "far", "vector": [0, 1, 0]}])
        rows = other.query(query_vector=[1, 0, 0])
        assert [row["id"] for row in rows] == ["near", "far"]
        assert rows[0]["_score"] > rows[1]["_score"]
    finally:
        other.drop_collection()
        other.close()


def test_cosine_guard_never_reads_scalar_rejected_vectors(adapter):
    adapter.upsert(
        [
            {"id": "mine", "vector": [1, 0, 0], "account_id": "one"},
            {"id": "zero", "vector": [0, 0, 0], "account_id": "one"},
            {"id": "foreign", "vector": [-1, 0, 0], "account_id": "two"},
        ]
    )
    collection = adapter.get_collection()._Collection__collection
    predicate, params = collection._dense_filter_clause("<=>", '"account_id" = %s', ["one"])
    cursor = collection._cursor()
    try:
        # A cheap norm function lets the planner evaluate an ordinary AND before
        # scalar filtering. CASE must protect scalar-rejected heap rows.
        cursor.execute(
            """CREATE FUNCTION pg_temp.ov_test_norm(v vector) RETURNS double precision
               LANGUAGE plpgsql COST 0.0001 AS $$
               BEGIN
                 IF v = '[-1,0,0]'::vector THEN
                   RAISE EXCEPTION 'read scalar-rejected vector';
                 END IF;
                 RETURN vector_norm(v);
               END $$"""
        )
        cursor.execute("SET LOCAL enable_indexscan = off")
        cursor.execute("SET LOCAL enable_bitmapscan = off")
        cursor.execute(
            f'SELECT id FROM "{adapter.collection_name}" WHERE '
            + predicate.replace("vector_norm(vector)", "pg_temp.ov_test_norm(vector)"),
            params,
        )
        assert cursor.fetchall() == [("mine",)]
    finally:
        collection._conn.rollback()
        cursor.close()
    assert [
        r["id"]
        for r in adapter.query(query_vector=[1, 0, 0], filter=Eq("account_id", "one"), limit=10)
    ] == ["mine"]


@pytest.mark.parametrize("offset", [0, 1, 2, 3])
def test_cosine_zero_vectors_after_dense_pagination(adapter, offset):
    adapter.upsert(
        [
            {"id": "first", "vector": [1, 0, 0], "account_id": "one"},
            {"id": "second", "vector": [0, 1, 0], "account_id": "one"},
            *[{"id": f"zero_{i}", "vector": [0, 0, 0], "account_id": "one"} for i in range(8)],
        ]
    )
    expected = ["first", "second"][offset : offset + 3]
    for advance in [None, {"time_decay": {"protection": "1d"}}]:
        rows = adapter.query(
            query_vector=[1, 0, 0],
            filter=Eq("account_id", "one"),
            limit=3,
            offset=offset,
            advance=advance,
        )
        assert [r["id"] for r in rows] == expected
    assert adapter.query(query_vector=[0, 0, 0], filter=Eq("account_id", "one")) == []


@pytest.mark.parametrize("depth", [-1, 0, 1])
def test_cosine_zero_vectors_scoped_pagination_and_ranked(adapter, depth):
    collection = adapter.get_collection()._Collection__collection
    path = "viking://resources/目录_%"
    records = [
        {
            "id": f"mine_{i:02d}",
            "vector": [1, i / 10, 0],
            "sparse_vector": {"hello": i + 1},
            "uri": path if i == 0 else path + ("/sub" if i % 2 else "") + f"/f{i}",
            "account_id": "one",
            "search_tags": ["allowed"],
            "updated_at": "2026-11-12T00:00:00Z",
        }
        for i in range(12)
    ]
    records += [
        {
            "id": "tenant",
            "vector": [1, 0, 0],
            "uri": path,
            "account_id": "two",
            "search_tags": ["allowed"],
        },
        {
            "id": "acl",
            "vector": [1, 0, 0],
            "uri": path,
            "account_id": "one",
            "search_tags": ["denied"],
        },
        {
            "id": "sibling",
            "vector": [1, 0, 0],
            "uri": path + "suffix/f",
            "account_id": "one",
            "search_tags": ["allowed"],
        },
        {"id": "null", "uri": path, "account_id": "one", "search_tags": ["allowed"]},
        {
            "id": "zero",
            "vector": [0, 0, 0],
            "sparse_vector": {"hello": 100},
            "uri": path,
            "account_id": "one",
            "search_tags": ["allowed"],
        },
    ]
    adapter.upsert(records)
    scope = And(
        [Eq("account_id", "one"), Eq("search_tags", "allowed"), PathScope("uri", path, depth)]
    )
    modes = [
        {},
        {"sparse_query_vector": {"hello": 1}},
        {"advance": {"time_decay": {"protection": "1d", "origin": "2026-11-15T00:00:00Z"}}},
    ]
    for mode in modes:
        actual = adapter.query(
            query_vector=[1, 0, 0], filter=scope, limit=3, offset=1, output_fields=["uri"], **mode
        )
        # Flat search on the same table is the oracle even with a physical HNSW
        # index. Restore metadata before the next query.
        index_meta = collection._indexes["default"]
        previous_type = index_meta["_pg_index_type"]
        try:
            index_meta["_pg_index_type"] = "flat"
            expected = adapter.query(
                query_vector=[1, 0, 0],
                filter=scope,
                limit=3,
                offset=1,
                output_fields=["uri"],
                **mode,
            )
        finally:
            index_meta["_pg_index_type"] = previous_type
        assert [r["id"] for r in actual] == [r["id"] for r in expected]
        assert [r["_score"] for r in actual] == pytest.approx([r["_score"] for r in expected])
        assert all(r["id"].startswith("mine_") for r in actual)
    if depth < 0:
        full = adapter.query(query_vector=[1, 0, 0], filter=scope, limit=20)
        assert [r["id"] for r in full] == [f"mine_{i:02d}" for i in range(12)]
        assert adapter.query(query_vector=[1, 0, 0], filter=scope, limit=3, offset=20) == []


@pytest.mark.parametrize("adapter", ["hnsw"], indirect=True)
def test_cosine_ranked_candidates_use_hnsw_without_forcing_planner(adapter):
    collection = adapter.get_collection()._Collection__collection
    meta = collection._indexes["default"]
    cursor = collection._cursor()
    try:
        # A tiny table favors a sequential scan. Use enough nonzero rows to
        # exercise natural HNSW selection, including PostgreSQL 16's planner.
        cursor.execute(
            f'INSERT INTO "{adapter.collection_name}" (id, vector, updated_at) '
            "SELECT 'plan_' || i, "
            "ARRAY[sin(i::double precision), cos(i::double precision), 1]::vector, "
            "(extract(epoch FROM '2026-11-12T00:00:00Z'::timestamptz) * 1000)::bigint "
            "FROM generate_series(1, 25000) i"
        )
        cursor.execute(f'ANALYZE "{adapter.collection_name}"')
        collection._conn.commit()
        collection._apply_search_params_on_cursor(cursor, "default")
        sql, params = collection._dense_candidate_query("default", "", [], "[1,0,0]", "<=>", 100)
        cursor.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params)
        plan = cursor.fetchone()[0][0]["Plan"]

        def nodes(node):
            yield node
            for child in node.get("Plans", []):
                yield from nodes(child)

        scans = [node for node in nodes(plan) if node.get("Index Name") == meta["_pg_index_name"]]
        assert len(scans) == 1, plan
        assert scans[0]["Node Type"] == "Index Scan"
        assert scans[0]["Actual Rows"] == 100
    finally:
        collection._conn.rollback()
        cursor.close()

    for mode in [
        {"sparse_query_vector": {"hello": 1}},
        {"advance": {"time_decay": {"protection": "1d", "origin": "2026-11-15T00:00:00Z"}}},
    ]:
        rows = adapter.query(query_vector=[1, 0, 0], limit=10, **mode)
        assert len(rows) == 10
        assert all(row["id"].startswith("plan_") for row in rows)
        assert [(-row["_score"], row["id"]) for row in rows] == sorted(
            (-row["_score"], row["id"]) for row in rows
        )


def test_cosine_zero_vectors_do_not_consume_hybrid_sparse_budget(adapter):
    adapter.upsert(
        [
            {"id": "dense", "vector": [1, 0, 0], "account_id": "one"},
            {
                "id": "sparse",
                "vector": [0, 1, 0],
                "account_id": "one",
                "sparse_vector": {"hello": 2},
            },
            {
                "id": "zero",
                "vector": [0, 0, 0],
                "account_id": "one",
                "sparse_vector": {"hello": 100},
            },
        ]
    )
    collection = adapter.get_collection()._Collection__collection
    collection.create_sparse_index(candidate_limit=1, sparse_weight=0.5)
    rows = adapter.query(
        query_vector=[1, 0, 0],
        sparse_query_vector={"hello": 1},
        filter=Eq("account_id", "one"),
        limit=1,
    )
    assert [r["id"] for r in rows] == ["sparse"]
    assert rows[0]["_score"] == pytest.approx(1)
    # A zero dense vector remains eligible when no cosine score is requested.
    rows = adapter.query(sparse_query_vector={"hello": 1}, filter=Eq("account_id", "one"), limit=1)
    assert [r["id"] for r in rows] == ["zero"]


@pytest.mark.asyncio
async def test_service_facade_context_schema_and_tenant_isolation():
    if not os.environ.get("OV_PGVECTOR_TEST_DSN"):
        pytest.skip("Set OV_PGVECTOR_TEST_DSN for real PostgreSQL tests")
    from openviking.storage.collection_schemas import CollectionSchemas
    from openviking.storage.viking_vector_index_backend import _SingleAccountBackend

    name = "test_pg_" + uuid.uuid4().hex[:12]
    config = VectorDBBackendConfig(
        backend="pgvector",
        name=name,
        dimension=3,
        pgvector={"dsn_env": "OV_PGVECTOR_TEST_DSN"},
    )
    backend = _SingleAccountBackend(config, bound_account_id="one")
    root = _SingleAccountBackend(config, bound_account_id=None, shared_adapter=backend._adapter)
    try:
        assert await backend.create_collection(name, CollectionSchemas.context_collection(name, 3))
        assert (
            await root.upsert(
                {
                    "id": "foreign",
                    "vector": [1, 0, 0],
                    "account_id": "two",
                    "uri": "viking://resources/foreign.md",
                },
            )
            == "foreign"
        )
        assert (
            await backend.upsert(
                {
                    "id": "mine",
                    "vector": [1, 0, 0],
                    "uri": "viking://resources/mine.md",
                    "content": "preserved body",
                    "search_tags": ["tag"],
                },
            )
            == "mine"
        )
        assert [row["id"] for row in await backend.query(query_vector=[1, 0, 0])] == ["mine"]
        assert await backend.get(["foreign"]) == []
        assert (await backend.get(["mine"]))[0]["content"] == "preserved body"
        assert len(await root.query(query_vector=[1, 0, 0])) == 2
        assert not await backend.create_collection(
            name, CollectionSchemas.context_collection(name, 3)
        )
    finally:
        backend._adapter.drop_collection()
        await root.close()
        await backend.close()
