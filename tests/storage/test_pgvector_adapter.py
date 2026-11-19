# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real pgvector integration tests. Set OV_PGVECTOR_TEST_DSN to opt in."""

import os
import threading
import uuid
from collections import OrderedDict
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


@pytest.mark.parametrize("filtered", [False, True])
def test_dense_equal_distance_pagination(adapter, filtered):
    ids = [f"tie_{i:02d}" for i in range(12)]
    adapter.upsert(
        [{"id": item_id, "vector": [1, 0, 0], "account_id": "one"} for item_id in reversed(ids)]
    )
    for reload in (False, True):
        if reload:
            adapter._collection = None
        pages = [
            adapter.query(
                query_vector=[1, 0, 0],
                filter=Eq("account_id", "one") if filtered else None,
                limit=3,
                offset=offset,
            )
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
        {"filter_exact_threshold": -1},
        {"filter_exact_threshold": 10001},
        {"filter_exact_threshold": True},
        {"filter_exact_threshold": 1.5},
        {"index_type": "ivfflat"},
        {"dsn_env": ""},
        {"unknown_option": True},
    ],
)
def test_invalid_pgvector_settings(settings):
    with pytest.raises(ValueError):
        PgvectorConfig(**settings)


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


def test_hnsw_physical_plan_and_missing_index(adapter):
    if adapter._config.index_type != "hnsw":
        pytest.skip("Physical ANN index is specific to HNSW")
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
def test_drop_collection_with_unavailable_hnsw_after_reload(adapter, physical_index):
    if adapter._config.index_type != "hnsw":
        pytest.skip("Physical ANN index is specific to HNSW")
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
                {"FieldName": "account_id", "FieldType": "string"},
            ]
        }
        other.create_collection(
            name, schema, distance=metric, sparse_weight=0, index_name="default"
        )
        other.upsert(
            [
                {"id": "near", "vector": [1, 0, 0], "account_id": "one"},
                {"id": "far", "vector": [0, 1, 0], "account_id": "one"},
            ]
        )
        rows = other.query(query_vector=[1, 0, 0], filter=Eq("account_id", "one"))
        assert [row["id"] for row in rows] == ["near", "far"]
        assert rows[0]["_score"] > rows[1]["_score"]
    finally:
        other.drop_collection()
        other.close()


@pytest.mark.parametrize("threshold", [0, 1, 1000, 10000])
def test_filter_exact_threshold_config(threshold):
    assert PgvectorConfig(filter_exact_threshold=threshold).filter_exact_threshold == threshold


@pytest.mark.parametrize(
    "index_type,where,threshold",
    [
        ("flat", '"account_id" = %s', 5),
        ("hnsw", "", 5),
        ("hnsw", '"account_id" = %s', 0),
    ],
)
def test_nonadaptive_dense_query_has_no_probe(index_type, where, threshold):
    collection = pgvector_adapter.PgvectorCollection.__new__(pgvector_adapter.PgvectorCollection)
    collection._name = "test_pg"
    collection._indexes = {"default": {"_pg_index_type": index_type}}
    collection._retrieval_config = {"filter_exact_threshold": threshold}
    sql, params = collection._dense_candidate_query(
        "default", where, ["one"] if where else [], "[1,0,0]", "<=>", 10
    )
    assert "_ov_filter_probe" not in sql
    assert params == ["[1,0,0]"] + (["one"] if where else []) + [10]


@pytest.mark.parametrize(
    "probe_scan,native_index,admitted",
    [
        ({"Node Type": "Seq Scan", "Plan Rows": 1}, "ann", False),
        (
            {
                "Node Type": "Index Scan",
                "Index Name": "scalar",
                "Plan Rows": 10000,
                "Index Cond": "account_id = 'one'",
            },
            "ann",
            False,
        ),
        (
            {
                "Node Type": "Index Scan",
                "Index Name": "scalar",
                "Plan Rows": 1,
                "Filter": "uri = 'one'",
            },
            "ann",
            False,
        ),
        (
            {
                "Node Type": "Index Scan",
                "Index Name": "scalar",
                "Plan Rows": 1,
                "Index Cond": "account_id = 'one'",
            },
            "scalar",
            False,
        ),
        (
            {
                "Node Type": "Index Scan",
                "Index Name": "scalar",
                "Plan Rows": 1,
                "Index Cond": "account_id = 'one'",
            },
            "ann",
            True,
        ),
    ],
)
def test_probe_admission_plans_and_expiry(probe_scan, native_index, admitted, monkeypatch):
    collection = pgvector_adapter.PgvectorCollection.__new__(pgvector_adapter.PgvectorCollection)
    collection._name = "test_pg"
    collection._indexes = {"default": {"_pg_index_type": "hnsw", "_pg_index_name": "ann"}}
    collection._retrieval_config = {"filter_exact_threshold": 5}
    collection._lock = threading.RLock()
    collection._filter_probe_admission_lock = threading.Lock()
    collection._filter_probe_admission = OrderedDict()
    probe = {"Node Type": "Limit", "Total Cost": 10, "Plans": [probe_scan]}
    native = {
        "Node Type": "Limit",
        "Total Cost": 100,
        "Plans": [{"Node Type": "Index Scan", "Index Name": native_index}],
    }
    calls = []

    def query(sql, params, index_name):
        assert sql.startswith("EXPLAIN (FORMAT JSON)")  # No execution/counting at admission.
        calls.append(sql)
        plan = native if "ORDER BY" in sql else probe
        return ["QUERY PLAN"], [([{"Plan": plan}],)]

    monkeypatch.setattr(collection, "_query_rows", query)
    clock = [100.0]
    monkeypatch.setattr(pgvector_adapter.time, "monotonic", lambda: clock[0])
    args = ("default", '"account_id" = %s', ["one"], "[1,0,0]", "<=>", 10)
    assert collection._admit_filter_probe(*args) is admitted
    count = len(calls)
    assert collection._admit_filter_probe(*args) is admitted
    assert len(calls) == count
    clock[0] += 31
    assert collection._admit_filter_probe(*args) is admitted
    assert len(calls) == 2 * count
    # A different tenant must have its own plan decision.
    assert (
        collection._admit_filter_probe(
            "default", '"account_id" = %s', ["two"], "[1,0,0]", "<=>", 10
        )
        is admitted
    )
    assert len(calls) == 3 * count


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
        # Deliberately cheap function: an ordinary AND lets the planner evaluate
        # this before scalar filtering. CASE must protect rejected heap rows.
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
    collection._retrieval_config["filter_exact_threshold"] = 0
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
    collection = adapter.get_collection()._Collection__collection
    collection._retrieval_config["filter_exact_threshold"] = 0
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


def _plan_nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from _plan_nodes(child)


def test_real_plan_admission_and_exact_execution(adapter):
    if adapter._config.index_type != "hnsw":
        pytest.skip("Adaptive routing is specific to HNSW")
    collection = adapter.get_collection()._Collection__collection
    collection._retrieval_config["filter_exact_threshold"] = 5
    adapter.upsert(
        [
            {
                "id": f"row_{i:04d}",
                "vector": [1, i / 2000, 0],
                "account_id": "one" if i == 0 else "two",
            }
            for i in range(2000)
        ]
    )
    cursor = collection._cursor()
    try:
        cursor.execute(f'ANALYZE "{adapter.collection_name}"')
        # Exercise a real native-ANN plan even with a selective scalar index.
        # This is a planner-policy fixture, not a production tuning recommendation.
        cursor.execute("SET enable_sort = off")
        collection._conn.commit()
        assert collection._admit_filter_probe(
            "default", '"account_id" = %s', ["one"], "[1,0,0]", "<=>", 10
        )
        sql, params = collection._dense_candidate_query(
            "default", '"account_id" = %s', ["one"], "[1,0,0]", "<=>", 10
        )
        cursor.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params)
        nodes = list(_plan_nodes(cursor.fetchone()[0][0]["Plan"]))
        probe = next(n for n in nodes if n.get("Subplan Name") == "CTE _ov_filter_probe")
        assert probe["Actual Rows"] == 1
        assert [
            r["id"]
            for r in adapter.query(query_vector=[1, 0, 0], filter=Eq("account_id", "one"), limit=10)
        ] == ["row_0000"]
        adapter.upsert({"id": "new", "vector": [0, 1, 0], "account_id": "one"})
        # Cached admission must still count fresh rows in the ranking snapshot.
        assert [
            r["id"]
            for r in adapter.query(query_vector=[1, 0, 0], filter=Eq("account_id", "one"), limit=10)
        ] == ["row_0000", "new"]
    finally:
        collection._conn.rollback()
        cursor.close()
        reset = collection._cursor()
        try:
            reset.execute("RESET enable_sort")
            collection._conn.commit()
        finally:
            reset.close()


@pytest.mark.parametrize("matched", [0, 4, 5, 6])
def test_adaptive_plan_threshold_and_branch_execution(adapter, matched, monkeypatch):
    if adapter._config.index_type != "hnsw":
        pytest.skip("Adaptive routing is specific to HNSW")
    import math

    collection = adapter.get_collection()._Collection__collection
    collection._retrieval_config["filter_exact_threshold"] = 5
    # Test actual branch execution independently of planner admission. Admission
    # is intentionally conservative on this tiny, unindexed array fixture.
    monkeypatch.setattr(collection, "_admit_filter_probe", lambda *args: True)
    adapter.upsert(
        [
            {
                "id": f"row_{i:04d}",
                "vector": [math.cos(i), math.sin(i), 0.1],
                "account_id": "one" if i < matched else "two",
                "search_tags": ["one" if i < matched else "two"],
            }
            for i in range(2000)
        ]
    )
    sql, params = collection._dense_candidate_query(
        "default", '%s = ANY("search_tags")', ["one"], "[1,0,0]", "<=>", 10
    )
    assert params[1] == 6  # probe threshold + 1, never full COUNT on the table
    captured = []
    query_rows = collection._query_rows

    def capture_query(sql, params, index_name=None):
        captured.append((sql, params))
        return query_rows(sql, params, index_name)

    monkeypatch.setattr(collection, "_query_rows", capture_query)
    result = adapter.query(query_vector=[1, 0, 0], filter=Eq("search_tags", "one"), limit=10)
    sql, params = captured[-1]
    cursor = adapter._conn.cursor()
    try:
        cursor.execute(f'ANALYZE "{adapter.collection_name}"')
        # Force ANN for the broad branch even on this small test collection.
        cursor.execute("SET LOCAL enable_seqscan = off")
        collection._apply_search_params_on_cursor(cursor, "default")
        cursor.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params)
        nodes = list(_plan_nodes(cursor.fetchone()[0][0]["Plan"]))
        probe = next(n for n in nodes if n.get("Subplan Name") == "CTE _ov_filter_probe")
        assert probe["Actual Rows"] == min(matched, 6)
        ann_name = collection._indexes["default"]["_pg_index_name"]
        ann_nodes = [n for n in nodes if n.get("Index Name") == ann_name]
        assert ann_nodes, [(n["Node Type"], n.get("Index Name")) for n in nodes]
        ann = ann_nodes[0]
        assert ann["Actual Loops"] == (0 if matched <= 5 else 1)
        exact = next(n for n in nodes if n.get("CTE Name") == "_ov_exact_vectors")
        assert exact["Actual Rows"] == (matched if matched <= 5 else 0)
    finally:
        adapter._conn.rollback()
        cursor.close()
    if matched <= 5:
        assert len(result) == matched
        distances = {f"row_{i:04d}": math.cos(i) / math.sqrt(1.01) for i in range(matched)}
        assert [r["id"] for r in result] == sorted(distances, key=lambda k: -distances[k])


@pytest.mark.parametrize("depth", [-1, 0, 1])
def test_adaptive_exact_scope_acl_pagination_and_ranked(adapter, depth, monkeypatch):
    collection = adapter.get_collection()._Collection__collection
    collection._retrieval_config["filter_exact_threshold"] = 20
    monkeypatch.setattr(
        collection,
        "_admit_filter_probe",
        lambda index_name, *args: collection._indexes[index_name]["_pg_index_type"] == "hnsw",
    )
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
        # Flat on the same table is the ground truth, including when a physical
        # HNSW index exists. Restore metadata before the next adaptive query.
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


def test_adaptive_hybrid_keeps_candidate_budget(adapter):
    seed(adapter)
    collection = adapter.get_collection()._Collection__collection
    collection._retrieval_config["filter_exact_threshold"] = 5
    # The middle vector is second in each modality but first by fused score.
    # It must remain outside a budget-one union even in a small exact space.
    adapter.upsert(
        {"id": "middle", "vector": [1, 1, 0], "account_id": "one", "sparse_vector": {"other": 0.9}}
    )
    collection.create_sparse_index(candidate_limit=1, sparse_weight=0.5)
    rows = adapter.query(
        query_vector=[1, 0, 0],
        sparse_query_vector={"other": 1},
        filter=Eq("account_id", "one"),
        limit=1,
    )
    assert [r["id"] for r in rows] == ["a"]
    assert rows[0]["_score"] == pytest.approx(0.5)


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
