# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone

import pytest

from openviking.storage.expr import And, Contains, Eq, PathScope, TimeRange
from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking.storage.vectordb_adapters.milvus_adapter import (
    MilvusCollection,
    MilvusCollectionAdapter,
    MilvusFilterCompiler,
    _encode_scope_roots,
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
        ],
        "ScalarIndex": ["uri", "level", "updated_at", "search_tags", "account_id"],
    }


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
    assert adapter.physical_collection_name == "ov_default_context"


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

    public_record = adapter.normalize_record_for_read(normalized)
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


def test_vector_literal_and_collection_name_safety():
    name = _safe_collection_name("Project/With Space", "Context.Table")

    assert name.startswith("ov_Project_With_Space_Context_Table")
    assert len(name) <= 255
    assert _normalize_distance("ip") == "ip"

    with pytest.raises(ValueError, match="supports only cosine, l2, and ip"):
        _normalize_distance("dot")


def test_scope_roots_encoding_is_token_safe():
    encoded = _encode_scope_roots(["/a", "/a/b"])

    assert encoded == "\n/a\n/a/b\n"
    assert "\n/a\n" in encoded
    assert "\n/a/b\n" in encoded
    assert "\n/a/c\n" not in encoded


def test_score_from_cosine_distance_is_higher_is_better():
    from openviking.storage.vectordb_adapters.milvus_adapter import _score_from_hit

    assert _score_from_hit({"distance": 0.0}, "cosine") == pytest.approx(1.0)
    assert _score_from_hit({"distance": 1.0}, "cosine") == pytest.approx(0.0)


class _FakeSchema:
    def __init__(self) -> None:
        self.fields = []

    def add_field(self, **kwargs):
        self.fields.append(kwargs)


class _FakeIndexParams:
    def __init__(self) -> None:
        self.indexes = []

    def add_index(self, **kwargs):
        self.indexes.append(kwargs)


class _FakeMilvusClient:
    def __init__(self) -> None:
        self.schema = _FakeSchema()
        self.index_params = _FakeIndexParams()
        self.created_collection = None
        self.created_index = None
        self.properties = {}
        self.loaded = False

    def create_schema(self, **kwargs):
        self.schema_kwargs = kwargs
        return self.schema

    def create_collection(self, **kwargs):
        self.created_collection = kwargs

    def alter_collection_properties(self, collection_name, properties, timeout=None):
        self.properties.update(properties)

    def prepare_index_params(self):
        return self.index_params

    def create_index(self, **kwargs):
        self.created_index = kwargs

    def load_collection(self, collection_name, timeout=None):
        self.loaded = True


def test_collection_creation_uses_explicit_schema_and_autoindex():
    client = _FakeMilvusClient()
    collection = MilvusCollection(
        client=client,
        logical_collection_name="context",
        physical_collection_name="ov_default_context",
        project_name="default",
        dense_vector_name="vector",
        sparse_vector_name="sparse_vector",
        distance_metric="cosine",
        timeout_seconds=7,
        meta=_schema(),
    )

    collection.create_remote_collection(_schema(), consistency_level="Session")
    collection.create_index(
        "default",
        {
            "IndexName": "default",
            "VectorIndex": {"IndexType": "AUTOINDEX", "Distance": "cosine"},
            "ScalarIndex": ["uri", "level", "parent_uri", "scope_roots"],
        },
    )

    assert client.schema_kwargs["auto_id"] is False
    assert client.schema_kwargs["enable_dynamic_field"] is True
    field_names = {field["field_name"] for field in client.schema.fields}
    assert {"id", "uri", "vector", "sparse_vector", "parent_uri", "scope_roots"} <= field_names
    vector_field = next(field for field in client.schema.fields if field["field_name"] == "vector")
    assert vector_field["dim"] == 2
    assert client.created_collection["collection_name"] == "ov_default_context"
    assert client.created_collection["consistency_level"] == "Session"
    assert client.index_params.indexes[0] == {
        "field_name": "vector",
        "index_name": "default",
        "index_type": "AUTOINDEX",
        "metric_type": "COSINE",
    }
    assert client.loaded is True


@pytest.mark.skipif(
    importlib.util.find_spec("milvus_lite") is None,
    reason="milvus_lite is not installed",
)
def test_milvus_lite_adapter_integration_smoke(tmp_path):
    pytest.importorskip("pymilvus")

    suffix = uuid.uuid4().hex[:8]
    uri = str(tmp_path / "milvus.db")
    project_name = f"pytest_{suffix}"

    def _new_adapter() -> MilvusCollectionAdapter:
        return MilvusCollectionAdapter(
            uri=uri,
            token=None,
            db_name=None,
            consistency_level="Strong",
            timeout_seconds=30,
            project_name=project_name,
            collection_name="context",
            index_name="default",
            distance_metric="cosine",
            dense_vector_name="vector",
            sparse_vector_name="sparse_vector",
        )

    adapter = _new_adapter()

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
                },
            ]
        )

        adapter.close()
        adapter = _new_adapter()
        assert adapter.collection_exists() is True

        result = adapter.query(
            query_vector=[1.0, 0.0],
            limit=1,
            filter=PathScope("uri", "viking://resources/acme/docs", depth=-1),
            output_fields=["id", "uri", "abstract", "level"],
        )
        assert [item["id"] for item in result] == ["doc-1"]
        assert result[0]["_score"] == pytest.approx(1.0)
        assert adapter.count(Eq("account_id", "missing")) == 0
        assert adapter.count() == 2
        assert adapter.delete(ids=["doc-2"]) == 1
        assert adapter.count() == 1
    finally:
        adapter.drop_collection()
        adapter.close()
