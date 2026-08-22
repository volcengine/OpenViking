import json
import threading
from unittest.mock import Mock, call, patch

import pytest
from pydantic import ValidationError

from openviking.storage.expr import And, Eq, Or
from openviking.storage.vectordb_adapters.opengauss_adapter import (
    OpenGaussCollection,
    OpenGaussCollectionAdapter,
    _PooledConnectionProxy,
    _bounded_identifier,
    _build_where_clause,
    _coerce_sparse_vector,
    _coerce_vector,
    _index_meta_table_name,
    _is_table_already_distributed,
    _is_undefined_table_error,
    _load_collection_meta,
    _validate_identifier,
    _validate_vector,
)
from openviking_cli.utils.config.vectordb_config import (
    OpenGaussConfig,
    VectorDBBackendConfig,
    resolve_opengauss_index_spec,
)

INDEX_CASES = [
    ("hnsw", {"m": 16, "ef_construction": 64}, {"ef_search": 100}, "hnsw"),
    ("hnsw-pq", {"pq_m": 8, "pq_ksub": 256}, {}, "hnsw"),
    (
        "hnsw-rabitq",
        {"rabitq_refine_type": "FP32", "rabitq_fht": True},
        {"rbq_query_bits": 8, "rbq_refinek": 20},
        "hnsw",
    ),
    ("ivfflat", {"lists": 100}, {"probes": 10}, "ivfflat"),
    (
        "ivf-pq",
        {"lists": 100, "pq_m": 8, "pq_ksub": 256, "by_residual": True},
        {"probes": 10, "ivfpq_kreorder": 100},
        "ivfflat",
    ),
    (
        "ivf-rabitq",
        {"lists": 100, "rabitq_refine_type": "SQ8"},
        {"probes": 10, "rbq_query_bits": 8},
        "ivfflat",
    ),
    ("diskann", {"index_size": 50}, {"probes": 256}, "diskann"),
]


@pytest.mark.parametrize(
    ("index_type", "build_params", "search_params", "access_method"), INDEX_CASES
)
def test_complete_index_matrix_generates_official_access_methods(
    index_type, build_params, search_params, access_method
):
    config = VectorDBBackendConfig(
        backend="opengauss",
        dimension=512,
        opengauss=OpenGaussConfig(
            index_type=index_type,
            build_params=build_params,
            search_params=search_params,
        ),
    )
    assert resolve_opengauss_index_spec(config.opengauss.index_type)[0] == access_method

    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._distance = "cosine"
    collection._distributed = False
    collection._indexes = {}
    collection._pending_indexes = {}
    index_meta = collection._normalized_index_meta(
        "default",
        {
            "VectorIndex": {"IndexType": index_type, "Distance": "cosine"},
            "build_params": config.opengauss.build_params,
            "search_params": config.opengauss.search_params,
        },
    )
    sql = collection._create_index_sql(index_meta)
    assert f"USING {access_method}" in sql
    assert "CREATE INDEX IF NOT EXISTS" not in sql
    if index_type.endswith("-pq"):
        assert "enable_pq = on" in sql
    if index_type.endswith("-rabitq"):
        assert "enable_rabitq = on" in sql

    collection._indexes["default"] = index_meta
    statements = collection._search_param_statements("default")
    assert all(statement.startswith("SET LOCAL ") for statement in statements)


def test_search_param_statements_propagate_to_dn_workers_in_distributed_mode():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._distance = "cosine"
    collection._distributed = False
    collection._indexes = {}
    collection._pending_indexes = {}
    index_meta = collection._normalized_index_meta(
        "default",
        {
            "VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"},
            "search_params": {"ef_search": 100},
        },
    )
    collection._indexes["default"] = index_meta

    standalone_statements = collection._search_param_statements("default")
    assert standalone_statements == ["SET LOCAL hnsw_ef_search = 100"]

    # spq defaults to propagate_set_commands='none'; without propagation the
    # search parameters would never reach the DN shard scans.
    collection._distributed = True
    distributed_statements = collection._search_param_statements("default")
    assert distributed_statements == [
        "SET LOCAL spq.propagate_set_commands = 'local'",
        "SET LOCAL hnsw_ef_search = 100",
    ]


def test_runtime_create_index_rejects_diskann_pq():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._distance = "cosine"
    collection._distributed = False
    collection._dim = 8

    with pytest.raises(ValueError, match="Invalid openGauss index_type"):
        collection.create_index(
            "default",
            {
                "VectorIndex": {
                    "IndexType": "diskann-pq",
                    "Distance": "cosine",
                },
                "build_params": {"index_size": 50, "pq_m": 8},
            },
        )

    with pytest.raises(ValueError, match="Unsupported openGauss diskann build_params"):
        collection.create_index(
            "default",
            {
                "VectorIndex": {
                    "IndexType": "diskann",
                    "Distance": "cosine",
                },
                "build_params": {"index_size": 50, "enable_pq": True, "pq_m": 8},
            },
        )


def test_config_rejects_unknown_and_incompatible_parameters():
    with pytest.raises(ValidationError, match="Unsupported openGauss"):
        OpenGaussConfig(index_type="hnsw", build_params={"lists": 100})
    with pytest.raises(ValidationError, match="cannot be enabled together"):
        OpenGaussConfig(
            index_type="hnsw",
            build_params={"enable_pq": True, "enable_rabitq": True},
        )
    with pytest.raises(ValidationError, match="divisible"):
        VectorDBBackendConfig(
            backend="opengauss",
            dimension=513,
            opengauss=OpenGaussConfig(index_type="hnsw-pq", build_params={"pq_m": 8}),
        )


def test_identifiers_and_vectors_are_strictly_validated():
    assert _validate_identifier("context_01") == "context_01"
    with pytest.raises(ValueError, match="Invalid"):
        _validate_identifier('context"; DROP TABLE context; --')
    assert _validate_vector([1, 2.5, 3], 3) == [1.0, 2.5, 3.0]
    with pytest.raises(ValueError, match="dimension mismatch"):
        _validate_vector([1, 2], 3)
    with pytest.raises(ValueError, match="finite"):
        _validate_vector([1, float("nan"), 3], 3)


def test_training_index_metadata_is_persisted_as_pending_before_data_exists():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._dim = 8
    collection._distance = "cosine"
    collection._field_names = {"id", "vector"}
    collection._indexes = {}
    collection._pending_indexes = {}
    collection._persist_index_meta_and_scalar_indexes = Mock()
    collection._table_has_rows = Mock(return_value=False)
    collection._physical_index_definition = Mock(return_value=None)
    collection._execute = Mock()

    result = collection.create_index(
        "default",
        {
            "VectorIndex": {"IndexType": "ivf-pq", "Distance": "cosine"},
            "build_params": {"lists": 10, "pq_m": 8},
        },
    )

    assert result.get_name() == "default"
    assert "default" in collection._pending_indexes
    assert "default" not in collection._indexes
    pending_meta = collection._pending_indexes["default"]
    assert pending_meta["_state"] == "pending"
    collection._persist_index_meta_and_scalar_indexes.assert_called_once_with(
        "default", pending_meta
    )
    collection._execute.assert_not_called()


def test_pending_index_drops_conflicting_physical_definition():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._dim = 8
    collection._distance = "l2"
    collection._field_names = {"id", "vector"}
    collection._indexes = {"default": {"stale": True}}
    collection._pending_indexes = {}
    collection._persist_index_meta_and_scalar_indexes = Mock()
    collection._table_has_rows = Mock(return_value=False)
    collection._physical_index_definition = Mock(
        return_value="CREATE INDEX idx_context_default_vec ON context "
        "USING ivfflat (vector vector_cosine_ops) WITH (lists=10)"
    )
    collection._execute = Mock()

    collection.create_index(
        "default",
        {
            "VectorIndex": {"IndexType": "ivf-pq", "Distance": "l2"},
            "build_params": {"lists": 10, "pq_m": 8},
        },
    )

    collection._execute.assert_called_once()
    assert "DROP INDEX" in collection._execute.call_args.args[0]
    assert "default" in collection._pending_indexes
    assert "default" not in collection._indexes
    collection._persist_index_meta_and_scalar_indexes.assert_called_once()


def test_catalog_verification_failure_never_saves_metadata():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._distance = "cosine"
    collection._distributed = False
    collection._indexes = {}
    collection._pending_indexes = {}
    collection._physical_index_definition = Mock(side_effect=[None, None])
    collection._execute = Mock()
    collection._save_index_meta = Mock()
    collection._lock = threading.RLock()
    collection._cursor = Mock(return_value=Mock())
    collection._conn = Mock()
    meta = collection._normalized_index_meta(
        "default",
        {"VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"}},
    )

    with pytest.raises(RuntimeError, match="catalog verification failed"):
        collection._materialize_index("default", meta)
    collection._save_index_meta.assert_not_called()


def test_materialized_index_is_not_published_before_metadata_persists():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._distance = "cosine"
    collection._distributed = False
    collection._maintenance_work_mem_mb = 64
    collection._indexes = {}
    collection._pending_indexes = {"default": {"_state": "pending"}}
    collection._lock = threading.RLock()
    collection._physical_index_definition = Mock(
        return_value=(
            "CREATE INDEX idx_context_default_vec ON context "
            "USING hnsw (vector vector_cosine_ops) "
            "WITH (m=16, ef_construction=64)"
        )
    )
    collection._apply_parallel_workers = Mock()
    collection._persist_index_meta_and_scalar_indexes = Mock(
        side_effect=RuntimeError("metadata failed")
    )
    meta = collection._normalized_index_meta(
        "default",
        {"VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"}},
    )

    with pytest.raises(RuntimeError, match="metadata failed"):
        collection._materialize_index("default", meta)

    assert "default" not in collection._indexes
    assert "default" in collection._pending_indexes
    collection._persist_index_meta_and_scalar_indexes.assert_called_once_with(
        "default", meta
    )


def test_pending_index_search_returns_empty_for_empty_collection():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._pending_indexes = {"default": {}}
    collection._bulk_ingest_depth = 0
    collection._materialize_index = Mock()
    collection._table_has_rows = Mock(return_value=False)
    collection.has_index = Mock(return_value=False)

    result = collection.search_by_vector("default", dense_vector=[1.0])

    assert result.data == []
    collection._materialize_index.assert_not_called()
    collection.has_index.assert_not_called()

def test_index_materialization_sets_transaction_local_build_memory():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._distance = "cosine"
    collection._distributed = False
    collection._maintenance_work_mem_mb = 128
    collection._indexes = {}
    collection._pending_indexes = {}
    collection._lock = threading.RLock()
    collection._physical_index_definition = Mock(
        side_effect=[
            None,
            "CREATE INDEX idx_context_default_vec ON context "
            "USING ivfflat (vector vector_cosine_ops) WITH (lists=1)",
        ]
    )
    collection._persist_index_meta_and_scalar_indexes = Mock()
    cursor = Mock()
    collection._cursor = Mock(return_value=cursor)
    collection._conn = Mock()
    meta = collection._normalized_index_meta(
        "default",
        {
            "VectorIndex": {"IndexType": "ivfflat", "Distance": "cosine"},
            "build_params": {"lists": 1},
        },
    )

    collection._materialize_index("default", meta)

    assert cursor.execute.call_args_list[0].args[0] == (
        'ALTER TABLE "context" RESET (parallel_workers)'
    )
    assert cursor.execute.call_args_list[1].args == (
        "SET LOCAL maintenance_work_mem = '128MB'",
    )
    create_index_sql = cursor.execute.call_args_list[2].args[0]
    assert create_index_sql.startswith("CREATE INDEX")
    assert '"idx_context_default_vec"' in create_index_sql
    assert collection._conn.commit.call_count == 2
    collection._conn.rollback.assert_not_called()
    assert cursor.close.call_count == 2
    collection._persist_index_meta_and_scalar_indexes.assert_called_once_with(
        "default", meta
    )


def _adapter_without_connect(**overrides) -> OpenGaussCollectionAdapter:
    kwargs = {
        "collection_name": "context",
        "host": "127.0.0.1",
        "port": 5432,
        "user": "gaussdb",
        "password": "",
        "db_name": "openviking",
        "index_type": "hnsw",
        "distance_metric": "cosine",
    }
    kwargs.update(overrides)
    with patch.object(OpenGaussCollectionAdapter, "_connect", return_value=None):
        return OpenGaussCollectionAdapter(**kwargs)


def test_create_collection_rejects_unsafe_name_before_backend_ddl():
    adapter = _adapter_without_connect()
    adapter._create_backend_collection = Mock()
    with patch.object(adapter, "collection_exists", return_value=False):
        with pytest.raises(ValueError, match="Invalid openGauss collection name"):
            adapter.create_collection(
                'evil"; DROP TABLE t; --',
                {"Fields": []},
                distance="cosine",
                sparse_weight=0.0,
                index_name="default",
            )
    adapter._create_backend_collection.assert_not_called()


def test_ensure_vector_index_always_materializes_configured_index():
    adapter = _adapter_without_connect(index_type="hnsw")
    og_coll = Mock()
    og_coll._indexes = {"default": {"ScalarIndex": ["uri"]}}
    og_coll._pending_indexes = {}
    og_coll._meta = {"ScalarIndex": ["uri"]}
    og_coll._normalized_index_meta = Mock(
        return_value={"_pg_index_name": "idx_context_default_vec", "ScalarIndex": ["uri"]}
    )
    og_coll._physical_index_definition = Mock(return_value=None)

    adapter._ensure_vector_index_exists(og_coll, "cosine")

    og_coll.create_index.assert_called_once()
    index_name, index_meta = og_coll.create_index.call_args.args
    assert index_name == "default"
    assert index_meta["VectorIndex"]["IndexType"] == "hnsw"
    assert index_meta["VectorIndex"]["Distance"] == "cosine"
    assert index_meta["ScalarIndex"] == ["uri"]


def test_ensure_vector_index_registers_matching_physical_index_without_rebuild():
    adapter = _adapter_without_connect(index_type="hnsw")
    og_coll = Mock()
    og_coll._indexes = {}
    og_coll._pending_indexes = {"default": {"stale": True}}
    og_coll._meta = {"ScalarIndex": ["uri"]}
    recovered = {
        "_pg_index_name": "idx_context_default_vec",
        "ScalarIndex": ["uri"],
    }
    og_coll._normalized_index_meta = Mock(return_value=recovered)
    og_coll._physical_index_definition = Mock(
        return_value="CREATE INDEX idx_context_default_vec ON context USING hnsw (vector vector_cosine_ops)"
    )
    og_coll._index_definition_matches = Mock(return_value=True)

    adapter._ensure_vector_index_exists(og_coll, "cosine")

    og_coll.create_index.assert_not_called()
    og_coll._persist_index_meta_and_scalar_indexes.assert_called_once_with(
        "default", recovered
    )
    assert og_coll._indexes["default"] == recovered
    assert "default" not in og_coll._pending_indexes


def test_index_definition_matches_tolerates_omitted_default_with_options():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._distance = "cosine"
    collection._indexes = {}
    collection._pending_indexes = {}
    meta = collection._normalized_index_meta(
        "default",
        {
            "VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"},
            "build_params": {"m": 16, "ef_construction": 64},
        },
    )
    catalog_without_with = (
        "CREATE INDEX idx_context_default_vec ON context "
        "USING hnsw (vector vector_cosine_ops)"
    )
    catalog_with_defaults = (
        catalog_without_with + " WITH (m=16, ef_construction=64)"
    )
    catalog_with_conflict = catalog_without_with + " WITH (m=8, ef_construction=64)"
    catalog_wrong_opclass = (
        "CREATE INDEX idx_context_default_vec ON context "
        "USING hnsw (vector vector_l2_ops)"
    )

    assert collection._index_definition_matches(meta, catalog_without_with)
    assert collection._index_definition_matches(meta, catalog_with_defaults)
    assert not collection._index_definition_matches(meta, catalog_with_conflict)
    assert not collection._index_definition_matches(meta, catalog_wrong_opclass)

    custom_meta = collection._normalized_index_meta(
        "default",
        {
            "VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"},
            "build_params": {"m": 32, "ef_construction": 128},
        },
    )
    catalog_missing_non_default_m = catalog_without_with + " WITH (ef_construction=128)"
    assert not collection._index_definition_matches(custom_meta, catalog_without_with)
    assert not collection._index_definition_matches(custom_meta, catalog_missing_non_default_m)
    assert collection._index_definition_matches(
        custom_meta,
        catalog_without_with + " WITH (m=32, ef_construction=128)",
    )
    assert collection._index_definition_matches(
        meta,
        catalog_without_with + " WITH (ef_construction=64)",
    )

    pq_meta = collection._normalized_index_meta(
        "default",
        {
            "VectorIndex": {"IndexType": "hnsw-pq", "Distance": "cosine"},
            "build_params": {"m": 16, "ef_construction": 64, "pq_m": 8},
        },
    )
    assert not collection._index_definition_matches(pq_meta, catalog_without_with)
    assert not collection._index_definition_matches(pq_meta, catalog_with_defaults)
    leftover_pq = (
        catalog_without_with
        + " WITH (m=16, ef_construction=64, enable_pq=on, pq_m=8, pq_ksub=256)"
    )
    leftover_rabitq = (
        catalog_without_with
        + " WITH (m=16, ef_construction=64, enable_rabitq=on, rabitq_refine_type=fp32)"
    )
    assert not collection._index_definition_matches(meta, leftover_pq)
    assert not collection._index_definition_matches(meta, leftover_rabitq)


def test_public_api_empty_or_compiles_to_sql_contradiction():
    adapter = _adapter_without_connect()

    compiled = adapter._compile_filter(Or([]))
    assert compiled == {"op": "or", "conds": []}
    sql, params = _build_where_clause(compiled)
    assert sql == "FALSE"
    assert params == []

    unfiltered = adapter._compile_filter(None)
    assert unfiltered == {}
    assert _build_where_clause(unfiltered) == ("", [])

    nested = adapter._compile_filter(And([Eq("id", "keep"), Or([])]))
    nested_sql, nested_params = _build_where_clause(nested)
    assert "FALSE" in nested_sql
    assert nested_params == ["keep"]


def test_select_projects_unstored_content_as_null():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._field_names = {"id", "uri", "vector", "content", "account_id"}
    sql = collection._select_output_columns(
        ["id", "uri", "content", "vector", "account_id"]
    )
    assert sql == '"id", "uri", NULL AS "content", "vector", "account_id"'

    collection._field_names = {"id", "uri", "vector"}
    sql = collection._select_output_columns(["id", "uri", "content"])
    assert sql == '"id", "uri", NULL AS "content"'


def test_collection_update_uses_canonical_description_key():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._meta = {"Description": "old"}
    collection._execute = Mock()

    collection.update(description="new")

    assert collection._meta["Description"] == "new"
    assert "description" not in collection._meta
    persisted_meta = json.loads(collection._execute.call_args.args[1][0])
    assert persisted_meta["Description"] == "new"


def test_row_to_dict_normalizes_psycopg_vector_payloads():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    record = collection._row_to_dict(
        ("row-1", "[0.25, 0.5, 1]", '{"3": 0.75}'),
        ["id", "vector", "sparse_vector"],
    )
    assert record["id"] == "row-1"
    assert record["vector"] == [0.25, 0.5, 1.0]
    assert record["sparse_vector"] == {"3": 0.75}
    assert _coerce_vector(None) is None
    assert _coerce_vector([1, 2]) == [1.0, 2.0]
    assert _coerce_sparse_vector({"1": 1.0}) == {"1": 1.0}


def test_long_collection_name_fits_physical_sql_identifiers():
    raw_index = f"idx_{'c' * 50}_default_vec"
    assert len(raw_index) > 63

    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "c" * 50
    collection._distance = "cosine"
    collection._indexes = {}
    collection._pending_indexes = {}
    meta = collection._normalized_index_meta(
        "default",
        {"VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"}},
    )
    physical = meta["_pg_index_name"]
    assert len(physical) <= 63
    assert _validate_identifier(physical) == physical
    assert physical == _bounded_identifier(raw_index)
    again = collection._normalized_index_meta(
        "default",
        {"VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"}},
    )
    assert again["_pg_index_name"] == physical

    assert _bounded_identifier("idx_context_default_vec") == "idx_context_default_vec"
    assert _index_meta_table_name("context") == "_ov_index_context"
    long_collection = "n" * 54
    assert len(f"_ov_index_{long_collection}") > 63
    catalog = _index_meta_table_name(long_collection)
    assert len(catalog) <= 63
    assert _validate_identifier(catalog) == catalog


def test_reconnect_prefers_configured_distance_over_persisted_meta():
    adapter = _adapter_without_connect(distance_metric="l2")
    adapter._collection = None
    adapter._conn = Mock()
    adapter._table_exists = Mock(return_value=True)
    persisted = {"_dim": 8, "_distance": "cosine", "Fields": []}
    og_coll = Mock()

    with (
        patch(
            "openviking.storage.vectordb_adapters.opengauss_adapter._load_collection_meta",
            return_value=persisted,
        ),
        patch(
            "openviking.storage.vectordb_adapters.opengauss_adapter.OpenGaussCollection",
            return_value=og_coll,
        ) as collection_cls,
        patch(
            "openviking.storage.vectordb_adapters.opengauss_adapter.Collection",
            side_effect=lambda impl: impl,
        ),
        patch(
            "openviking.storage.vectordb_adapters.opengauss_adapter._save_collection_meta",
        ) as save_meta,
        patch.object(adapter, "_ensure_vector_index_exists") as ensure_index,
    ):
        adapter._load_existing_collection_if_needed()

    assert persisted["_distance"] == "l2"
    save_meta.assert_called_once()
    assert collection_cls.call_args.args[4] == "l2"
    ensure_index.assert_called_once_with(og_coll, "l2")


def test_reconnect_does_not_rewrite_meta_when_distance_matches():
    adapter = _adapter_without_connect(distance_metric="cosine")
    adapter._collection = None
    adapter._conn = Mock()
    adapter._table_exists = Mock(return_value=True)
    persisted = {"_dim": 8, "_distance": "cosine", "Fields": []}
    og_coll = Mock()

    with (
        patch(
            "openviking.storage.vectordb_adapters.opengauss_adapter._load_collection_meta",
            return_value=persisted,
        ),
        patch(
            "openviking.storage.vectordb_adapters.opengauss_adapter.OpenGaussCollection",
            return_value=og_coll,
        ),
        patch(
            "openviking.storage.vectordb_adapters.opengauss_adapter.Collection",
            side_effect=lambda impl: impl,
        ),
        patch(
            "openviking.storage.vectordb_adapters.opengauss_adapter._save_collection_meta",
        ) as save_meta,
        patch.object(adapter, "_ensure_vector_index_exists") as ensure_index,
    ):
        adapter._load_existing_collection_if_needed()

    save_meta.assert_not_called()
    ensure_index.assert_called_once_with(og_coll, "cosine")


# ---------------------------------------------------------------------------
# Backend failures must propagate instead of masquerading as absence
# ---------------------------------------------------------------------------


class _UndefinedTableError(Exception):
    pgcode = "42P01"


class _PermissionDeniedError(Exception):
    pgcode = "42501"


def test_is_undefined_table_error_classification():
    assert _is_undefined_table_error(_UndefinedTableError("boom")) is True
    # A real SQLSTATE wins over any message text.
    assert (
        _is_undefined_table_error(
            _PermissionDeniedError('relation "t" does not exist')
        )
        is False
    )
    # Message text is not reliable enough to classify backend failures.
    assert _is_undefined_table_error(RuntimeError('relation "t" does not exist')) is False
    assert _is_undefined_table_error(RuntimeError("network timeout")) is False


def test_load_collection_meta_propagates_backend_errors():
    conn = Mock()
    cursor = Mock()
    conn.cursor.return_value = cursor

    def execute(sql, params=None):
        if sql.lstrip().startswith("SELECT"):
            raise RuntimeError("connection dropped")

    cursor.execute.side_effect = execute
    with pytest.raises(RuntimeError, match="connection dropped"):
        _load_collection_meta(conn, "context")
    conn.rollback.assert_called_once_with()


def test_load_collection_meta_returns_none_only_without_row():
    conn = Mock()
    cursor = Mock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = None

    assert _load_collection_meta(conn, "context") is None
    conn.rollback.assert_not_called()


def test_table_exists_propagates_backend_errors():
    adapter = _adapter_without_connect()
    conn = Mock()
    cursor = Mock()
    conn.cursor.return_value = cursor
    cursor.execute.side_effect = RuntimeError("permission denied")
    adapter._conn = conn

    with pytest.raises(RuntimeError, match="permission denied"):
        adapter._table_exists("context")
    conn.rollback.assert_called_once_with()


def test_pure_sparse_query_raises_not_implemented():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    with pytest.raises(NotImplementedError, match="sparse"):
        collection.search_by_vector(
            "default",
            dense_vector=None,
            sparse_vector={"term": 1.0},
        )
    # Empty dense query without sparse terms still short-circuits gracefully.
    assert collection.search_by_vector("default", dense_vector=None).data == []


def test_search_by_id_propagates_backend_errors():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._execute = Mock(side_effect=RuntimeError("connection dropped"))

    with pytest.raises(RuntimeError, match="connection dropped"):
        collection.search_by_id("default", "doc-1")


def test_search_by_id_applies_offset_after_excluding_source_record():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._execute = Mock(return_value=[("[1,0]",)])
    collection.search_by_vector = Mock(
        return_value=Mock(
            data=[
                Mock(id="doc-1"),
                Mock(id="neighbor-1"),
                Mock(id="neighbor-2"),
                Mock(id="neighbor-3"),
            ]
        )
    )

    result = collection.search_by_id("default", "doc-1", limit=2, offset=1)

    collection.search_by_vector.assert_called_once_with(
        "default",
        dense_vector=[1.0, 0.0],
        limit=4,
        offset=0,
        filters=None,
        output_fields=None,
    )
    assert [item.id for item in result.data] == ["neighbor-2", "neighbor-3"]


def test_drop_index_keeps_registry_when_database_drop_fails():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._indexes = {
        "default": {"_pg_index_name": "idx_context_default_vec"}
    }
    collection._pending_indexes = {}
    collection._lock = __import__("threading").RLock()
    collection._conn = Mock()
    cursor = Mock()
    cursor.execute.side_effect = RuntimeError("permission denied")
    collection._conn.cursor.return_value = cursor

    with pytest.raises(RuntimeError, match="permission denied"):
        collection.drop_index("default")

    assert "default" in collection._indexes


def test_drop_index_removes_ann_scalar_indexes_and_metadata_atomically():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._indexes = {
        "default": {
            "_pg_index_name": "idx_context_default_vec",
            "ScalarIndex": ["uri", "level"],
        }
    }
    collection._pending_indexes = {}
    collection._lock = threading.RLock()
    collection._conn = Mock()
    cursor = Mock()
    collection._conn.cursor.return_value = cursor

    collection.drop_index("default")

    executed_sql = [item.args[0] for item in cursor.execute.call_args_list]
    assert 'DROP INDEX IF EXISTS "idx_context_default_vec"' in executed_sql
    assert 'DROP INDEX IF EXISTS "idx_context_default_uri"' in executed_sql
    assert 'DROP INDEX IF EXISTS "idx_context_default_level"' in executed_sql
    assert any(sql.startswith("DELETE FROM") for sql in executed_sql)
    collection._conn.commit.assert_called_once_with()
    assert "default" not in collection._indexes


def test_reconcile_missing_ann_cleans_scalar_indexes_before_registry_removal():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    meta = {
        "_pg_index_name": "idx_context_default_vec",
        "ScalarIndex": ["uri"],
        "VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"},
    }
    collection._indexes = {"default": meta}
    collection._physical_index_definition = Mock(return_value=None)
    collection._normalized_index_meta = Mock(return_value=meta)
    collection._delete_index_meta_and_scalar_indexes = Mock()

    collection._reconcile_index_metadata()

    collection._delete_index_meta_and_scalar_indexes.assert_called_once_with(
        "default", meta
    )
    assert "default" not in collection._indexes


def test_reconcile_keeps_registry_when_scalar_cleanup_fails():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    meta = {
        "_pg_index_name": "idx_context_default_vec",
        "ScalarIndex": ["uri"],
        "VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"},
    }
    collection._indexes = {"default": meta}
    collection._physical_index_definition = Mock(return_value=None)
    collection._normalized_index_meta = Mock(return_value=meta)
    collection._delete_index_meta_and_scalar_indexes = Mock(
        side_effect=RuntimeError("cleanup failed")
    )

    with pytest.raises(RuntimeError, match="cleanup failed"):
        collection._reconcile_index_metadata()

    assert collection._indexes["default"] == meta


def test_update_index_accepts_scalar_field_list_and_removes_stale_indexes():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._field_names = {"id", "vector", "uri", "level"}
    collection._indexes = {
        "default": {
            "_pg_index_name": "idx_context_default_vec",
            "ScalarIndex": ["uri"],
        }
    }
    collection._pending_indexes = {}
    collection._lock = threading.RLock()
    collection._conn = Mock()
    collection._ensure_index_meta_table = Mock()
    cursor = Mock()
    cursor.rowcount = 1
    cursor.fetchone.return_value = None
    collection._conn.cursor.return_value = cursor

    collection.update_index("default", ["level"], "updated")

    executed_sql = [item.args[0] for item in cursor.execute.call_args_list]
    assert 'DROP INDEX IF EXISTS "idx_context_default_uri"' in executed_sql
    assert any(
        sql.startswith('CREATE INDEX "idx_context_default_level"')
        for sql in executed_sql
    )
    assert collection._indexes["default"]["ScalarIndex"] == ["level"]
    assert collection._indexes["default"]["Description"] == "updated"


def test_update_index_empty_scalar_list_clears_indexes():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._field_names = {"id", "vector", "uri"}
    collection._indexes = {
        "default": {
            "_pg_index_name": "idx_context_default_vec",
            "ScalarIndex": ["uri"],
        }
    }
    collection._pending_indexes = {}
    collection._lock = threading.RLock()
    collection._conn = Mock()
    collection._ensure_index_meta_table = Mock()
    cursor = Mock()
    cursor.rowcount = 1
    cursor.fetchone.return_value = None
    collection._conn.cursor.return_value = cursor

    collection.update_index("default", [])

    assert collection._indexes["default"]["ScalarIndex"] == []
    assert 'DROP INDEX IF EXISTS "idx_context_default_uri"' in [
        item.args[0] for item in cursor.execute.call_args_list
    ]


def test_create_collection_cleans_partial_collection_after_index_failure():
    adapter = _adapter_without_connect()
    adapter._collection = None
    partial_collection = Mock()
    partial_collection.create_index.side_effect = RuntimeError("index creation failed")
    adapter._create_backend_collection = Mock(return_value=partial_collection)
    adapter._table_exists = Mock(return_value=False)

    with patch.object(adapter, "collection_exists", return_value=False):
        with pytest.raises(RuntimeError, match="index creation failed"):
            adapter.create_collection(
                "context",
                {"Fields": []},
                distance="cosine",
                sparse_weight=0.0,
                index_name="default",
            )

    partial_collection.drop.assert_called_once_with()
    assert adapter._collection is None


def test_create_collection_rejects_unregistered_orphan_table():
    adapter = _adapter_without_connect()
    adapter._conn = Mock()
    adapter._table_exists = Mock(return_value=True)

    with patch(
        "openviking.storage.vectordb_adapters.opengauss_adapter._load_collection_meta",
        return_value=None,
    ):
        with pytest.raises(RuntimeError, match="orphan table"):
            adapter.create_collection(
                "context",
                {"Fields": []},
                distance="cosine",
                sparse_weight=0.0,
                index_name="default",
            )


def test_distributed_conversion_failure_cleans_created_local_table():
    adapter = _adapter_without_connect(distributed=True)
    adapter._conn = Mock()
    cleanup_cursor = Mock()
    adapter._conn.cursor.return_value = cleanup_cursor
    meta = {
        "Fields": [
            {"FieldName": "vector", "FieldType": "vector", "Dim": 8},
        ]
    }

    def fail_after_table_creation(*args, **kwargs):
        kwargs["on_table_created"]()
        raise RuntimeError("distribution failed")

    with patch(
        "openviking.storage.vectordb_adapters.opengauss_adapter._create_collection_table",
        side_effect=fail_after_table_creation,
    ):
        with pytest.raises(RuntimeError, match="distribution failed"):
            adapter._create_backend_collection(meta)

    executed_sql = [item.args[0] for item in cleanup_cursor.execute.call_args_list]
    assert 'DROP TABLE IF EXISTS "context" CASCADE' in executed_sql
    adapter._conn.commit.assert_called_once_with()


def test_explicit_quantization_flags_require_training_data():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    assert collection._index_requires_data(
        {
            "_pg_index_type": "hnsw",
            "_quantization": None,
            "build_params": {"enable_pq": True},
        }
    )
    assert collection._index_requires_data(
        {
            "_pg_index_type": "hnsw",
            "_quantization": None,
            "build_params": {"enable_rabitq": True},
        }
    )
    assert not collection._index_requires_data(
        {
            "_pg_index_type": "hnsw",
            "_quantization": None,
            "build_params": {},
        }
    )


def test_update_data_rejects_missing_records_and_requires_primary_key():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    with pytest.raises(ValueError, match="primary key 'id'"):
        collection.update_data([{"uri": "viking://resources/new"}])

    collection._name = "context"
    collection._dim = 2
    collection._date_time_fields = set()
    collection._lock = threading.RLock()
    collection._conn = Mock()
    collection._get_all_columns = Mock(return_value=["id", "uri"])
    collection._get_column_types = Mock(return_value={"id": "character varying", "uri": "text"})
    cursor = Mock()
    cursor.rowcount = 0
    collection._conn.cursor.return_value = cursor
    with pytest.raises(ValueError, match="record not found"):
        collection.update_data([{"id": "missing", "uri": "viking://resources/new"}])
    collection._conn.rollback.assert_called_once_with()


def test_update_data_preserves_unspecified_fields():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._dim = 2
    collection._date_time_fields = set()
    collection._lock = threading.RLock()
    collection._conn = Mock()
    collection._get_all_columns = Mock(return_value=["id", "uri", "level"])
    collection._get_column_types = Mock(
        return_value={"id": "character varying", "uri": "text", "level": "bigint"}
    )
    cursor = Mock()
    cursor.rowcount = 1
    collection._conn.cursor.return_value = cursor

    updated_ids = collection.update_data([{"id": "doc-1", "level": 2}])

    update_sql, update_params = cursor.execute.call_args.args
    assert 'SET "level" = %s' in update_sql
    assert '"uri"' not in update_sql
    assert update_params == [2, "doc-1"]
    assert updated_ids == ["doc-1"]
    collection._conn.commit.assert_called_once_with()


def test_adapter_exposes_transactional_update_data():
    adapter = _adapter_without_connect()
    adapter._collection = Mock()
    adapter._collection.update_data.return_value = ["doc-1"]

    assert adapter.update_data([{"id": "doc-1", "level": 2}]) == ["doc-1"]
    adapter._collection.update_data.assert_called_once_with(
        [{"id": "doc-1", "level": 2}]
    )


def test_adapter_update_normalizes_all_uri_fields_before_write():
    adapter = _adapter_without_connect()
    adapter._collection = Mock()
    adapter._collection.update_data.return_value = ["doc-1"]
    record = {
        "id": "doc-1",
        "uri": "viking://resources/demo",
        "parent_uri": "viking://resources",
    }

    assert adapter.update_data([record]) == ["doc-1"]

    adapter._collection.update_data.assert_called_once_with(
        [
            {
                "id": "doc-1",
                "uri": "/resources/demo",
                "parent_uri": "/resources",
            }
        ]
    )
    assert record["uri"] == "viking://resources/demo"


def test_runtime_index_creation_reuses_config_validation():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._dim = 8
    collection._persist_index_meta_and_scalar_indexes = Mock()

    with pytest.raises(ValueError, match="cannot be enabled together"):
        collection.create_index(
            "invalid",
            {
                "VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"},
                "build_params": {"enable_pq": True, "enable_rabitq": True},
            },
        )
    collection._persist_index_meta_and_scalar_indexes.assert_not_called()


def test_runtime_index_validation_preserves_parallel_workers():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._dim = 8
    collection._distance = "cosine"
    collection._field_names = {"id", "vector"}
    collection._indexes = {}
    collection._pending_indexes = {}
    collection._persist_index_meta_and_scalar_indexes = Mock()
    collection._table_has_rows = Mock(return_value=False)
    collection._physical_index_definition = Mock(return_value=None)

    collection.create_index(
        "default",
        {
            "VectorIndex": {"IndexType": "ivfflat", "Distance": "cosine"},
            "build_params": {"lists": 10, "parallel_workers": 4},
        },
    )

    assert collection._pending_indexes["default"]["build_params"]["parallel_workers"] == 4


def test_reconnect_reapplies_parallel_workers_for_matching_index():
    adapter = _adapter_without_connect(parallel_workers=8)
    collection = Mock()
    collection._indexes = {}
    collection._pending_indexes = {}
    collection._meta = {}
    normalized_meta = {
        "_pg_index_name": "idx_context_default_vec",
        "build_params": {"parallel_workers": 8},
        "ScalarIndex": [],
    }
    collection._normalized_index_meta.return_value = normalized_meta
    collection._physical_index_definition.return_value = "matching index"
    collection._index_definition_matches.return_value = True

    adapter._ensure_vector_index_exists(collection, "cosine")

    collection._apply_parallel_workers.assert_called_once_with(normalized_meta)


def test_connection_proxy_returns_closed_connection_before_replacement():
    pool = Mock()
    closed_connection = Mock()
    closed_connection.closed = True
    replacement = Mock()
    replacement.closed = False
    pool.getconn.return_value = replacement
    proxy = _PooledConnectionProxy(pool)
    proxy._local.connection = closed_connection
    proxy._local.cursor_count = 0

    assert proxy._checkout() is replacement
    pool.putconn.assert_called_once_with(closed_connection, close=True)


def test_connection_proxy_releases_connection_when_cursor_creation_fails():
    pool = Mock()
    connection = Mock()
    connection.closed = False
    connection.cursor.side_effect = RuntimeError("cursor failed")
    pool.getconn.return_value = connection
    proxy = _PooledConnectionProxy(pool)

    with pytest.raises(RuntimeError, match="cursor failed"):
        proxy.cursor()

    pool.putconn.assert_called_once_with(connection, close=False)
    assert proxy._local.connection is None
    assert proxy._local.cursor_count == 0


def test_connection_proxy_returns_connection_when_checkout_initialization_fails():
    pool = Mock()
    connection = Mock()
    type(connection).autocommit = property(
        fset=Mock(side_effect=RuntimeError("connection lost"))
    )
    pool.getconn.return_value = connection
    proxy = _PooledConnectionProxy(pool)

    with pytest.raises(RuntimeError, match="connection lost"):
        proxy._checkout()

    pool.putconn.assert_called_once_with(connection, close=True)
    assert getattr(proxy._local, "connection", None) is None


def test_adapter_connect_closes_pool_when_metadata_initialization_fails():
    adapter = _adapter_without_connect()
    pool = Mock()
    with (
        patch(
            "openviking.storage.vectordb_adapters.opengauss_adapter._create_connection_pool",
            return_value=pool,
        ),
        patch(
            "openviking.storage.vectordb_adapters.opengauss_adapter._ensure_meta_table",
            side_effect=RuntimeError("permission denied"),
        ),
    ):
        with pytest.raises(RuntimeError, match="permission denied"):
            adapter._connect()

    pool.closeall.assert_called_once_with()
    assert adapter._conn is None
    assert adapter._pool is None


@pytest.mark.parametrize("parallel_workers", [-1, 33, True, 1.5, "invalid"])
def test_runtime_index_validation_rejects_invalid_parallel_workers(parallel_workers):
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._dim = 8
    collection._distance = "cosine"
    collection._create_scalar_indexes = Mock()

    with pytest.raises((ValueError, ValidationError)):
        collection.create_index(
            "default",
            {
                "VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"},
                "build_params": {"parallel_workers": parallel_workers},
            },
        )
    collection._create_scalar_indexes.assert_not_called()


@pytest.mark.parametrize("index_type", ["hnsw-pq", "hnsw-rabitq"])
def test_runtime_index_validation_rejects_quantized_l1(index_type):
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._dim = 8
    collection._distance = "l1"
    collection._create_scalar_indexes = Mock()

    with pytest.raises(ValueError, match="plain hnsw"):
        collection.create_index(
            "default",
            {
                "VectorIndex": {"IndexType": index_type, "Distance": "l1"},
            },
        )
    collection._create_scalar_indexes.assert_not_called()


def test_pending_index_metadata_survives_reconnect():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._indexes = {}
    collection._pending_indexes = {}
    collection._execute = Mock(
        return_value=[
            (
                "secondary",
                json.dumps(
                    {
                        "_state": "pending",
                        "_pg_index_name": "idx_context_secondary_vec",
                    }
                ),
            )
        ]
    )

    collection._load_index_meta()

    assert "secondary" in collection._pending_indexes
    assert "secondary" not in collection._indexes


def test_filter_delete_executes_direct_sql_with_stable_limit():
    adapter = _adapter_without_connect()
    adapter._collection = Mock()
    adapter._collection.get_meta_data.return_value = {
        "Fields": [{"FieldName": "account_id", "FieldType": "string"}]
    }
    adapter._conn = Mock()
    cursor = Mock()
    cursor.rowcount = 100001
    adapter._conn.cursor.return_value = cursor

    deleted_count = adapter.delete(filter=Eq("account_id", "acct-1"))

    assert deleted_count == 100001
    delete_sql, delete_params = cursor.execute.call_args.args
    assert delete_sql.startswith('DELETE FROM "context" WHERE')
    assert "ORDER BY id LIMIT %s" in delete_sql
    assert delete_params == ["acct-1", 100000]


def test_save_index_meta_recovers_cross_process_unique_race():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._distributed = False
    collection._lock = threading.RLock()
    collection._ensure_index_meta_table = Mock()
    collection._conn = Mock()
    first_cursor = Mock()
    first_cursor.rowcount = 0
    unique_error = RuntimeError("duplicate key")
    unique_error.pgcode = "23505"
    first_cursor.execute.side_effect = [None, unique_error]
    retry_cursor = Mock()
    retry_cursor.rowcount = 1
    collection._conn.cursor.side_effect = [first_cursor, retry_cursor]

    collection._save_index_meta("default", {"_state": "pending"})

    collection._conn.rollback.assert_called_once_with()
    collection._conn.commit.assert_called_once_with()
    assert "UPDATE" in retry_cursor.execute.call_args.args[0]


def test_atomic_scalar_metadata_persistence_recovers_unique_race():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._field_names = {"id", "vector", "uri"}
    collection._lock = threading.RLock()
    collection._ensure_index_meta_table = Mock()
    collection._conn = Mock()
    first_cursor = Mock()
    retry_cursor = Mock()
    unique_error = RuntimeError("duplicate key")
    unique_error.pgcode = "23505"

    def first_execute(sql, params=None):
        if sql.startswith("UPDATE"):
            first_cursor.rowcount = 0
        elif sql.startswith("INSERT"):
            raise unique_error

    def retry_execute(sql, params=None):
        if sql.startswith("UPDATE"):
            retry_cursor.rowcount = 1

    first_cursor.execute.side_effect = first_execute
    retry_cursor.execute.side_effect = retry_execute
    collection._conn.cursor.side_effect = [first_cursor, retry_cursor]

    collection._persist_index_meta_and_scalar_indexes(
        "default",
        {"ScalarIndex": ["uri"], "_state": "pending"},
    )

    collection._conn.rollback.assert_called_once_with()
    collection._conn.commit.assert_called_once_with()
    assert any(
        item.args[0].startswith("CREATE INDEX IF NOT EXISTS")
        for item in retry_cursor.execute.call_args_list
    )
    assert any(
        item.args[0].startswith("UPDATE")
        for item in retry_cursor.execute.call_args_list
    )


def test_search_sql_uses_stable_id_tiebreaker():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._dim = 2
    collection._distance = "cosine"
    collection._field_names = {"id", "vector", "level"}
    collection._array_fields = set()
    collection._date_time_fields = set()
    collection._indexes = {"default": {"_distance": "cosine"}}
    collection._pending_indexes = {}
    collection._lock = threading.RLock()
    collection._conn = Mock()
    cursor = Mock()
    cursor.description = [("id",), ("vector",), ("_distance",)]
    cursor.fetchall.return_value = []
    collection._conn.cursor.return_value = cursor
    collection._materialize_pending_index = Mock()
    collection.has_index = Mock(return_value=True)
    collection._apply_search_params_on_cursor = Mock()

    collection.search_by_vector("default", dense_vector=[1.0, 0.0])
    assert "ORDER BY _distance, id" in cursor.execute.call_args.args[0]

    cursor.description = [("id",), ("level",), ("_scalar_val",)]
    cursor.fetchall.return_value = []
    collection.search_by_scalar("default", field="level", order="desc")
    assert "ORDER BY \"level\" DESC, id DESC" in cursor.execute.call_args.args[0]


def test_healthy_index_query_ignores_unrelated_failing_pending_index():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._dim = 2
    collection._distance = "cosine"
    collection._field_names = {"id", "vector"}
    collection._array_fields = set()
    collection._date_time_fields = set()
    collection._indexes = {"default": {"_distance": "cosine"}}
    collection._pending_indexes = {"secondary": {"_distance": "cosine"}}
    collection._bulk_ingest_depth = 0
    collection._lock = threading.RLock()
    collection._conn = Mock()
    cursor = Mock()
    cursor.description = [("id",), ("vector",), ("_distance",)]
    cursor.fetchall.return_value = []
    collection._conn.cursor.return_value = cursor
    collection._table_has_rows = Mock(return_value=True)
    collection._materialize_index = Mock(
        side_effect=RuntimeError("secondary build failed")
    )
    collection.has_index = Mock(return_value=True)
    collection._apply_search_params_on_cursor = Mock()

    result = collection.search_by_vector("default", dense_vector=[1.0, 0.0])

    assert result.data == []
    collection._materialize_index.assert_not_called()


def test_query_materializes_only_requested_pending_index():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    requested_meta = {"_distance": "cosine"}
    collection._pending_indexes = {
        "requested": requested_meta,
        "secondary": {"_distance": "cosine"},
    }
    collection._bulk_ingest_depth = 0
    collection._table_has_rows = Mock(return_value=True)
    collection._materialize_index = Mock()

    collection._materialize_pending_index("requested")

    collection._materialize_index.assert_called_once_with("requested", requested_meta)


def test_upsert_does_not_report_committed_rows_as_failed_when_index_build_fails():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._dim = 2
    collection._field_names = {"id", "vector"}
    collection._date_time_fields = set()
    collection._lock = threading.RLock()
    collection._conn = Mock()
    cursor = Mock()
    cursor.rowcount = 0
    collection._conn.cursor.return_value = cursor
    collection._get_all_columns = Mock(return_value=["id", "vector"])
    collection._get_column_types = Mock(return_value={"id": "character varying", "vector": "USER-DEFINED"})
    collection._materialize_pending_indexes = Mock(
        side_effect=RuntimeError("index creation failed")
    )

    collection.upsert_data([{"id": "doc-1", "vector": [1.0, 0.0]}])

    collection._conn.commit.assert_called_once_with()
    collection._materialize_pending_indexes.assert_called_once_with()


def test_upsert_recovers_concurrent_insert_unique_race():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._dim = 2
    collection._field_names = {"id", "vector"}
    collection._date_time_fields = set()
    collection._lock = threading.RLock()
    collection._conn = Mock()
    cursor = Mock()
    unique_error = RuntimeError("duplicate key")
    unique_error.pgcode = "23505"
    rowcounts = iter([0, 1])

    def execute(sql, params=None):
        if sql.startswith("UPDATE"):
            cursor.rowcount = next(rowcounts)
        elif sql.startswith("INSERT"):
            raise unique_error

    cursor.execute.side_effect = execute
    collection._conn.cursor.return_value = cursor
    collection._get_all_columns = Mock(return_value=["id", "vector"])
    collection._get_column_types = Mock(
        return_value={"id": "character varying", "vector": "USER-DEFINED"}
    )
    collection._materialize_pending_indexes = Mock()

    collection.upsert_data([{"id": "doc-1", "vector": [1.0, 0.0]}])

    executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert executed_sql == [
        'UPDATE "context" SET "vector" = %s::vector WHERE "id" = %s',
        "SAVEPOINT ov_upsert_insert",
        'INSERT INTO "context" ("id", "vector") VALUES (%s, %s::vector)',
        "ROLLBACK TO SAVEPOINT ov_upsert_insert",
        'UPDATE "context" SET "vector" = %s::vector WHERE "id" = %s',
        "RELEASE SAVEPOINT ov_upsert_insert",
    ]
    collection._conn.commit.assert_called_once_with()


def test_load_index_meta_tolerates_only_missing_catalog_table():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._indexes = {}

    collection._execute = Mock(
        side_effect=_UndefinedTableError('relation "_ov_index_context" does not exist')
    )
    collection._load_index_meta()
    assert collection._indexes == {}

    collection._execute = Mock(side_effect=_PermissionDeniedError("permission denied"))
    with pytest.raises(_PermissionDeniedError):
        collection._load_index_meta()


def test_delete_index_meta_tolerates_only_missing_catalog_table():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"

    collection._execute = Mock(
        side_effect=_UndefinedTableError('relation "_ov_index_context" does not exist')
    )
    collection._delete_index_meta("default")

    collection._execute = Mock(side_effect=RuntimeError("connection dropped"))
    with pytest.raises(RuntimeError, match="connection dropped"):
        collection._delete_index_meta("default")


def test_is_table_already_distributed_propagates_non_catalog_errors():
    conn = Mock()
    cursor = Mock()
    conn.cursor.return_value = cursor
    cursor.execute.side_effect = _UndefinedTableError(
        'relation "pg_dist_partition" does not exist'
    )
    assert _is_table_already_distributed(conn, "context") is False

    broken_conn = Mock()
    broken_cursor = Mock()
    broken_conn.cursor.return_value = broken_cursor
    broken_cursor.execute.side_effect = RuntimeError("node down")
    with pytest.raises(RuntimeError, match="node down"):
        _is_table_already_distributed(broken_conn, "context")
    broken_conn.rollback.assert_called_once_with()
