# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking.storage.vectordb_adapters.opengauss_adapter import (
    OpenGaussCollection,
    OpenGaussCollectionAdapter,
    _build_path_scope_clause,
    _build_where_clause,
    _create_collection_table,
    _date_time_to_epoch_ms,
    _distance_to_similarity,
    _field_to_column_ddl,
    _normalize_index_type,
    _resolve_build_params,
)
from openviking_cli.utils.config.vectordb_config import (
    OpenGaussConfig,
    VectorDBBackendConfig,
    validate_opengauss_vector_constraints,
)


def test_opengauss_index_type_defaults_and_variants():
    assert OpenGaussConfig().index_type == "hnsw"
    assert OpenGaussConfig(index_type="ivfflat", build_params={"lists": 128}).index_type == "ivfflat"
    assert OpenGaussConfig(
        index_type="diskann",
        build_params={"index_size": 64},
        search_params={"probes": 32},
    ).index_type == "diskann"


def test_opengauss_hnsw_param_validation():
    with pytest.raises(ValidationError, match="ef_construction"):
        OpenGaussConfig(index_type="hnsw", build_params={"m": 32, "ef_construction": 40})
    with pytest.raises(ValidationError, match="lists"):
        OpenGaussConfig(index_type="ivfflat", build_params={"lists": 0})
    with pytest.raises(ValidationError, match="index_size"):
        OpenGaussConfig(index_type="diskann", build_params={"index_size": 8})
    with pytest.raises(ValidationError, match="maintenance_work_mem_mb"):
        OpenGaussConfig(maintenance_work_mem_mb=15)


@pytest.mark.parametrize(
    ("index_type", "build_params"),
    [
        ("hnsw-pq", {"pq_m": 8}),
        ("hnsw-rabitq", {"rabitq_refine_type": "FP32"}),
        ("ivfflat", {"lists": 1}),
        ("ivf-pq", {"lists": 1, "pq_m": 8}),
        ("ivf-rabitq", {"lists": 1}),
        ("diskann", {"index_size": 50}),
    ],
)
def test_opengauss_distributed_mode_rejects_non_plain_hnsw(index_type, build_params):
    with pytest.raises(ValidationError, match="supports only plain HNSW"):
        OpenGaussConfig(
            mode="distributed",
            index_type=index_type,
            build_params=build_params,
        )


@pytest.mark.parametrize("index_type", ["diskann-pq", "diskann_pq", "diskannpq"])
def test_opengauss_rejects_diskann_pq_index_types(index_type):
    with pytest.raises(ValidationError, match="Invalid openGauss index_type"):
        OpenGaussConfig(index_type=index_type)


@pytest.mark.parametrize(
    "build_params",
    [
        {"index_size": 50, "enable_pq": True},
        {"index_size": 50, "pq_m": 8},
        {"index_size": 50, "enable_pq": True, "pq_m": 8},
    ],
)
def test_opengauss_rejects_diskann_pq_build_parameters(build_params):
    with pytest.raises(ValidationError, match="Unsupported openGauss diskann build_params"):
        OpenGaussConfig(index_type="diskann", build_params=build_params)


def test_opengauss_index_build_memory_defaults_to_safe_transaction_value():
    config = OpenGaussConfig()

    assert config.maintenance_work_mem_mb == 64


def test_normalize_and_resolve_index_helpers():
    assert _normalize_index_type("HNSW") == "hnsw"
    assert _normalize_index_type("ivf_flat") == "ivfflat"
    assert _normalize_index_type("diskann_v1") == "diskann"
    assert _resolve_build_params("hnsw", {"m": 16, "build_params": {"ef_construction": 80}}) == {
        "ef_construction": 80,
        "m": 16,
    }


def test_build_where_clause_supports_common_filters():
    sql, params = _build_where_clause(
        {
            "op": "and",
            "conds": [
                {"op": "must", "field": "uri", "conds": ["/a"], "para": "-d=1"},
                {"op": "range", "field": "score", "gte": 1, "lt": 10},
                {"op": "contains", "field": "abstract", "substring": "hello"},
            ],
        }
    )
    assert 'rtrim(btrim("uri"),' in sql
    assert 'LIKE %s ESCAPE' in sql
    assert '"score" >= %s' in sql
    assert '"abstract" LIKE %s ESCAPE' in sql
    assert params == ["/a", "/a/%", 1, 1, 10, "%hello%"]


def test_factory_routes_opengauss_backend():
    config = VectorDBBackendConfig(
        backend="opengauss",
        opengauss=OpenGaussConfig(
            host="127.0.0.1",
            index_type="ivfflat",
            build_params={"lists": 200},
            search_params={"probes": 14},
        ),
    )
    with patch.object(OpenGaussCollectionAdapter, "_connect", return_value=None):
        adapter = create_collection_adapter(config)
    assert isinstance(adapter, OpenGaussCollectionAdapter)
    assert adapter.mode == "opengauss"
    assert adapter._index_type == "ivfflat"
    assert adapter._build_params["lists"] == 200
    assert adapter._search_params["probes"] == 14


def test_build_default_index_meta_uses_configured_index_knobs():
    with patch.object(OpenGaussCollectionAdapter, "_connect", return_value=None):
        adapter = OpenGaussCollectionAdapter(
            collection_name="context",
            host="127.0.0.1",
            port=5432,
            user="gaussdb",
            password="",
            db_name="openviking",
            index_type="hnsw",
            build_params={"m": 16, "ef_construction": 64},
            search_params={"ef_search": 100},
            distance_metric="cosine",
            maintenance_work_mem_mb=128,
        )
    meta = adapter._build_default_index_meta(
        index_name="default",
        distance="cosine",
        use_sparse=False,
        sparse_weight=0.0,
        scalar_index_fields=["uri"],
    )
    assert meta["VectorIndex"]["IndexType"] == "hnsw"
    assert meta["build_params"]["m"] == 16
    assert meta["search_params"]["ef_search"] == 100
    assert meta["maintenance_work_mem_mb"] == 128
    assert meta["ScalarIndex"] == ["uri"]


def test_direct_adapter_constructor_validates_and_normalizes_all_config_fields():
    with patch.object(OpenGaussCollectionAdapter, "_connect", return_value=None):
        adapter = OpenGaussCollectionAdapter(
            collection_name="context",
            host="host",
            port="5432",
            user="u",
            password="p",
            db_name="db",
            distributed=False,
            shard_count="4",
            index_type="hnsw",
            maintenance_work_mem_mb="128",
            connection_pool_min_size="2",
            connection_pool_max_size="4",
        )

    assert adapter._port == 5432
    assert adapter._shard_count == 4
    assert adapter._maintenance_work_mem_mb == 128
    assert adapter._connection_pool_min_size == 2
    assert adapter._connection_pool_max_size == 4


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"port": 0},
        {"shard_count": 0},
        {"maintenance_work_mem_mb": 15},
        {"connection_pool_min_size": 0},
        {"connection_pool_min_size": 5, "connection_pool_max_size": 4},
    ],
)
def test_direct_adapter_constructor_rejects_invalid_config_before_connect(invalid_fields):
    kwargs = {
        "collection_name": "context",
        "host": "host",
        "port": 5432,
        "user": "u",
        "password": "p",
        "db_name": "db",
        "index_type": "hnsw",
    }
    kwargs.update(invalid_fields)

    with patch.object(OpenGaussCollectionAdapter, "_connect") as connect:
        with pytest.raises(ValidationError):
            OpenGaussCollectionAdapter(**kwargs)
    connect.assert_not_called()


@pytest.mark.parametrize(
    ("distance_metric", "distance", "expected_score"),
    [
        ("cosine", 0.0, 1.0),
        ("cosine", 1.0, 0.0),
        ("ip", -0.75, 0.75),
        ("l2", 0.0, 1.0),
        ("l2", 3.0, 0.25),
        ("l1", 1.0, 0.5),
    ],
)
def test_distance_to_similarity_uses_higher_is_better_scores(
    distance_metric, distance, expected_score
):
    assert _distance_to_similarity(distance_metric, distance) == pytest.approx(expected_score)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1767225600000, 1767225600000),
        ("1767225600000", 1767225600000),
        ("2026-01-01T00:00:00Z", 1767225600000),
        ("2026-01-01T08:00:00+08:00", 1767225600000),
    ],
)
def test_date_time_to_epoch_ms_accepts_epoch_and_iso_values(value, expected):
    assert _date_time_to_epoch_ms(value) == expected


def test_opengauss_field_types_match_openviking_storage_contract():
    assert _field_to_column_ddl({"FieldName": "created_at", "FieldType": "date_time"}) == (
        '"created_at" BIGINT'
    )
    assert _field_to_column_ddl({"FieldName": "search_tags", "FieldType": "list<string>"}) == (
        '"search_tags" TEXT[]'
    )
    assert _field_to_column_ddl({"FieldName": "sparse_vector", "FieldType": "sparse_vector"}) == (
        '"sparse_vector" JSONB'
    )


def test_build_where_clause_supports_array_membership_negation_and_prefix():
    sql, params = _build_where_clause(
        {
            "op": "and",
            "conds": [
                {"op": "must", "field": "search_tags", "conds": ["env=prod"]},
                {"op": "must_not", "field": "search_tags", "conds": ["hidden=true"]},
                {"op": "prefix", "field": "uri", "prefix": "/resources/demo_"},
            ],
        },
        {"search_tags"},
    )
    assert '%s = ANY("search_tags")' in sql
    assert 'NOT (%s = ANY("search_tags"))' in sql
    assert '"uri" LIKE %s ESCAPE' in sql
    assert params == ["env=prod", "hidden=true", r"/resources/demo\_%"]


def test_build_where_clause_rejects_unsupported_filter_operations():
    with pytest.raises(NotImplementedError, match="regex"):
        _build_where_clause({"op": "regex", "field": "name", "pattern": ".*"})


def test_empty_in_and_or_are_contradictions_not_unfiltered_scans():
    sql, params = _build_where_clause({"op": "must", "field": "id", "conds": []})
    assert sql == "FALSE"
    assert params == []

    sql, params = _build_where_clause({"op": "or", "conds": []})
    assert sql == "FALSE"
    assert params == []

    sql, params = _build_where_clause(
        {"op": "and", "conds": [{"op": "must", "field": "id", "conds": []}]}
    )
    assert sql == "(FALSE)"
    assert params == []

    sql, params = _build_where_clause({"op": "must_not", "field": "id", "conds": []})
    assert sql == ""
    assert params == []


def test_contains_and_prefix_escape_sql_wildcards():
    sql, params = _build_where_clause(
        {"op": "contains", "field": "abstract", "substring": "100%_off"}
    )
    assert "ESCAPE '\\'" in sql
    assert params == [r"%100\%\_off%"]

    sql, params = _build_where_clause(
        {"op": "prefix", "field": "uri", "prefix": r"foo\bar%"}
    )
    assert "ESCAPE '\\'" in sql
    assert params == [r"foo\\bar\%%"]


def test_path_scope_limits_relative_depth_and_avoids_sibling_prefix_match():
    exact_sql, exact_params = _build_path_scope_clause('"uri"', "/a", 0)
    assert exact_sql.endswith("= %s")
    assert "rtrim(btrim(\"uri\"), '/')" in exact_sql
    assert exact_params == ["/a"]

    one_sql, one_params = _build_path_scope_clause('"uri"', "/a", 1)
    assert "rtrim" in one_sql
    assert "LIKE %s ESCAPE" in one_sql
    assert "substring" in one_sql
    assert "char_length" in one_sql
    assert one_params == ["/a", "/a/%", 1]
    assert "/a%" not in one_params
    assert _build_path_scope_clause('"uri"', "/a/", 0)[1] == ["/a"]

    unbounded_sql, unbounded_params = _build_path_scope_clause('"uri"', "/a", -1)
    assert "substring" not in unbounded_sql
    assert unbounded_params == ["/a", "/a/%"]

    root_sql, root_params = _build_path_scope_clause('"uri"', "/", 1)
    assert root_params == ["/", "/%", 1]
    assert "from 2" in root_sql

    exclude_sql, exclude_params = _build_where_clause(
        {"op": "must_not", "field": "uri", "conds": ["/tmp"], "para": "-d=-1"}
    )
    assert exclude_sql.startswith("NOT (")
    assert exclude_params == ["/tmp", "/tmp/%"]


def test_create_collection_table_validates_name_before_ddl():
    conn = Mock()
    with pytest.raises(ValueError, match="Invalid openGauss collection name"):
        _create_collection_table(conn, 'evil"; DROP TABLE t; --', {"Fields": []}, 0)
    conn.cursor.assert_not_called()


def test_resolved_dimension_is_revalidated_after_embedding_fill():
    config = VectorDBBackendConfig(
        backend="opengauss",
        dimension=0,
        opengauss=OpenGaussConfig(
            host="127.0.0.1",
            index_type="diskann",
            build_params={"index_size": 50},
        ),
    )
    assert config.dimension == 0
    with pytest.raises(ValueError, match="1536"):
        config.apply_resolved_dimension(3072)

    divisible = VectorDBBackendConfig(
        backend="opengauss",
        dimension=0,
        opengauss=OpenGaussConfig(
            host="127.0.0.1",
            index_type="hnsw-pq",
            build_params={"pq_m": 7},
        ),
    )
    with pytest.raises(ValueError, match="divisible"):
        divisible.apply_resolved_dimension(1030)
    validate_opengauss_vector_constraints(
        index_type="hnsw-pq",
        build_params={"pq_m": 8, "enable_pq": True},
        dimension=1024,
    )


def test_explicit_quantization_flags_enforce_dimension_limits():
    # ``hnsw`` + ``enable_rabitq`` is effectively RabitQ; the 2000-dim cap applies.
    with pytest.raises(ValidationError, match="2000"):
        VectorDBBackendConfig(
            backend="opengauss",
            dimension=3000,
            opengauss=OpenGaussConfig(
                host="127.0.0.1",
                index_type="hnsw",
                build_params={"enable_rabitq": True},
            ),
        )
    with pytest.raises(ValueError, match="2000"):
        validate_opengauss_vector_constraints(
            index_type="ivfflat",
            build_params={"enable_rabitq": True},
            dimension=2049,
        )
    # Under the cap the explicit flag remains a valid configuration.
    accepted = VectorDBBackendConfig(
        backend="opengauss",
        dimension=1024,
        opengauss=OpenGaussConfig(
            host="127.0.0.1",
            index_type="hnsw",
            build_params={"enable_rabitq": True},
        ),
    )
    assert accepted.opengauss.build_params["enable_rabitq"] is True


def test_l1_distance_rejects_explicit_quantization_flags():
    for build_params in (
        {"enable_pq": True, "pq_m": 8, "pq_ksub": 256},
        {"enable_rabitq": True},
    ):
        with pytest.raises(ValidationError, match="PQ or RabitQ"):
            VectorDBBackendConfig(
                backend="opengauss",
                distance_metric="l1",
                dimension=1024,
                opengauss=OpenGaussConfig(
                    host="127.0.0.1",
                    index_type="hnsw",
                    build_params=build_params,
                ),
            )
    plain = VectorDBBackendConfig(
        backend="opengauss",
        distance_metric="l1",
        dimension=1024,
        opengauss=OpenGaussConfig(host="127.0.0.1", index_type="hnsw"),
    )
    assert plain.distance_metric == "l1"


def test_opengauss_distance_metric_whitelist_and_normalization():
    with pytest.raises(ValidationError, match="distance_metric"):
        VectorDBBackendConfig(
            backend="opengauss",
            distance_metric="bogus",
            dimension=1024,
            opengauss=OpenGaussConfig(host="127.0.0.1"),
        )
    normalized = VectorDBBackendConfig(
        backend="opengauss",
        distance_metric="COSINE",
        dimension=1024,
        opengauss=OpenGaussConfig(host="127.0.0.1"),
    )
    assert normalized.distance_metric == "cosine"


def test_create_backend_collection_rejects_unsupported_distance_before_ddl():
    with patch.object(OpenGaussCollectionAdapter, "_connect", return_value=None):
        adapter = OpenGaussCollectionAdapter(
            collection_name="context",
            host="127.0.0.1",
            port=5432,
            user="u",
            password="p",
            db_name="d",
            distance_metric="bogus",
            index_type="hnsw",
        )
    adapter._conn = Mock()
    meta = {"Fields": [{"FieldName": "vector", "FieldType": "vector", "Dim": 8}]}
    with pytest.raises(ValueError, match="distance"):
        adapter._create_backend_collection(meta)
    adapter._conn.cursor.assert_not_called()


def test_from_config_ignores_unvalidated_custom_params():
    config = VectorDBBackendConfig(
        backend="opengauss",
        opengauss=OpenGaussConfig(
            host="127.0.0.1",
            index_type="hnsw",
            build_params={"m": 16, "ef_construction": 64},
            search_params={"ef_search": 40},
        ),
        custom_params={
            "index_type": "hnsw-pq",
            "build_params": {"enable_pq": True, "pq_m": 0},
            "search_params": {"ef_search": 999999},
            "pq_m": 0,
        },
    )
    with patch.object(OpenGaussCollectionAdapter, "_connect", return_value=None):
        adapter = OpenGaussCollectionAdapter.from_config(config)
    assert adapter._index_type == "hnsw"
    assert adapter._build_params.get("enable_pq") is not True
    assert adapter._build_params.get("pq_m") is None
    assert adapter._search_params.get("ef_search") == 40


def test_opengauss_rejects_sparse_weight_configuration():
    with pytest.raises(ValidationError, match="sparse_weight"):
        VectorDBBackendConfig(backend="opengauss", sparse_weight=0.5)


def test_opengauss_collection_rejects_unsupported_search_modes():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    with pytest.raises(NotImplementedError, match="keyword"):
        collection.search_by_keywords("default", query="hello")
    with pytest.raises(NotImplementedError, match="multimodal"):
        collection.search_by_multimodal("default", text="hello", image=None, video=None)


@pytest.mark.parametrize("parallel_workers", [True, False, "4", 1.5])
def test_parallel_workers_requires_strict_integer(parallel_workers):
    with pytest.raises(ValidationError, match="parallel_workers"):
        OpenGaussConfig(parallel_workers=parallel_workers)
