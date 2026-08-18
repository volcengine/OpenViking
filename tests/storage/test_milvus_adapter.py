# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from openviking.storage.expr import And, Contains, Eq, In, Or, PathScope, RawDSL, TimeRange
from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking.storage.vectordb_adapters.milvus_adapter import (
    MilvusCollection,
    MilvusCollectionAdapter,
    MilvusFilterCompiler,
    _encode_scope_roots,
    _legacy_collection_name,
    _normalize_distance,
    _safe_collection_name,
)
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def _build_config() -> VectorDBBackendConfig:
    return VectorDBBackendConfig.model_validate(
        {
            "backend": "milvus",
            "project": "default",
            "name": "context",
            "index_name": "default",
            "distance_metric": "cosine",
            "milvus": {
                "uri": "./milvus.db",
                "token": "test-token",
                "db_name": "default",
                "consistency_level": "session",
                "timeout_seconds": 7,
                "dense_vector_name": "vector",
                "sparse_vector_name": "sparse_vector",
            },
        }
    )


def _schema() -> dict:
    return {
        "CollectionName": "context",
        "Description": "test collection",
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "uri", "FieldType": "path"},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 2},
            {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
            {"FieldName": "abstract", "FieldType": "string"},
            {"FieldName": "level", "FieldType": "int64"},
            {"FieldName": "updated_at", "FieldType": "date_time"},
            {"FieldName": "search_tags", "FieldType": "list<string>"},
            {"FieldName": "account_id", "FieldType": "string"},
            {"FieldName": "acl_enabled", "FieldType": "bool", "DefaultValue": False},
            {
                "FieldName": "acl_direct_grants",
                "FieldType": "list<string>",
                "DefaultValue": [],
            },
            {
                "FieldName": "acl_inherited_grants",
                "FieldType": "list<string>",
                "DefaultValue": [],
            },
        ],
        "ScalarIndex": [
            "uri",
            "level",
            "updated_at",
            "search_tags",
            "account_id",
            "acl_enabled",
            "acl_direct_grants",
            "acl_inherited_grants",
        ],
    }


def _acl_filter(*grants: str) -> Or:
    return Or(
        [
            RawDSL({"op": "must_not", "field": "acl_enabled", "conds": [True]}),
            In("acl_direct_grants", list(grants)),
            In("acl_inherited_grants", list(grants)),
        ]
    )


def test_milvus_backend_config_validation():
    config = _build_config()

    assert config.backend == "milvus"
    assert config.milvus is not None
    assert config.milvus.uri == "./milvus.db"
    assert config.milvus.token == "test-token"
    assert config.milvus.db_name == "default"
    assert config.milvus.consistency_level == "Session"


def test_factory_creates_milvus_adapter_without_connecting():
    adapter = create_collection_adapter(_build_config())

    assert isinstance(adapter, MilvusCollectionAdapter)
    assert adapter.mode == "milvus"
    assert adapter.collection_name == "context"
    assert adapter.index_name == "default"
    assert adapter.physical_collection_name.startswith("ov_data_default_context_")


def test_default_local_factory_does_not_import_pymilvus(monkeypatch):
    import builtins

    imported = []
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "pymilvus" or name.startswith("pymilvus."):
            imported.append(name)
            raise AssertionError("default local backend imported pymilvus")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    config = VectorDBBackendConfig.model_validate({"backend": "local"})
    adapter = create_collection_adapter(config)

    assert adapter.mode == "local"
    assert imported == []


def test_augments_path_fields_on_write_and_hides_them_on_read():
    adapter = MilvusCollectionAdapter.from_config(_build_config())
    source_record = {
        "id": "1",
        "uri": "viking://resources/acme/docs/a.md",
        "vector": [0.1, 0.2],
    }

    normalized = adapter._normalize_record_for_write(source_record)

    assert normalized["uri"] == "/resources/acme/docs/a.md"
    assert normalized["parent_uri"] == "/resources/acme/docs"
    assert normalized["scope_roots"] == [
        "/",
        "/resources",
        "/resources/acme",
        "/resources/acme/docs",
    ]
    assert normalized["uri_depth"] == 4
    assert source_record["uri"] == "viking://resources/acme/docs/a.md"

    public_record = adapter._normalize_record_for_read(normalized)
    assert public_record["uri"] == "viking://resources/acme/docs/a.md"
    assert "parent_uri" not in public_record
    assert "scope_roots" not in public_record
    assert "uri_depth" not in public_record


def test_compiles_filter_exprs():
    compiler = MilvusFilterCompiler(
        {
            "account_id": "string",
            "scope_roots": "string",
            "updated_at": "date_time",
            "abstract": "string",
        }
    )

    compiled = compiler.compile(
        And(
            [
                Eq("account_id", "acme"),
                PathScope("uri", "viking://resources/acme/docs", depth=-1),
                TimeRange(
                    "updated_at",
                    start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                    end=datetime(2026, 6, 1, tzinfo=timezone.utc),
                ),
                Contains("abstract", "quarterly report"),
            ]
        )
    )

    assert compiled == (
        '(account_id == "acme") and '
        '(scope_roots like "%\\n/resources/acme/docs\\n%") and '
        '(updated_at >= "2026-05-01T00:00:00+00:00" and '
        'updated_at < "2026-06-01T00:00:00+00:00") and '
        '(abstract like "%quarterly report%")'
    )


def test_compiles_legacy_dict_filters():
    compiler = MilvusFilterCompiler(
        {
            "account_id": "string",
            "updated_at": "date_time",
            "scope_roots": "string",
        }
    )

    compiled = compiler.compile(
        {
            "op": "and",
            "conds": [
                {"op": "must", "field": "account_id", "conds": ["acme"]},
                {
                    "op": "time_range",
                    "field": "updated_at",
                    "gte": "2026-05-01T00:00:00Z",
                    "lt": "2026-06-01T00:00:00Z",
                },
                {"op": "prefix", "field": "uri", "prefix": "viking://resources/acme/docs"},
            ],
        }
    )

    assert compiled == (
        '(account_id == "acme") and '
        '(updated_at >= "2026-05-01T00:00:00Z" and '
        'updated_at < "2026-06-01T00:00:00Z") and '
        '(scope_roots like "%\\n/resources/acme/docs\\n%")'
    )


def test_acl_filters_include_legacy_null_and_array_grants():
    compiler = MilvusFilterCompiler(
        {
            "acl_enabled": "bool",
            "acl_direct_grants": "list<string>",
            "acl_inherited_grants": "list<string>",
        }
    )

    compiled = compiler.compile(
        Or(
            [
                RawDSL({"op": "must_not", "field": "acl_enabled", "conds": [True]}),
                In("acl_direct_grants", ["user:alice"]),
                In("acl_inherited_grants", ["team:docs"]),
            ]
        )
    )

    assert "not (acl_enabled == true)" in compiled
    assert "acl_enabled is null" in compiled
    assert 'ARRAY_CONTAINS(acl_direct_grants, "user:alice")' in compiled
    assert 'ARRAY_CONTAINS(acl_inherited_grants, "team:docs")' in compiled


def test_vector_literal_and_collection_name_safety():
    name = _safe_collection_name("Project/With Space", "Context.Table")

    assert name.startswith("ov_Project_With_Space_Context_Table_")
    assert len(name) <= 255
    assert _safe_collection_name("team-a", "context") != _safe_collection_name("team_a", "context")
    assert _legacy_collection_name("team-a", "context") == _legacy_collection_name(
        "team_a", "context"
    )
    assert (
        _safe_collection_name("internal", "metadata_v1", prefix="ov_data")
        != "ov_internal_metadata_v1"
    )
    assert _normalize_distance("ip") == "ip"

    with pytest.raises(ValueError, match="supports only cosine, l2, and ip"):
        _normalize_distance("dot")


def test_scope_roots_encoding_is_token_safe():
    encoded = _encode_scope_roots(["/a", "/a/b"])

    assert encoded == "\n/a\n/a/b\n"
    assert "\n/a\n" in encoded
    assert "\n/a/b\n" in encoded
    assert "\n/a/c\n" not in encoded


def test_score_from_cosine_similarity_is_higher_is_better():
    from openviking.storage.vectordb_adapters.milvus_adapter import _score_from_hit

    assert _score_from_hit({"distance": 1.0}, "cosine") == pytest.approx(1.0)
    assert _score_from_hit({"distance": 0.0}, "cosine") == pytest.approx(0.0)


class _QueryStub:
    def query(self, **kwargs):
        return [{"id": str(index), "level": index % 3} for index in range(10_001)]


def _unit_collection(client=None, meta: dict | None = None) -> MilvusCollection:
    return MilvusCollection(
        client=client or object(),
        logical_collection_name="context",
        physical_collection_name="ov_data_default_context_test",
        project_name="default",
        dense_vector_name="vector",
        sparse_vector_name="sparse_vector",
        distance_metric="cosine",
        timeout_seconds=7,
        meta=meta or _schema(),
    )


def test_materializes_defaults_and_normalizes_legacy_nulls():
    collection = _unit_collection()

    first = collection._prepare_record_for_write({"id": "one", "vector": [1.0, 0.0]})
    second = collection._prepare_record_for_write({"id": "two", "vector": [0.0, 1.0]})

    assert first["acl_enabled"] is False
    assert first["acl_direct_grants"] == []
    assert first["acl_inherited_grants"] == []
    assert first["acl_direct_grants"] is not second["acl_direct_grants"]
    decoded = collection._decode_record(
        {
            "acl_enabled": None,
            "acl_direct_grants": None,
            "acl_inherited_grants": None,
        }
    )
    assert decoded == {
        "acl_enabled": False,
        "acl_direct_grants": [],
        "acl_inherited_grants": [],
    }


def test_sparse_and_hybrid_queries_fail_fast():
    collection = _unit_collection()
    adapter = MilvusCollectionAdapter.from_config(_build_config())

    with pytest.raises(NotImplementedError, match="cannot be made complete"):
        collection.search_by_vector("default", dense_vector=[1.0, 0.0], sparse_vector={"term": 1.0})
    with pytest.raises(NotImplementedError, match="cannot be made complete"):
        collection.search_by_vector("default", sparse_vector={"term": 1.0})
    with pytest.raises(NotImplementedError, match="complete recall"):
        adapter._build_default_index_meta(
            index_name="default",
            distance="cosine",
            use_sparse=True,
            sparse_weight=0.5,
            scalar_index_fields=[],
        )


def test_scalar_order_and_group_fail_fast_at_10001_rows():
    collection = _unit_collection(_QueryStub())

    with pytest.raises(ValueError, match="more than 10000"):
        collection.search_by_scalar("default", "level")
    with pytest.raises(ValueError, match="more than 10000"):
        collection.aggregate_data("default", field="level")


def test_uri_precedence_does_not_connect():
    cases: list[tuple[dict[str, Any], str]] = [
        (
            {"milvus": {"uri": "http://explicit:19530"}, "url": "http://url:19530"},
            "http://explicit:19530",
        ),
        ({"milvus": {}, "url": "http://url:19530"}, "http://url:19530"),
        (
            {"milvus": {}, "custom_params": {"uri": "http://custom:19530"}},
            "http://custom:19530",
        ),
        ({"milvus": {}}, "./milvus.db"),
    ]
    for extra, expected in cases:
        config = VectorDBBackendConfig.model_validate(
            {"backend": "milvus", "project": "safe", "name": "context", **extra}
        )
        adapter = MilvusCollectionAdapter.from_config(config)
        assert adapter._uri == expected
        assert adapter._client is None


@pytest.mark.parametrize(
    ("project_name", "collection_name"),
    [
        ("default", "ov_internal_metadata_v1"),
        ("default", "ov_openviking_milvus_meta"),
        ("internal", "metadata-v1"),
    ],
)
def test_reserved_metadata_names_are_rejected(project_name, collection_name):
    with pytest.raises(ValueError, match="reserved OpenViking metadata namespace"):
        MilvusCollectionAdapter(
            uri="./unused.db",
            token=None,
            db_name=None,
            consistency_level="Strong",
            timeout_seconds=30,
            project_name=project_name,
            collection_name=collection_name,
            index_name="default",
            distance_metric="cosine",
            dense_vector_name="vector",
            sparse_vector_name="sparse_vector",
        )


def _lite_adapter(
    uri: str, project_name: str, collection_name: str = "context"
) -> MilvusCollectionAdapter:
    return MilvusCollectionAdapter(
        uri=uri,
        token=None,
        db_name=None,
        consistency_level="Strong",
        timeout_seconds=30,
        project_name=project_name,
        collection_name=collection_name,
        index_name="default",
        distance_metric="cosine",
        dense_vector_name="vector",
        sparse_vector_name="sparse_vector",
    )


class _OneShotFaultProxy:
    def __init__(self, client, method, predicate, *, after=False):
        self._client = client
        self._method = method
        self._predicate = predicate
        self._after = after
        self._armed = True
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name):
        target = getattr(self._client, name)
        if not callable(target):
            return target

        def delegated(*args, **kwargs):
            self.calls.append((name, dict(kwargs)))
            should_fail = self._armed and name == self._method and self._predicate(kwargs)
            if should_fail and not self._after:
                self._armed = False
                raise RuntimeError(f"injected {name} failure")
            result = target(*args, **kwargs)
            if should_fail:
                self._armed = False
                raise RuntimeError(f"injected post-{name} failure")
            return result

        return delegated


class _CollectionAuditProxy:
    def __init__(self, client):
        self._client = client
        self.calls: list[tuple[str, str | None]] = []

    def __getattr__(self, name):
        target = getattr(self._client, name)
        if not callable(target):
            return target

        def delegated(*args, **kwargs):
            self.calls.append((name, kwargs.get("collection_name")))
            return target(*args, **kwargs)

        return delegated


def _create_legacy_metadata_collection(client, rows):
    pymilvus = pytest.importorskip("pymilvus")
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(
        field_name="id",
        datatype=pymilvus.DataType.VARCHAR,
        is_primary=True,
        max_length=512,
    )
    schema.add_field(
        field_name="meta_json",
        datatype=pymilvus.DataType.VARCHAR,
        max_length=65_535,
    )
    schema.add_field(
        field_name="indexes_json",
        datatype=pymilvus.DataType.JSON,
        nullable=True,
    )
    schema.add_field(field_name="meta_vector", datatype=pymilvus.DataType.FLOAT_VECTOR, dim=2)
    client.create_collection(collection_name="ov_openviking_milvus_meta", schema=schema)
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="meta_vector",
        index_name="legacy_meta_vector_index",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )
    client.create_index(
        collection_name="ov_openviking_milvus_meta",
        index_params=index_params,
    )
    client.alter_collection_properties(
        collection_name="ov_openviking_milvus_meta",
        properties={"legacy_sentinel": "unchanged"},
    )
    client.upsert(collection_name="ov_openviking_milvus_meta", data=rows)


def _index_fields(client, collection_name):
    return {
        client.describe_index(collection_name=collection_name, index_name=index_name)[
            "field_name"
        ]: index_name
        for index_name in client.list_indexes(collection_name=collection_name)
    }


def _collection_is_loaded(client, collection_name):
    state = client.get_load_state(collection_name=collection_name)["state"]
    return str(getattr(state, "name", state)).lower() == "loaded"


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
def test_milvus_lite_v2_never_accesses_preexisting_legacy_metadata(tmp_path):
    pymilvus = pytest.importorskip("pymilvus")
    uri = str(tmp_path / "v2-legacy-isolation.db")
    client = pymilvus.MilvusClient(uri)
    adapter = _lite_adapter(uri, "v2-legacy-isolation")
    physical_name = adapter.physical_collection_name
    exact_meta = _schema()
    exact_meta["_openviking_identity"] = {
        "logical_project": "v2-legacy-isolation",
        "logical_collection": "context",
        "naming_version": 2,
        "physical_collection": physical_name,
    }
    rows = [
        {
            "id": "unrelated-legacy-row",
            "meta_json": json.dumps({"sentinel": "unrelated"}),
            "indexes_json": {"sentinel": "unrelated"},
            "meta_vector": [0.0, 0.0],
        },
        {
            "id": physical_name,
            "meta_json": json.dumps(exact_meta),
            "indexes_json": {"sentinel": "exact-v2-id"},
            "meta_vector": [0.0, 0.0],
        },
    ]
    _create_legacy_metadata_collection(client, rows)
    row_ids = [row["id"] for row in rows]
    rows_before = client.get(
        collection_name="ov_openviking_milvus_meta",
        ids=row_ids,
        output_fields=["meta_json", "indexes_json", "meta_vector"],
    )
    indexes_before = _index_fields(client, "ov_openviking_milvus_meta")
    properties_before = client.describe_collection("ov_openviking_milvus_meta")["properties"]
    client.release_collection(collection_name="ov_openviking_milvus_meta")
    assert not _collection_is_loaded(client, "ov_openviking_milvus_meta")

    proxy = _CollectionAuditProxy(client)
    cast(Any, adapter)._client = proxy
    assert adapter.create_collection(
        "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
    )
    adapter.upsert(
        {
            "id": "isolated",
            "uri": "viking://resources/isolated.md",
            "vector": [1.0, 0.0],
            "level": 1,
        }
    )
    assert adapter.get(["isolated"])[0]["id"] == "isolated"
    assert [
        item["id"] for item in adapter.query(query_vector=[1.0, 0.0], limit=1, output_fields=["id"])
    ] == ["isolated"]
    assert adapter.delete(ids=["isolated"]) == 1
    adapter.upsert({"id": "drop-me", "vector": [1.0, 0.0]})
    assert adapter.drop_collection() is True

    assert [
        (method, collection_name)
        for method, collection_name in proxy.calls
        if collection_name == "ov_openviking_milvus_meta"
    ] == []
    assert not _collection_is_loaded(client, "ov_openviking_milvus_meta")
    assert _index_fields(client, "ov_openviking_milvus_meta") == indexes_before
    assert (
        client.describe_collection("ov_openviking_milvus_meta")["properties"] == properties_before
    )
    assert client.get(collection_name="ov_internal_metadata_v1", ids=[physical_name]) == []

    client.load_collection(collection_name="ov_openviking_milvus_meta")
    rows_after = client.get(
        collection_name="ov_openviking_milvus_meta",
        ids=row_ids,
        output_fields=["meta_json", "indexes_json", "meta_vector"],
    )
    assert rows_after == rows_before
    client.release_collection(collection_name="ov_openviking_milvus_meta")
    assert not _collection_is_loaded(client, "ov_openviking_milvus_meta")
    adapter.close()


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
def test_milvus_lite_verified_legacy_binding_uses_legacy_sidecar(tmp_path):
    pytest.importorskip("pymilvus")
    uri = str(tmp_path / "verified-legacy-binding.db")
    owner = _lite_adapter(uri, "legacy-owner")
    owner._resolved_physical_collection_name = owner.legacy_physical_collection_name
    physical_name = owner.physical_collection_name
    assert owner.create_collection(
        "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
    )
    owner.upsert({"id": "owned", "vector": [1.0, 0.0]})
    client = owner._connect()
    current_row = client.get(
        collection_name="ov_internal_metadata_v1",
        ids=[physical_name],
        output_fields=["meta_json", "indexes_json"],
    )[0]
    identity = json.loads(current_row["meta_json"])["_openviking_identity"]
    assert identity["naming_version"] == 1
    _create_legacy_metadata_collection(
        client,
        [
            {
                "id": physical_name,
                "meta_json": current_row["meta_json"],
                "indexes_json": current_row["indexes_json"],
                "meta_vector": [0.0, 0.0],
            }
        ],
    )
    client.delete(collection_name="ov_internal_metadata_v1", ids=[physical_name])
    client.release_collection(collection_name="ov_openviking_milvus_meta")
    owner.close()

    recovered = _lite_adapter(uri, "legacy-owner")
    assert recovered.collection_exists() is True
    assert recovered.physical_collection_name == physical_name
    assert recovered.get(["owned"])[0]["id"] == "owned"
    assert recovered.drop_collection() is True
    client = recovered._connect()
    assert not client.has_collection(physical_name)
    assert client.get(collection_name="ov_internal_metadata_v1", ids=[physical_name]) == []
    assert client.get(collection_name="ov_openviking_milvus_meta", ids=[physical_name]) == []
    recovered.close()


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
def test_milvus_lite_ownership_collision_matrix_and_wrong_project_drop(tmp_path):
    pytest.importorskip("pymilvus")
    matrix_uri = str(tmp_path / "ownership-matrix.db")
    projects = ["team-a", "team_a", "team.a", "team/a", "team a"]
    physical_names = []
    for position, project in enumerate(projects):
        adapter = _lite_adapter(matrix_uri, project)
        assert adapter.create_collection(
            "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
        )
        adapter.upsert([{"id": f"doc-{position}", "vector": [1.0, 0.0]}])
        physical_names.append(adapter.physical_collection_name)
        adapter.close()
    assert len(set(physical_names)) == len(projects)

    from pymilvus import MilvusClient

    client = MilvusClient(matrix_uri)
    for position, physical_name in enumerate(physical_names):
        rows = client.get(collection_name=physical_name, ids=[f"doc-{position}"])
        assert [row["id"] for row in rows] == [f"doc-{position}"]
    client.close()

    legacy_uri = str(tmp_path / "legacy-owner.db")
    owner = _lite_adapter(legacy_uri, "team-a")
    owner._resolved_physical_collection_name = owner.legacy_physical_collection_name
    legacy_name = owner.physical_collection_name
    assert owner.create_collection(
        "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
    )
    owner.upsert([{"id": "owned", "vector": [1.0, 0.0]}])
    owner_client = owner._connect()
    identity = json.loads(
        owner_client.get(
            collection_name="ov_internal_metadata_v1",
            ids=[legacy_name],
            output_fields=["meta_json"],
        )[0]["meta_json"]
    )["_openviking_identity"]
    assert identity == {
        "logical_project": "team-a",
        "logical_collection": "context",
        "naming_version": 1,
        "physical_collection": legacy_name,
    }
    owner.close()

    wrong_project = _lite_adapter(legacy_uri, "team_a")
    assert wrong_project.legacy_physical_collection_name == legacy_name
    with pytest.raises(RuntimeError, match="ownership identity does not match"):
        wrong_project.drop_collection()
    wrong_project.close()

    client = MilvusClient(legacy_uri)
    assert client.has_collection(legacy_name)
    assert [row["id"] for row in client.get(collection_name=legacy_name, ids=["owned"])] == [
        "owned"
    ]
    client.close()

    owner = _lite_adapter(legacy_uri, "team-a")
    assert owner.drop_collection() is True
    owner.close()
    for project in projects:
        adapter = _lite_adapter(matrix_uri, project)
        assert adapter.drop_collection() is True
        adapter.close()


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
def test_milvus_lite_rejects_legacy_sidecar_without_identity(tmp_path):
    pytest.importorskip("pymilvus")
    uri = str(tmp_path / "unowned-legacy.db")
    adapter = _lite_adapter(uri, "team-a")
    adapter._resolved_physical_collection_name = adapter.legacy_physical_collection_name
    physical_name = adapter.physical_collection_name
    assert adapter.create_collection(
        "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
    )
    adapter.upsert([{"id": "owned", "vector": [1.0, 0.0]}])
    client = adapter._connect()
    record = client.get(
        collection_name="ov_internal_metadata_v1",
        ids=[physical_name],
        output_fields=["indexes_json"],
    )[0]
    client.upsert(
        collection_name="ov_internal_metadata_v1",
        data=[
            {
                "id": physical_name,
                "meta_json": json.dumps(_schema()),
                "indexes_json": record["indexes_json"],
                "meta_vector": [0.0, 0.0],
            }
        ],
    )
    client.alter_collection_properties(
        collection_name=physical_name,
        properties={"openviking_meta": json.dumps(_schema())},
    )
    adapter.close()

    unowned = _lite_adapter(uri, "team-a")
    with pytest.raises(RuntimeError, match="no verifiable ownership identity"):
        unowned.collection_exists()
    with pytest.raises(RuntimeError, match="no verifiable ownership identity"):
        unowned.drop_collection()
    client = unowned._connect()
    assert client.has_collection(physical_name)
    assert [row["id"] for row in client.get(collection_name=physical_name, ids=["owned"])] == [
        "owned"
    ]
    unowned.close()


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
@pytest.mark.parametrize(
    ("fault_method", "after"), [("alter_collection_properties", False), ("upsert", True)]
)
def test_milvus_lite_collection_metadata_failure_rolls_back_and_retries(
    tmp_path, fault_method, after
):
    pymilvus = pytest.importorskip("pymilvus")
    uri = str(tmp_path / f"collection-rollback-{fault_method}.db")
    client = pymilvus.MilvusClient(uri)
    adapter = _lite_adapter(uri, "collection-rollback")
    physical_name = adapter.physical_collection_name

    def predicate(kwargs):
        if fault_method == "upsert":
            return kwargs.get("collection_name") == "ov_internal_metadata_v1"
        return kwargs.get("collection_name") == physical_name and "openviking_meta" in kwargs.get(
            "properties", {}
        )

    proxy = _OneShotFaultProxy(client, fault_method, predicate, after=after)
    cast(Any, adapter)._client = proxy
    with pytest.raises(RuntimeError, match="rolled back"):
        adapter.create_collection(
            "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
        )
    assert not client.has_collection(physical_name)
    assert client.get(collection_name="ov_internal_metadata_v1", ids=[physical_name]) == []

    assert adapter.create_collection(
        "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
    )
    assert client.has_collection(physical_name)
    assert adapter.drop_collection() is True
    adapter.close()


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
@pytest.mark.parametrize(
    ("fault_method", "after"), [("alter_collection_properties", False), ("upsert", True)]
)
def test_milvus_lite_index_failure_rolls_back_only_new_indexes(tmp_path, fault_method, after):
    pytest.importorskip("pymilvus")
    uri = str(tmp_path / f"index-rollback-{fault_method}.db")
    initial_schema = _schema()
    initial_schema["ScalarIndex"] = ["uri"]
    adapter = _lite_adapter(uri, "rollback")
    assert adapter.create_collection(
        "context", initial_schema, distance="cosine", sparse_weight=0.0, index_name="default"
    )
    client = adapter._connect()
    physical_name = adapter.physical_collection_name
    indexes_before = _index_fields(client, physical_name)
    sidecar_before = client.get(
        collection_name="ov_internal_metadata_v1",
        ids=[physical_name],
        output_fields=["meta_json", "indexes_json"],
    )[0]
    properties_before = client.describe_collection(physical_name)["properties"]

    raw = adapter._new_collection()
    assert raw.load_remote_meta() == initial_schema

    def predicate(kwargs):
        if fault_method == "upsert":
            return kwargs.get("collection_name") == "ov_internal_metadata_v1"
        return "openviking_index_default" in kwargs.get("properties", {})

    proxy = _OneShotFaultProxy(client, fault_method, predicate, after=after)
    raw._client = proxy
    with pytest.raises(RuntimeError, match="rolled back"):
        raw.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"Distance": "cosine"},
                "ScalarIndex": ["uri", "level"],
            },
        )

    assert _index_fields(client, physical_name) == indexes_before
    assert client.has_collection(physical_name)
    assert (
        client.get(
            collection_name="ov_internal_metadata_v1",
            ids=[physical_name],
            output_fields=["meta_json", "indexes_json"],
        )[0]
        == sidecar_before
    )
    assert client.describe_collection(physical_name)["properties"] == properties_before
    called = [name for name, _ in proxy.calls]
    assert "release_collection" in called
    assert "drop_index" in called
    assert "load_collection" in called
    adapter.close()

    restarted = _lite_adapter(uri, "rollback")
    assert restarted.collection_exists()
    raw = restarted._new_collection()
    raw.load_remote_meta()
    raw.create_index(
        "default",
        {
            "IndexName": "default",
            "VectorIndex": {"Distance": "cosine"},
            "ScalarIndex": ["uri", "level"],
        },
    )
    client = restarted._connect()
    assert set(_index_fields(client, physical_name)) == set(indexes_before) | {"level"}
    sidecar_meta = client.get(
        collection_name="ov_internal_metadata_v1",
        ids=[physical_name],
        output_fields=["indexes_json"],
    )[0]["indexes_json"]["default"]
    property_meta = json.loads(
        client.describe_collection(physical_name)["properties"]["openviking_index_default"]
    )
    assert sidecar_meta == property_meta
    assert restarted.drop_collection() is True
    restarted.close()


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
def test_milvus_lite_successful_index_update_loads_collection(tmp_path):
    pytest.importorskip("pymilvus")
    uri = str(tmp_path / "index-load.db")
    adapter = _lite_adapter(uri, "index-load")
    assert adapter.create_collection(
        "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
    )
    client = adapter._connect()
    physical_name = adapter.physical_collection_name
    assert _collection_is_loaded(client, physical_name)

    client.release_collection(collection_name=physical_name)
    assert not _collection_is_loaded(client, physical_name)
    assert (
        adapter.create_collection(
            "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
        )
        is False
    )
    assert _collection_is_loaded(client, physical_name)
    assert adapter.drop_collection() is True
    adapter.close()


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
@pytest.mark.parametrize("after", [False, True], ids=["before-effect", "after-effect"])
def test_milvus_lite_load_failure_restores_unloaded_collection(tmp_path, after):
    pytest.importorskip("pymilvus")
    uri = str(tmp_path / f"index-load-rollback-{after}.db")
    adapter = _lite_adapter(uri, f"index-load-rollback-{after}")
    assert adapter.create_collection(
        "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
    )
    adapter.upsert(
        {
            "id": "preserved",
            "uri": "viking://resources/preserved.md",
            "vector": [1.0, 0.0],
            "level": 1,
        }
    )
    assert (
        adapter.create_collection(
            "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
        )
        is False
    )
    client = adapter._connect()
    physical_name = adapter.physical_collection_name
    data_before = client.get(collection_name=physical_name, ids=["preserved"])
    indexes_before = _index_fields(client, physical_name)
    properties_before = client.describe_collection(physical_name)["properties"]
    sidecar_before = client.get(
        collection_name="ov_internal_metadata_v1",
        ids=[physical_name],
        output_fields=["meta_json", "indexes_json"],
    )[0]

    client.release_collection(collection_name=physical_name)
    assert not _collection_is_loaded(client, physical_name)
    proxy = _OneShotFaultProxy(
        client,
        "load_collection",
        lambda kwargs: kwargs.get("collection_name") == physical_name,
        after=after,
    )
    cast(Any, adapter)._client = proxy
    with pytest.raises(RuntimeError, match="rolled back"):
        adapter.create_collection(
            "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
        )

    assert not _collection_is_loaded(client, physical_name)
    assert _index_fields(client, physical_name) == indexes_before
    assert client.describe_collection(physical_name)["properties"] == properties_before
    assert (
        client.get(
            collection_name="ov_internal_metadata_v1",
            ids=[physical_name],
            output_fields=["meta_json", "indexes_json"],
        )[0]
        == sidecar_before
    )
    called = [name for name, _ in proxy.calls]
    assert "create_index" not in called
    assert "load_collection" in called
    assert "release_collection" in called

    assert (
        adapter.create_collection(
            "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
        )
        is False
    )
    assert _collection_is_loaded(client, physical_name)
    assert client.get(collection_name=physical_name, ids=["preserved"]) == data_before
    result = adapter.query(
        query_vector=[1.0, 0.0],
        limit=1,
        output_fields=["id"],
    )
    assert [item["id"] for item in result] == ["preserved"]
    assert adapter.drop_collection() is True
    adapter.close()


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
def test_milvus_lite_drop_failure_preserves_collection_state(tmp_path):
    pytest.importorskip("pymilvus")
    uri = str(tmp_path / "drop-rollback.db")
    adapter = _lite_adapter(uri, "drop-rollback")
    assert adapter.create_collection(
        "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
    )
    adapter.upsert(
        {
            "id": "preserved",
            "uri": "viking://resources/preserved.md",
            "vector": [1.0, 0.0],
            "level": 1,
        }
    )
    client = adapter._connect()
    physical_name = adapter.physical_collection_name
    indexes_before = _index_fields(client, physical_name)
    properties_before = client.describe_collection(physical_name)["properties"]
    sidecar_before = client.get(
        collection_name="ov_internal_metadata_v1",
        ids=[physical_name],
        output_fields=["meta_json", "indexes_json"],
    )[0]
    assert _collection_is_loaded(client, physical_name)

    proxy = _OneShotFaultProxy(
        client,
        "drop_collection",
        lambda kwargs: kwargs.get("collection_name") == physical_name,
    )
    cast(Any, adapter)._client = proxy
    with pytest.raises(RuntimeError, match="injected drop_collection failure"):
        adapter.drop_collection()

    assert client.has_collection(physical_name)
    assert [row["id"] for row in client.get(collection_name=physical_name, ids=["preserved"])] == [
        "preserved"
    ]
    assert _index_fields(client, physical_name) == indexes_before
    assert _collection_is_loaded(client, physical_name)
    assert client.describe_collection(physical_name)["properties"] == properties_before
    assert (
        client.get(
            collection_name="ov_internal_metadata_v1",
            ids=[physical_name],
            output_fields=["meta_json", "indexes_json"],
        )[0]
        == sidecar_before
    )
    called = [name for name, _ in proxy.calls]
    assert "drop_index" not in called
    assert "drop_collection_properties" not in called
    assert "release_collection" not in called

    assert adapter.drop_collection() is True
    assert not client.has_collection(physical_name)
    assert client.get(collection_name="ov_internal_metadata_v1", ids=[physical_name]) == []
    adapter.close()


def _create_mismatched_lite_collection(uri, mismatch):
    pymilvus = pytest.importorskip("pymilvus")
    client = pymilvus.MilvusClient(uri)
    project_name = f"schema-{mismatch}"
    physical_name = _safe_collection_name(project_name, "context", prefix="ov_data")
    auto_id = mismatch == "auto_id"
    dynamic = mismatch != "dynamic"
    schema = client.create_schema(auto_id=auto_id, enable_dynamic_field=dynamic)
    fields = list(_schema()["Fields"]) + [
        {"FieldName": "parent_uri", "FieldType": "path"},
        {"FieldName": "scope_roots", "FieldType": "string"},
        {"FieldName": "uri_depth", "FieldType": "int64"},
    ]
    for field in fields:
        name = field["FieldName"]
        field_type = field["FieldType"]
        kwargs: dict[str, Any] = {}
        if name == "id":
            if auto_id:
                data_type = pymilvus.DataType.INT64
                kwargs["is_primary"] = True
            else:
                data_type = pymilvus.DataType.VARCHAR
                kwargs["max_length"] = 512
                kwargs["is_primary"] = mismatch != "primary_key"
                if mismatch == "primary_key":
                    kwargs["nullable"] = True
        elif field_type == "vector":
            data_type = pymilvus.DataType.FLOAT_VECTOR
            kwargs["dim"] = 3 if mismatch == "dimension" else 2
        elif field_type == "list<string>":
            data_type = pymilvus.DataType.ARRAY
            kwargs.update(
                element_type=pymilvus.DataType.VARCHAR,
                max_capacity=8 if mismatch == "array" and name == "search_tags" else 1024,
                max_length=1024,
                nullable=True,
            )
        elif field_type == "int64":
            data_type = pymilvus.DataType.INT64
            kwargs["nullable"] = True
        elif field_type == "bool":
            data_type = pymilvus.DataType.BOOL
            kwargs["nullable"] = True
        elif field_type == "sparse_vector":
            data_type = pymilvus.DataType.JSON
            kwargs["nullable"] = True
        else:
            data_type = pymilvus.DataType.VARCHAR
            kwargs.update(
                max_length=4096 if name in {"uri", "parent_uri"} else 65_535,
                nullable=True,
            )
            if mismatch == "primary_key" and name == "uri":
                kwargs.pop("nullable", None)
                kwargs["is_primary"] = True
        schema.add_field(field_name=name, datatype=data_type, **kwargs)
    client.create_collection(collection_name=physical_name, schema=schema)
    raw = MilvusCollection(
        client=client,
        logical_collection_name="context",
        physical_collection_name=physical_name,
        project_name=project_name,
        dense_vector_name="vector",
        sparse_vector_name="sparse_vector",
        distance_metric="cosine",
        timeout_seconds=30,
        meta=_schema(),
    )
    raw._save_collection_meta()
    sidecar = client.get(
        collection_name="ov_internal_metadata_v1",
        ids=[physical_name],
        output_fields=["meta_json", "indexes_json"],
    )[0]
    properties = client.describe_collection(physical_name)["properties"]
    client.close()
    return project_name, physical_name, sidecar, properties


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
@pytest.mark.parametrize("mismatch", ["dimension", "primary_key", "auto_id", "array", "dynamic"])
def test_milvus_lite_existing_schema_mismatch_is_read_only(tmp_path, mismatch):
    uri = str(tmp_path / f"schema-{mismatch}.db")
    project_name, physical_name, sidecar_before, properties_before = (
        _create_mismatched_lite_collection(uri, mismatch)
    )
    adapter = _lite_adapter(uri, project_name)
    with pytest.raises(RuntimeError, match="incompatible.*migration/rebuild"):
        adapter.create_collection(
            "context", _schema(), distance="cosine", sparse_weight=0.0, index_name="default"
        )

    client = adapter._connect()
    assert client.has_collection(physical_name)
    assert (
        client.get(
            collection_name="ov_internal_metadata_v1",
            ids=[physical_name],
            output_fields=["meta_json", "indexes_json"],
        )[0]
        == sidecar_before
    )
    assert client.describe_collection(physical_name)["properties"] == properties_before
    adapter.close()


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
def test_milvus_lite_adapter_integration_smoke(tmp_path):
    pytest.importorskip("pymilvus")

    suffix = uuid.uuid4().hex[:8]
    uri = str(tmp_path / "milvus.db")
    project_name = f"pytest_{suffix}"

    adapter = _lite_adapter(uri, project_name)

    try:
        assert adapter.create_collection(
            "context",
            _schema(),
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )
        adapter.upsert(
            [
                {
                    "id": "doc-1",
                    "uri": "viking://resources/acme/docs/a.md",
                    "vector": [1.0, 0.0],
                    "sparse_vector": {"quarter": 1.0},
                    "abstract": "quarterly report",
                    "level": 1,
                    "updated_at": "2026-05-15T00:00:00+00:00",
                    "search_tags": ["finance"],
                    "account_id": "acme",
                },
                {
                    "id": "doc-2",
                    "uri": "viking://resources/acme/notes/b.md",
                    "vector": [0.0, 1.0],
                    "sparse_vector": {"notes": 1.0},
                    "abstract": "meeting notes",
                    "level": 2,
                    "updated_at": "2026-05-16T00:00:00+00:00",
                    "search_tags": ["notes"],
                    "account_id": "acme",
                    "acl_enabled": True,
                    "acl_direct_grants": ["user:alice"],
                },
                {
                    "id": "doc-3",
                    "uri": "viking://resources/acme/shared/c.md",
                    "vector": [0.8, 0.2],
                    "abstract": "shared report",
                    "level": 3,
                    "updated_at": "2026-05-17T00:00:00+00:00",
                    "search_tags": ["shared"],
                    "account_id": "acme",
                    "acl_enabled": True,
                    "acl_inherited_grants": ["team:docs"],
                },
            ]
        )

        saved = adapter.get(["doc-1"])[0]
        assert saved["acl_enabled"] is False
        assert saved["acl_direct_grants"] == []
        assert saved["acl_inherited_grants"] == []
        adapter.update_data([{"id": "doc-1", "abstract": "updated report"}])
        assert adapter.get(["doc-1"])[0]["abstract"] == "updated report"

        client = adapter._connect()
        physical_indexes = {
            client.describe_index(
                collection_name=adapter.physical_collection_name,
                index_name=name,
            )["field_name"]
            for name in client.list_indexes(collection_name=adapter.physical_collection_name)
        }
        assert {"vector", "uri", "level", "account_id", "acl_enabled"} <= physical_indexes
        assert "search_tags" not in physical_indexes
        assert "acl_direct_grants" not in physical_indexes
        assert "acl_inherited_grants" not in physical_indexes
        index_meta = adapter.get_collection().get_index_meta_data("default")
        assert set(index_meta["ScalarIndex"]) <= physical_indexes
        assert {"search_tags", "acl_direct_grants", "acl_inherited_grants"} <= set(
            index_meta["ScalarIndexUnavailable"]
        )
        sidecar = client.get(
            collection_name="ov_internal_metadata_v1",
            ids=[adapter.physical_collection_name],
            output_fields=["meta_json"],
        )[0]
        identity = json.loads(sidecar["meta_json"])["_openviking_identity"]
        assert identity == {
            "logical_project": project_name,
            "logical_collection": "context",
            "naming_version": 2,
            "physical_collection": adapter.physical_collection_name,
        }

        adapter.close()
        adapter = _lite_adapter(uri, project_name)
        assert adapter.collection_exists() is True

        result = adapter.query(
            query_vector=[1.0, 0.0],
            limit=1,
            filter=PathScope("uri", "viking://resources/acme/docs", depth=-1),
            output_fields=["id", "uri", "abstract", "level"],
        )
        assert [item["id"] for item in result] == ["doc-1"]
        assert result[0]["_score"] == pytest.approx(1.0)
        direct = adapter.query(
            query_vector=[0.0, 1.0],
            limit=5,
            filter=_acl_filter("user:alice"),
            output_fields=["id"],
        )
        assert {item["id"] for item in direct} == {"doc-1", "doc-2"}
        inherited = adapter.query(
            query_vector=[1.0, 0.0],
            limit=5,
            filter=_acl_filter("team:docs"),
            output_fields=["id"],
        )
        assert {item["id"] for item in inherited} == {"doc-1", "doc-3"}
        assert adapter.count(Eq("account_id", "missing")) == 0
        assert adapter.count() == 3
        assert adapter.delete(ids=["doc-2"]) == 1
        assert adapter.count() == 2
    finally:
        adapter.drop_collection()
        adapter.close()


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
def test_milvus_lite_dynamic_pre_acl_restart_recovery(tmp_path):
    pytest.importorskip("pymilvus")
    old_schema = _schema()
    old_schema["Fields"] = [
        field for field in old_schema["Fields"] if not field["FieldName"].startswith("acl_")
    ]
    old_schema["ScalarIndex"] = [
        field for field in old_schema["ScalarIndex"] if not field.startswith("acl_")
    ]
    uri = str(tmp_path / "legacy.db")
    project_name = f"legacy_{uuid.uuid4().hex[:8]}"

    adapter = _lite_adapter(uri, project_name)
    try:
        hashed_name = adapter.physical_collection_name
        legacy_name = adapter.legacy_physical_collection_name
        assert hashed_name != legacy_name
        adapter._resolved_physical_collection_name = legacy_name
        assert adapter.create_collection(
            "context",
            old_schema,
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )
        adapter.upsert(
            [
                {
                    "id": "legacy-public",
                    "uri": "viking://resources/legacy.md",
                    "vector": [1.0, 0.0],
                    "level": 1,
                }
            ]
        )
        adapter.close()

        adapter = _lite_adapter(uri, project_name)
        assert adapter.physical_collection_name == hashed_name
        assert (
            adapter.create_collection(
                "context",
                _schema(),
                distance="cosine",
                sparse_weight=0.0,
                index_name="default",
            )
            is False
        )
        assert adapter.physical_collection_name == legacy_name
        adapter.upsert(
            [
                {
                    "id": "direct",
                    "uri": "viking://resources/direct.md",
                    "vector": [0.9, 0.1],
                    "acl_enabled": True,
                    "acl_direct_grants": ["user:alice"],
                },
                {
                    "id": "inherited",
                    "uri": "viking://resources/inherited.md",
                    "vector": [0.8, 0.2],
                    "acl_enabled": True,
                    "acl_inherited_grants": ["team:docs"],
                },
            ]
        )
        adapter.close()

        adapter = _lite_adapter(uri, project_name)
        legacy = adapter.get(["legacy-public"])[0]
        assert legacy["acl_enabled"] is False
        assert legacy["acl_direct_grants"] == []
        assert legacy["acl_inherited_grants"] == []
        visible = adapter.query(
            query_vector=[1.0, 0.0],
            limit=10,
            filter=_acl_filter("user:alice", "team:docs"),
            output_fields=["id"],
        )
        assert {item["id"] for item in visible} == {
            "legacy-public",
            "direct",
            "inherited",
        }
    finally:
        adapter.drop_collection()
        adapter.close()
