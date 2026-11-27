"""Real factory/adapter/PG tests; require both isolated database DSNs."""

import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from openviking.storage.expr import And, Contains, Eq, Or, PathScope, Range, RawDSL
from openviking.storage.vectordb_adapters import pgvector_adapter
from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking_cli.exceptions import FailedPreconditionError
from openviking_cli.utils.config.vectordb_config import PgvectorConfig, VectorDBBackendConfig

psycopg2 = pytest.importorskip("psycopg2")

SCHEMA = {
    "Fields": [
        {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
        {"FieldName": "vector", "FieldType": "vector", "Dim": 3},
        {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
        {"FieldName": "uri", "FieldType": "path"},
        {"FieldName": "account_id", "FieldType": "string"},
        {"FieldName": "updated_at", "FieldType": "date_time"},
        {"FieldName": "search_tags", "FieldType": "list<string>"},
        {"FieldName": "content", "FieldType": "text"},
        {"FieldName": "level", "FieldType": "int64"},
        {"FieldName": "acl", "FieldType": "list<string>"},
    ]
}
EVIDENCE: list[dict[str, Any]] = []


@pytest.fixture(scope="session", autouse=True)
def save_evidence():
    yield
    if os.environ.get("OV_E2E_EVIDENCE"):
        Path(os.environ["OV_E2E_EVIDENCE"]).write_text(
            json.dumps({"comparisons": EVIDENCE}, ensure_ascii=False, indent=2)
        )


@pytest.fixture(autouse=True)
def require_databases():
    if not all(os.environ.get(key) for key in ("OV_ENHANCED_TEST_DSN", "OV_NATIVE_TEST_DSN")):
        pytest.skip("Set OV_ENHANCED_TEST_DSN and OV_NATIVE_TEST_DSN")


@pytest.fixture
def diagnostics(monkeypatch):
    evidence = []
    original = pgvector_adapter.logger.debug

    def record(message, *args, **kwargs):
        if message == "pgvector filtered_knn diagnostics=%s":
            evidence.append(json.loads(args[0]))
        return original(message, *args, **kwargs)

    monkeypatch.setattr(pgvector_adapter.logger, "debug", record)
    return evidence


def config(
    db="enhanced",
    mode="required",
    metric="cosine",
    index_type="hnsw",
    options=None,
    name=None,
    unfiltered_use_native=False,
):
    return VectorDBBackendConfig(
        backend="pgvector",
        name=name or "e2e_" + uuid.uuid4().hex[:12],
        dimension=3,
        distance_metric=metric,
        sparse_weight=0.5,
        pgvector=PgvectorConfig.model_validate(
            {
                "dsn_env": f"OV_{db.upper()}_TEST_DSN",
                "index_type": index_type,
                "filtered_knn": mode,
                "filtered_knn_options": options or {},
                # Existing tests exercise the envelope, including absent predicates.
                "unfiltered_use_native": unfiltered_use_native,
                "candidate_limit": 20,
            }
        ),
    )


@contextmanager
def collection(**kwargs):
    a = cast(
        pgvector_adapter.PgvectorCollectionAdapter, create_collection_adapter(config(**kwargs))
    )
    try:
        a.create_collection(
            a.collection_name,
            SCHEMA,
            distance=a._distance_metric,
            sparse_weight=0.5,
            index_name="default",
        )
        yield a
    finally:
        a.drop_collection()
        a.close()


def rows():
    paths = [
        "viking://resources",
        "viking://resources/a",
        "viking://resources/sub/b",
        "viking://resources2/wrong",
        "viking://resources/a%_/quoted'中文",
    ]
    return [
        {
            "id": f"id_{i:02}",
            "vector": [1, (i % 4) * 0.2, (i // 4) * 0.1],
            "sparse_vector": {"hello": float(i % 3 + 1)},
            "uri": paths[i % len(paths)],
            "account_id": "allowed" if i % 2 == 0 else "denied",
            "updated_at": "2026-11-12T00:00:00Z" if i % 3 else "2026-11-05T00:00:00Z",
            "search_tags": ["shared", "even" if i % 2 == 0 else "odd"],
            "content": f"payload {i}",
            "level": i % 3,
            "acl": ["reader"] if i % 4 == 0 else ["writer"],
        }
        for i in range(15)
    ]


FILTERS = [
    None,
    And([]),
    Or([]),
    PathScope("uri", "viking://resources", -1),
    PathScope("uri", "viking://resources", 0),
    PathScope("uri", "viking://resources", 1),
    PathScope("uri", "viking://resources/a%_", -1),
    And([Eq("account_id", "allowed"), PathScope("uri", "viking://resources", -1)]),
    Eq("search_tags", "even"),
    And(
        [
            Eq("account_id", "allowed"),
            PathScope("uri", "viking://resources", -1),
            Eq("level", 0),
            Eq("acl", "reader"),
        ]
    ),
    Contains("content", "payload 1"),
    Range("updated_at", gte="2026-11-10T00:00:00Z"),
    RawDSL({"op": "must_not", "field": "uri", "conds": ["viking://resources"], "para": "-d=1"}),
]


def assert_equal(actual, expected):
    assert [r["id"] for r in actual] == [r["id"] for r in expected]
    for a, b in zip(actual, expected, strict=True):
        assert a["_score"] == pytest.approx(b["_score"], abs=1e-6)
        assert {k: v for k, v in a.items() if k != "_score"} == {
            k: v for k, v in b.items() if k != "_score"
        }


@pytest.mark.parametrize("route", ["exact", "filtered_bitmap_ann"])
@pytest.mark.parametrize("kind", ["dense", "hybrid", "decay"])
@pytest.mark.parametrize(
    "filter_args",
    [
        pytest.param({}, id="omitted"),
        pytest.param({"filters": None}, id="none"),
        pytest.param({"filters": {}}, id="empty-object"),
    ],
)
def test_public_collection_unfiltered_search(route, kind, filter_args, diagnostics):
    options = (
        {"mode": "exact"}
        if route == "exact"
        else {"exact_threshold": 0, "search_width": 100, "recover": True}
    )
    with (
        collection(options=options) as enhanced,
        collection(db="native", mode="off", index_type="flat") as baseline,
    ):
        enhanced.upsert(rows())
        baseline.upsert(rows())
        query = {
            "index_name": "default",
            "dense_vector": [1, 0.1, 0.1],
            "limit": 4,
            "offset": 1,
            "output_fields": ["uri", "account_id", "content"],
            **filter_args,
        }
        if kind == "hybrid":
            query["sparse_vector"] = {"hello": 1}
        elif kind == "decay":
            query["advance"] = {"time_decay": {"protection": "0", "origin": "2026-11-19T00:00:00Z"}}
        # Call the public Collection directly; adapter.query() compiles None
        # into {}, which would hide a JSON-null serialization regression.
        actual = enhanced.get_collection().search_by_vector(**query).data
        expected = baseline.get_collection().search_by_vector(**query).data
        assert len(actual) == len(expected) == 4
        assert [(r.id, r.fields) for r in actual] == [(r.id, r.fields) for r in expected]
        assert [r.score for r in actual] == pytest.approx([r.score for r in expected], abs=1e-6)
        assert diagnostics[-1]["route"] == route


@pytest.mark.parametrize("metric", ["l2", "cosine", "ip"])
@pytest.mark.parametrize("route", ["exact", "filtered_bitmap_ann"])
def test_dense_filters_fields_and_paging(metric, route, diagnostics):
    options = (
        {"mode": "exact"}
        if route == "exact"
        else {"exact_threshold": 0, "search_width": 100, "recover": True}
    )
    with (
        collection(metric=metric, options=options) as enhanced,
        collection(db="native", mode="off", metric=metric, index_type="flat") as baseline,
    ):
        enhanced.upsert(rows())
        baseline.upsert(rows())
        assert enhanced.count() == 15
        assert enhanced.get(["id_00"])[0]["content"] == "payload 0"
        for filter in FILTERS:
            for offset in (0, 2):
                query = {
                    "query_vector": [1, 0.1, 0.1],
                    "filter": filter,
                    "limit": 4,
                    "offset": offset,
                    "output_fields": ["id", "uri", "content", "search_tags"],
                }
                actual, expected = enhanced.query(**query), baseline.query(**query)
                assert_equal(actual, expected)
                envelope = diagnostics[-1]
                assert envelope["route"] == (
                    "exact" if envelope["stats"]["eligible_count"] == 0 else route
                )
                assert envelope["stats"]["eligible_count_known"]
                assert envelope["stats"]["returned_count"] <= 4 + offset
                EVIDENCE.append(
                    {
                        "kind": "dense",
                        "metric": metric,
                        "configured_route": route,
                        "filter": repr(filter),
                        "offset": offset,
                        "limit": 4,
                        "candidate_budget": 4 + offset,
                        "envelope": envelope,
                        "actual": actual,
                        "expected": expected,
                    }
                )
        enhanced._collection = None
        assert enhanced.query(query_vector=[1, 0.1, 0.1])
        assert diagnostics[-1]["route"] == route


@pytest.mark.parametrize("route", ["exact", "filtered_bitmap_ann"])
@pytest.mark.parametrize("kind", ["hybrid", "decay", "hybrid_decay", "sparse"])
def test_shared_ranked_hook(route, kind, diagnostics):
    options = {"mode": "exact"} if route == "exact" else {"exact_threshold": 0, "recover": True}
    with (
        collection(options=options) as enhanced,
        collection(db="native", mode="off", index_type="flat") as baseline,
    ):
        enhanced.upsert(rows())
        baseline.upsert(rows())
        kwargs = {"filter": Eq("account_id", "allowed"), "limit": 3, "offset": 1}
        if kind != "sparse":
            kwargs["query_vector"] = [1, 0.1, 0.1]
        if kind != "decay":
            kwargs["sparse_query_vector"] = {"hello": 1}
        if "decay" in kind:
            kwargs["advance"] = {
                "time_decay": {"protection": "0", "origin": "2026-11-19T00:00:00Z"}
            }
        before = len(diagnostics)
        actual, expected = enhanced.query(**kwargs), baseline.query(**kwargs)
        assert_equal(actual, expected)
        if kind == "sparse":
            assert len(diagnostics) == before
        else:
            assert diagnostics[-1]["route"] == route
            assert diagnostics[-1]["stats"]["returned_count"] == 8
        EVIDENCE.append(
            {
                "kind": kind,
                "configured_route": route,
                "query": repr(kwargs),
                "candidate_budget": 20 if kind != "sparse" else None,
                "envelope": diagnostics[-1] if kind != "sparse" else None,
                "actual": actual,
                "expected": expected,
            }
        )


@pytest.mark.parametrize("kind", ["dense", "hybrid", "decay", "hybrid_decay"])
@pytest.mark.parametrize("empty_filter", [None, {}, And([])])
def test_unfiltered_use_native_shared_hook(kind, empty_filter, diagnostics, monkeypatch):
    with (
        collection(unfiltered_use_native=True) as enhanced,
        collection(db="native", mode="off", index_type="flat") as baseline,
    ):
        enhanced.upsert(rows())
        baseline.upsert(rows())
        raw = enhanced.get_collection()._Collection__collection
        original = raw._candidate_query_sql
        captured = []

        def capture(ctes, select):
            sql = original(ctes, select)
            captured.append(sql)
            return sql

        monkeypatch.setattr(raw, "_candidate_query_sql", capture)
        if kind == "dense":
            original_query = raw._query_rows

            def capture_query(sql, params, index_name=None):
                captured.append(sql)
                return original_query(sql, params, index_name)

            monkeypatch.setattr(raw, "_query_rows", capture_query)
        kwargs = {
            "query_vector": [1, 0.1, 0.1],
            "limit": 3,
            "offset": 1,
            "output_fields": ["uri", "account_id", "content"],
            "filter": empty_filter,
        }
        if "hybrid" in kind:
            kwargs["sparse_query_vector"] = {"hello": 1}
        if "decay" in kind:
            kwargs["advance"] = {
                "time_decay": {"protection": "0", "origin": "2026-11-19T00:00:00Z"}
            }
        assert_equal(enhanced.query(**kwargs), baseline.query(**kwargs))
        assert not diagnostics
        assert captured and all("ov_filtered_knn(" not in sql for sql in captured)
        assert '"vector" IS NOT NULL' in captured[-1]
        # Supplying a real predicate still goes through the filtered envelope.
        kwargs["filter"] = Eq("account_id", "allowed")
        assert_equal(enhanced.query(**kwargs), baseline.query(**kwargs))
        assert diagnostics[-1]["stats"]["eligible_count"] == 8
        assert "ov_filtered_knn(" in captured[-1]
        kwargs["filter"] = Or([])
        assert enhanced.query(**kwargs) == []
        assert diagnostics[-1]["stats"]["eligible_count"] == 0


def test_unfiltered_use_native_preserves_explicit_exact(diagnostics):
    with collection(unfiltered_use_native=True, options={"mode": "exact"}) as a:
        a.upsert(rows())
        assert a.query(query_vector=[1, 0.1, 0.1], limit=4)
        assert diagnostics[-1]["route"] == "exact"


@pytest.mark.parametrize("hybrid", [False, True])
def test_unfiltered_use_native_preserves_rls(hybrid, monkeypatch, diagnostics):
    role = "e2e_native_" + uuid.uuid4().hex[:8]
    with collection(unfiltered_use_native=True) as owner, admin() as cursor:
        owner.upsert(rows())
        table = owner.collection_name
        restricted = None
        cursor.execute(f'CREATE ROLE "{role}" LOGIN')
        try:
            cursor.execute(f'GRANT USAGE, CREATE ON SCHEMA public TO "{role}"')
            cursor.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"')
            cursor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            cursor.execute(f"CREATE POLICY allowed ON \"{table}\" USING (account_id = 'allowed')")
            monkeypatch.setenv(
                "OV_ROLE_TEST_DSN", os.environ["OV_ENHANCED_TEST_DSN"] + f" user={role}"
            )
            cfg = config(mode="auto", name=table, unfiltered_use_native=True)
            cfg.pgvector.dsn_env = "OV_ROLE_TEST_DSN"
            restricted = create_collection_adapter(cfg)
            query: dict[str, Any] = {"query_vector": [1, 0.1, 0.1], "limit": 20}
            if hybrid:
                query["sparse_query_vector"] = {"hello": 1}
            visible = restricted.query(**query)
            assert len(visible) == 8
            assert all(r["account_id"] == "allowed" for r in visible)
            assert not diagnostics
        finally:
            if restricted:
                restricted.close()
            cursor.execute(f'DROP OWNED BY "{role}"')
            cursor.execute(f'DROP ROLE "{role}"')


@pytest.mark.parametrize("db", ["native", "enhanced"])
@pytest.mark.parametrize("mode", ["off", "auto", "required"])
def test_capability_modes(db, mode, diagnostics):
    if db == "native" and mode == "required":
        with pytest.raises(FailedPreconditionError, match="capability"):
            create_collection_adapter(config(db=db, mode=mode))
        return
    with collection(db=db, mode=mode) as a:
        a.upsert(rows())
        assert a.query(query_vector=[1, 0, 0])
        assert bool(diagnostics) == (db == "enhanced" and mode != "off")


@pytest.mark.parametrize("metric,index_type", [("l1", "hnsw"), ("cosine", "flat")])
@pytest.mark.parametrize("mode", ["auto", "required"])
def test_unsupported_structure(metric, index_type, mode, diagnostics):
    with collection(metric=metric, index_type=index_type, mode=mode) as a:
        a.upsert(rows())
        if mode == "required":
            with pytest.raises(FailedPreconditionError, match="HNSW"):
                a.query(query_vector=[1, 0, 0])
        else:
            assert a.query(query_vector=[1, 0, 0])
        assert not diagnostics


def test_budget_error_propagates_and_pool_recovers(diagnostics):
    with collection(mode="auto", options={"mode": "exact", "max_heap_fetches": 1}) as a:
        a.upsert(rows())
        with pytest.raises(psycopg2.Error):
            a.query(query_vector=[1, 0, 0])
        assert not diagnostics
        assert a.count() == 15
        assert a.get(["id_00"])[0]["id"] == "id_00"


def test_ann_partial_budget_is_observable(diagnostics):
    with collection(options={"exact_threshold": 0, "max_graph_visits": 1}) as a:
        a.upsert(rows())
        result = a.query(query_vector=[1, 0, 0], filter=Eq("account_id", "allowed"))
        assert all(r["account_id"] == "allowed" for r in result)
        assert diagnostics[-1]["route"] == "filtered_bitmap_ann"
        assert diagnostics[-1]["stop_reason"] == "visit_budget"
        assert diagnostics[-1]["approximate"] is True


def test_unknown_ast_error_never_falls_back(diagnostics):
    with collection(mode="auto") as a:
        a.upsert(rows())
        with pytest.raises(psycopg2.Error):
            a.query(
                query_vector=[1, 0, 0], filter=RawDSL({"op": "unsupported", "field": "content"})
            )
        assert not diagnostics
        assert a.count() == 15


def test_equal_distance_multi_tid_paging(diagnostics):
    with collection(options={"exact_threshold": 0, "recover": True}) as a:
        ids = [f"tie_{i:02d}" for i in range(12)]
        a.upsert([{"id": item, "vector": [1, 0, 0]} for item in reversed(ids)])
        actual = [
            r["id"]
            for offset in range(0, 12, 3)
            for r in a.query(query_vector=[1, 0, 0], limit=3, offset=offset)
        ]
        assert actual == ids
        assert diagnostics[-1]["stats"]["returned_count"] == 12


def test_ranked_budget_includes_offset(diagnostics):
    with collection(options={"mode": "exact"}) as a:
        a.upsert([{"id": f"budget_{i:02}", "vector": [1, i * 0.01, 0]} for i in range(40)])
        result = a.query(
            query_vector=[1, 0, 0],
            limit=5,
            offset=21,
            advance={"time_decay": {"protection": "0", "origin": "2026-11-19T00:00:00Z"}},
        )
        assert [r["id"] for r in result] == [f"budget_{i:02}" for i in range(21, 26)]
        assert diagnostics[-1]["stats"]["returned_count"] == 26


@pytest.mark.parametrize(
    "options",
    [
        {"unknown": 1},
        {"mode": "ann"},
        {"exact_threshold": -1},
        {"recover": "yes"},
        {"max_heap_fetches": 0},
        {"search_width": 100001},
        {"mode": "exact", "overflow_policy": "partial"},
        {"eligible_memory_limit": 4294967297},
        {"eligible_memory_limit": 4294967295.75},
        {"eligible_memory_limit": 4294967296.0},
        {"eligible_memory_limit": -1},
        {"eligible_memory_limit": 1048575},
        {"eligible_memory_limit": True},
    ],
)
def test_options_validation(options):
    with pytest.raises(ValueError):
        PgvectorConfig(filtered_knn_options=options)


@pytest.mark.parametrize("memory_limit", [2147483648, 3221225472, 4294967296])
@pytest.mark.parametrize("route", ["exact", "filtered_bitmap_ann"])
def test_large_memory_budget(memory_limit, route, diagnostics):
    options = {
        "eligible_memory_limit": memory_limit,
        "exact_threshold": 0,
        "search_width": 100,
        "recover": True,
    }
    if route == "exact":
        options["mode"] = "exact"
    with (
        collection(options=options) as enhanced,
        collection(db="native", mode="off", index_type="flat") as baseline,
    ):
        enhanced.upsert(rows())
        baseline.upsert(rows())
        query = {"query_vector": [1, 0.1, 0.1], "limit": 4, "filter": Eq("account_id", "allowed")}
        assert_equal(enhanced.query(**query), baseline.query(**query))
        assert diagnostics[-1]["route"] == route
    assert PgvectorConfig().filtered_knn_options.eligible_memory_limit == 67108864


@contextmanager
def admin(db="enhanced"):
    conn = psycopg2.connect(os.environ[f"OV_{db.upper()}_TEST_DSN"])
    conn.autocommit = True
    try:
        with conn.cursor() as cursor:
            yield cursor
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("memory_limit", "error"),
    [
        (4294967297, "option is out of range"),
        (4294967295.75, "integer option expected"),
        (-1, "option is out of range"),
        (1048575, "option is out of range"),
        (True, "numeric option expected"),
        ("4294967296", "numeric option expected"),
    ],
)
def test_sql_memory_budget_validation(memory_limit, error):
    with admin() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE ov_budget_validation (id text PRIMARY KEY, vector vector(3))"
        )
        cursor.execute(
            "CREATE INDEX ov_budget_validation_idx ON ov_budget_validation "
            "USING hnsw (vector vector_cosine_ops)"
        )
        with pytest.raises(psycopg2.Error, match=error):
            cursor.execute(
                "SELECT ov_filtered_knn('ov_budget_validation_idx'::regclass, "
                "'[1,0,0]'::vector, NULL, 4, %s::jsonb)",
                (json.dumps({"eligible_memory_limit": memory_limit}),),
            )


@pytest.mark.parametrize("mode", ["auto", "required"])
def test_version_mismatch(mode):
    with admin() as cursor:
        cursor.execute("SELECT pg_get_functiondef('ov_filtered_knn_version()'::regprocedure)")
        original = cursor.fetchone()[0]
        try:
            cursor.execute(
                "CREATE OR REPLACE FUNCTION ov_filtered_knn_version() RETURNS integer "
                "LANGUAGE SQL IMMUTABLE AS 'SELECT 2'"
            )
            if mode == "required":
                with pytest.raises(FailedPreconditionError, match="capability"):
                    create_collection_adapter(config(mode=mode))
            else:
                a = cast(
                    pgvector_adapter.PgvectorCollectionAdapter,
                    create_collection_adapter(config(mode=mode)),
                )
                try:
                    assert a._filtered_knn_schema is None
                finally:
                    a.close()
        finally:
            cursor.execute(original)


def test_same_named_nonextension_functions_are_not_capability():
    with admin("native") as cursor:
        try:
            cursor.execute(
                "CREATE FUNCTION ov_filtered_knn(regclass, vector, jsonb, integer, jsonb)"
                " RETURNS jsonb LANGUAGE SQL STABLE AS 'SELECT ''{}''::jsonb'"
            )
            cursor.execute(
                "CREATE FUNCTION ov_filtered_knn_version() RETURNS integer "
                "LANGUAGE SQL IMMUTABLE AS 'SELECT 1'"
            )
            with pytest.raises(FailedPreconditionError, match="capability"):
                create_collection_adapter(config(db="native"))
        finally:
            cursor.execute(
                "DROP FUNCTION IF EXISTS ov_filtered_knn(regclass, vector, jsonb, integer, jsonb)"
            )
            cursor.execute("DROP FUNCTION IF EXISTS ov_filtered_knn_version()")


def test_capability_probe_error_is_not_absence():
    with admin() as cursor:
        cursor.execute("SELECT pg_get_functiondef('ov_filtered_knn_version()'::regprocedure)")
        original = cursor.fetchone()[0]
        try:
            cursor.execute("""CREATE OR REPLACE FUNCTION ov_filtered_knn_version()
                RETURNS integer LANGUAGE plpgsql STABLE AS $body$
                BEGIN RAISE EXCEPTION 'probe failure'; END $body$""")
            with pytest.raises(psycopg2.Error, match="probe failure"):
                create_collection_adapter(config(mode="auto"))
        finally:
            cursor.execute(original)


@pytest.mark.parametrize("route", ["exact", "filtered_bitmap_ann"])
def test_rls_and_runtime_execute_revocation(route, monkeypatch, diagnostics):
    role = "e2e_role_" + uuid.uuid4().hex[:8]
    options = {"mode": "exact"} if route == "exact" else {"exact_threshold": 0, "recover": True}
    with collection(mode="auto", options=options) as owner, admin() as cursor:
        owner.upsert(rows())
        table = owner.collection_name
        restricted = None
        cursor.execute(f'CREATE ROLE "{role}" LOGIN')
        try:
            cursor.execute(f'GRANT USAGE, CREATE ON SCHEMA public TO "{role}"')
            cursor.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"')
            cursor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            cursor.execute(f"CREATE POLICY allowed ON \"{table}\" USING (account_id = 'allowed')")
            dsn = os.environ["OV_ENHANCED_TEST_DSN"] + f" user={role}"
            monkeypatch.setenv("OV_ROLE_TEST_DSN", dsn)
            cfg = config(mode="auto", options=options, name=table)
            cfg.pgvector.dsn_env = "OV_ROLE_TEST_DSN"
            restricted = create_collection_adapter(cfg)
            visible = restricted.query(query_vector=[1, 0.1, 0.1], limit=20)
            assert len(visible) == 8
            assert all(r["account_id"] == "allowed" for r in visible)
            assert diagnostics[-1]["stats"]["eligible_count"] == 8
            assert diagnostics[-1]["route"] == route
            visible = restricted.query(
                query_vector=[1, 0.1, 0.1], sparse_query_vector={"hello": 1}, limit=20
            )
            assert len(visible) == 8
            assert all(r["account_id"] == "allowed" for r in visible)
            cursor.execute(
                "REVOKE EXECUTE ON FUNCTION ov_filtered_knn"
                "(regclass, vector, jsonb, integer, jsonb) FROM PUBLIC"
            )
            try:
                count = len(diagnostics)
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    restricted.query(query_vector=[1, 0, 0])
                assert len(diagnostics) == count
                assert restricted.count() == 8
                with pytest.raises(FailedPreconditionError, match="permissions"):
                    create_collection_adapter(cfg)
            finally:
                cursor.execute(
                    "GRANT EXECUTE ON FUNCTION ov_filtered_knn"
                    "(regclass, vector, jsonb, integer, jsonb) TO PUBLIC"
                )
        finally:
            if restricted:
                restricted.close()
            cursor.execute(f'DROP OWNED BY "{role}"')
            cursor.execute(f'DROP ROLE "{role}"')


def test_candidates_and_fields_share_statement_snapshot(diagnostics):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    # Hold an advisory lock inside the real C-function wrapper, after the
    # caller's statement snapshot has been taken. Commit a separate writer
    # before releasing the barrier, then verify candidate and projected fields.
    with collection(options={"mode": "exact"}) as a, admin() as barrier, admin() as writer:
        a.upsert(
            {"id": "snapshot", "vector": [1, 0, 0], "account_id": "allowed", "content": "before"}
        )
        lock_key = 7261112601
        barrier.execute(f"SELECT pg_advisory_lock({lock_key})")
        barrier.execute(
            "ALTER FUNCTION ov_filtered_knn(regclass,vector,jsonb,integer,jsonb) "
            "RENAME TO ov_filtered_knn_real"
        )
        barrier.execute(f"""
            CREATE FUNCTION ov_filtered_knn(regclass, vector, jsonb, integer, jsonb)
            RETURNS jsonb LANGUAGE plpgsql STABLE AS $body$
            BEGIN
                PERFORM pg_advisory_xact_lock({lock_key});
                RETURN ov_filtered_knn_real($1,$2,$3,$4,$5);
            END $body$
        """)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    a.query, query_vector=[1, 0, 0], filter=Eq("account_id", "allowed")
                )
                # Observe the database's actual blocked query, not a timer.
                import time

                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    writer.execute(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname=current_database() "
                        "AND wait_event='advisory'"
                    )
                    if writer.fetchone()[0]:
                        break
                    threading.Event().wait(0.01)
                else:
                    barrier.execute(f"SELECT pg_advisory_unlock({lock_key})")
                    raise AssertionError("adapter never reached snapshot barrier")
                writer.execute(
                    f"UPDATE \"{a.collection_name}\" SET content='after', "
                    "account_id='denied', vector='[0,1,0]'"
                )
                barrier.execute(f"SELECT pg_advisory_unlock({lock_key})")
                result = future.result(timeout=10)
            assert result[0]["id"] == "snapshot"
            assert result[0]["content"] == "before"
            assert result[0]["account_id"] == "allowed"
            assert result[0]["_score"] == pytest.approx(1)
            assert a.query(query_vector=[1, 0, 0], filter=Eq("account_id", "allowed")) == []
        finally:
            barrier.execute(f"SELECT pg_advisory_unlock({lock_key})")
            barrier.execute("DROP FUNCTION ov_filtered_knn(regclass,vector,jsonb,integer,jsonb)")
            barrier.execute(
                "ALTER FUNCTION ov_filtered_knn_real"
                "(regclass,vector,jsonb,integer,jsonb) RENAME TO ov_filtered_knn"
            )
