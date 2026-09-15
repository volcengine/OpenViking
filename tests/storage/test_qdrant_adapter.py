# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import io
import json
import math
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from openviking.storage.expr import And, Contains, Eq, In, Or, PathScope, RawDSL
from openviking.storage.vectordb.collection.qdrant_collection import QdrantCollection
from openviking.storage.vectordb.collection.qdrant_rest import QdrantError, QdrantRestClient
from openviking.storage.vectordb.qdrant_sparse import (
    SparseTermDictionary,
    sparse_owner_point_id,
    stable_sparse_index,
)
from openviking.storage.vectordb.qdrant_utils import (
    build_qdrant_payload,
    compile_qdrant_filter,
    to_qdrant_point_id,
)
from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking.storage.vectordb_adapters.qdrant_adapter import QdrantCollectionAdapter
from openviking.storage.viking_vector_index_backend import _AsyncVectorAdapter
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


class _Response:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _ScriptedTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append(
            {
                "method": request.method,
                "url": request.full_url,
                "body": body,
                "params": dict(parse_qs(urlsplit(request.full_url).query)),
                "timeout": timeout,
                "headers": dict(request.header_items()),
            }
        )
        status, payload = self.responses.pop(0)
        if status >= 400:
            raise HTTPError(
                request.full_url,
                status,
                "qdrant error",
                {},
                io.BytesIO(json.dumps(payload).encode("utf-8")),
            )
        return _Response(payload)


def _index_request(requests):
    remote_indexes: set[str] = set()

    def request(method: str, path: str, body=None, *, params=None):
        requests.append((method, path, body or {}, params or {}))
        if method == "PUT" and path.endswith("/index"):
            remote_indexes.add(str((body or {}).get("field_name")))
            return {"result": {"status": "completed"}}
        if method == "DELETE" and "/index/" in path:
            remote_indexes.discard(path.rsplit("/", 1)[-1])
            return {"result": {"status": "completed"}}
        if method == "GET":
            return {
                "result": {
                    "status": "green",
                    "optimizer_status": "ok",
                    "update_queue": 0,
                    "payload_schema": {
                        field: {"data_type": "keyword"} for field in remote_indexes
                    }
                }
            }
        if method == "POST" and path.endswith("/points"):
            return {"result": []}
        return {}

    return request


def _qdrant_adapter_for_collection(
    collection: QdrantCollection,
    *,
    distance_metric: str = "cosine",
    sparse_weight: float = 0.0,
) -> QdrantCollectionAdapter:
    adapter = QdrantCollectionAdapter(
        url="http://qdrant.local",
        api_key=None,
        timeout_seconds=1.0,
        project_name="project",
        collection_name="docs",
        index_name="default",
        distance_metric=distance_metric,
        dimension=2,
        sparse_weight=sparse_weight,
        dense_vector_name="dense",
        sparse_vector_name="sparse",
    )
    adapter._collection = collection
    return adapter


def _target_marker(**updates):
    marker = {
        "_openviking_meta_version": 1,
        "collection_name": "generation-data",
        "metadata_collection_name": "generation-meta",
        "logical_collection": "project/docs",
        "migration_id": "migration-1",
        "migration_state": "ready",
        "migrator_version": "qdrant-blue-green-v1",
        "source_collection": "legacy-data",
        "source_metadata_collection": "legacy-meta",
        "source_fingerprint": "source-fingerprint",
        "metadata_fingerprint": "metadata-fingerprint",
        "sparse_map_fingerprint": "sparse-map-fingerprint",
        "target_count": 1,
        "target_collection": "generation-data",
        "target_metadata_collection": "generation-meta",
        "vector_dimension": 2,
        "dense_datatype": "float16",
        "sparse_modifier": "idf",
        "sparse_datatype": "float16",
        "acl_incomplete_count": 0,
        "sparse_term_count": 2,
        "sparse_term_fingerprint": "sparse-term-fingerprint",
        "source_count": 1,
        "setup_complete": True,
        "schema": {"CollectionName": "docs", "Fields": []},
        "dense_vector_name": "dense",
        "sparse_vector_name": "sparse",
        "vector_dim": 2,
        "distance": "Cosine",
        "sparse_enabled": True,
        "sparse_weight": 0.5,
        "indexes": {},
    }
    marker = dict(marker)
    marker.update(updates)
    return marker


def _legacy_current_marker(**updates):
    marker = {
        "_openviking_meta_version": 1,
        "collection_name": "docs",
        "metadata_collection_name": "docs__meta",
        "schema": {"CollectionName": "docs", "Fields": []},
        "dense_vector_name": "dense",
        "sparse_vector_name": "sparse",
        "vector_dim": 2,
        "distance": "Cosine",
        "sparse_enabled": False,
        "sparse_weight": 0.0,
        "indexes": {},
    }
    marker.update(updates)
    return marker


def test_path_payload_includes_self_and_ancestors() -> None:
    payload = build_qdrant_payload(
        {
            "id": "doc-1",
            "uri": "viking://resources/wiki/physics/doc.md",
            "parent_uri": "viking://resources/wiki/physics",
            "account_id": "acct",
        }
    )

    assert payload["uri"] == "/resources/wiki/physics/doc.md"
    assert payload["uri_depth"] == 4
    assert payload["scope_roots"] == [
        "/",
        "/resources",
        "/resources/wiki",
        "/resources/wiki/physics",
        "/resources/wiki/physics/doc.md",
    ]
    assert payload["parent_uri"] == "/resources/wiki/physics"


def test_path_scope_depth_mapping_is_segment_aware() -> None:
    subtree = compile_qdrant_filter(PathScope("uri", "viking://resources", depth=-1))
    finite = compile_qdrant_filter(PathScope("uri", "viking://resources/wiki", depth=2))

    assert subtree == {
        "must": [
            {
                "key": "scope_roots",
                "match": {"value": "/resources"},
            }
        ]
    }
    assert finite == {
        "must": [
            {
                "key": "scope_roots",
                "match": {"value": "/resources/wiki"},
            },
            {
                "key": "uri_depth",
                "range": {"lte": 4},
            },
        ]
    }


def test_path_scope_rejects_non_string_uri_paths() -> None:
    with pytest.raises(ValueError, match="URI path"):
        compile_qdrant_filter(PathScope("uri", 123, depth=-1))  # type: ignore[arg-type]


def test_parent_uri_path_scope_is_rejected_without_scope_payload() -> None:
    with pytest.raises(NotImplementedError, match="scope payload"):
        compile_qdrant_filter(PathScope("parent_uri", "viking://resources", depth=-1))


def test_multi_tag_eq_is_qdrant_must_and_in_is_match_any() -> None:
    result = compile_qdrant_filter(
        And(
            [
                Eq("search_tags", "team=search"),
                Eq("search_tags", "env=prod"),
            ]
        )
    )

    assert result == {
        "must": [
            {"key": "search_tags", "match": {"value": "team=search"}},
            {"key": "search_tags", "match": {"value": "env=prod"}},
        ]
    }
    assert compile_qdrant_filter(
        In("search_tags", ["team=search", "team=infra"])
    ) == {
        "must": [
            {
                "key": "search_tags",
                "match": {"any": ["team=search", "team=infra"]},
            }
        ]
    }


def test_account_filter_is_preserved() -> None:
    result = compile_qdrant_filter(
        And(
            [
                Eq("account_id", "acct"),
                PathScope("uri", "viking://resources", depth=-1),
            ]
        )
    )

    assert result["must"][0] == {
        "key": "account_id",
        "match": {"value": "acct"},
    }
    assert result["must"][1]["key"] == "scope_roots"


def test_composed_raw_filter_preserves_all_boolean_clauses() -> None:
    result = compile_qdrant_filter(
        And(
            [
                RawDSL(
                    {
                        "must": [{"key": "account_id", "match": {"value": "acct"}}],
                        "should": [{"key": "kind", "match": {"value": "doc"}}],
                    }
                ),
                Eq("name", "README.md"),
            ]
        )
    )

    assert result == {
        "must": [
            {"key": "account_id", "match": {"value": "acct"}},
            {"key": "name", "match": {"value": "README.md"}},
        ],
        "should": [{"key": "kind", "match": {"value": "doc"}}],
    }


def test_legacy_raw_filter_is_compiled_when_combined_with_account_filter() -> None:
    result = compile_qdrant_filter(
        And(
            [
                Eq("account_id", "acct"),
                RawDSL(
                    {
                        "op": "and",
                        "conds": [
                            {
                                "op": "must",
                                "field": "search_tags",
                                "conds": ["team=search"],
                            },
                            {
                                "op": "must",
                                "field": "search_tags",
                                "conds": ["env=prod"],
                            },
                        ],
                    }
                ),
            ]
        )
    )

    assert result == {
        "must": [
            {"key": "account_id", "match": {"value": "acct"}},
            {"key": "search_tags", "match": {"value": "team=search"}},
            {"key": "search_tags", "match": {"value": "env=prod"}},
        ]
    }


def test_or_does_not_flatten_must_not_into_should() -> None:
    result = compile_qdrant_filter(
        Or(
            [
                RawDSL(
                    {
                        "must_not": [
                            {"key": "kind", "match": {"value": "draft"}},
                        ]
                    }
                ),
                Eq("account_id", "acct"),
            ]
        )
    )

    assert result == {
        "should": [
            {
                "must_not": [
                    {"key": "kind", "match": {"value": "draft"}},
                ]
            },
            {"key": "account_id", "match": {"value": "acct"}},
        ]
    }


def test_point_id_is_deterministic_and_original_id_round_trips() -> None:
    first = to_qdrant_point_id("viking://resources/doc.md")
    second = to_qdrant_point_id("viking://resources/doc.md")
    payload = build_qdrant_payload({"id": "viking://resources/doc.md", "uri": "viking://resources/doc.md"})

    assert first == second
    assert first != "viking://resources/doc.md"
    assert payload["_openviking_original_id"] == "viking://resources/doc.md"


def test_parent_uri_round_trips_on_read() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    record = collection._payload_to_record(
        {
            "id": to_qdrant_point_id("doc-1"),
            "payload": {
                "_openviking_original_id": "doc-1",
                "uri": "/resources/doc.md",
                "parent_uri": "/resources",
            },
        }
    )

    assert record["parent_uri"] == "/resources"


def test_numeric_scalar_field_types_map_to_qdrant_numeric_schemas() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._schema = {
        "Fields": [
            {"FieldName": "score", "FieldType": "float32"},
            {"FieldName": "counts", "FieldType": "list<int64>"},
        ]
    }

    assert collection._field_schema("score") == "float"
    assert collection._field_schema("counts") == "integer"


@pytest.mark.asyncio
async def test_qdrant_schema_update_uses_public_adapter_method():
    calls = []

    class PublicAdapter:
        mode = "qdrant"

        def update_collection_schema(self, fields, scalar_index, index_name):
            calls.append((fields, scalar_index, index_name))

    fields = [{"FieldName": "acl_enabled", "FieldType": "bool"}]
    await _AsyncVectorAdapter(PublicAdapter()).update_collection_schema(
        fields, ["acl_enabled"], "custom-index"
    )
    assert calls == [(fields, ["acl_enabled"], "custom-index")]


@pytest.mark.asyncio
async def test_update_collection_schema_accepts_openviking_field_list() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._schema = {
        "CollectionName": "docs",
        "Fields": [{"FieldName": "legacy", "FieldType": "string"}],
    }
    collection._indexes = {"default": {"ScalarIndex": ["account_id"]}}
    marker_writes: list[dict[str, object]] = []
    collection._write_metadata_marker = lambda: marker_writes.append(  # type: ignore[method-assign]
        dict(collection._schema)
    )
    collection._ensure_remote_indexes = lambda _meta: None  # type: ignore[method-assign]

    await _AsyncVectorAdapter(_qdrant_adapter_for_collection(collection)).update_collection_schema(
        [
            {"FieldName": "acl_enabled", "FieldType": "bool"},
        ],
        ["account_id", "acl_enabled"],
        "default",
    )

    assert collection.get_meta_data()["Fields"] == [
        {"FieldName": "legacy", "FieldType": "string"},
        {"FieldName": "acl_enabled", "FieldType": "bool"},
    ]
    assert collection.get_meta_data()["ScalarIndex"] == [
        "account_id",
        "acl_enabled",
    ]
    assert collection.get_index_meta_data("default")["ScalarIndex"] == [
        "account_id",
        "acl_enabled",
    ]
    assert marker_writes == [
        {
            "CollectionName": "docs",
            "ScalarIndex": [
                "account_id",
                "acl_enabled",
            ],
            "Fields": [
                {"FieldName": "legacy", "FieldType": "string"},
                {"FieldName": "acl_enabled", "FieldType": "bool"},
            ],
        },
        {
            "CollectionName": "docs",
            "ScalarIndex": [
                "account_id",
                "acl_enabled",
            ],
            "Fields": [
                {"FieldName": "legacy", "FieldType": "string"},
                {"FieldName": "acl_enabled", "FieldType": "bool"},
            ],
        },
    ]


def test_update_preserves_existing_same_name_field_metadata() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._schema = {
        "CollectionName": "docs",
        "Fields": [{"FieldName": "legacy", "FieldType": "string", "DefaultValue": "keep"}],
    }
    collection._write_metadata_marker = lambda: None  # type: ignore[method-assign]

    collection.update(
        fields=[
            {"FieldName": "legacy", "FieldType": "int64", "DefaultValue": 0},
            {"FieldName": "acl_enabled", "FieldType": "bool"},
        ]
    )

    assert collection.get_meta_data()["Fields"] == [
        {"FieldName": "legacy", "FieldType": "string", "DefaultValue": "keep"},
        {"FieldName": "acl_enabled", "FieldType": "bool"},
    ]


@pytest.mark.asyncio
async def test_update_collection_schema_creates_missing_qdrant_index() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._schema = {
        "CollectionName": "docs",
        "Fields": [{"FieldName": "account_id", "FieldType": "string"}],
        "ScalarIndex": ["account_id", "tenant_custom"],
    }
    collection._indexes = {}
    collection._write_metadata_marker = lambda: None  # type: ignore[method-assign]
    requests: list[tuple[str, str, dict[str, object], dict[str, object]]] = []

    collection._client.request = _index_request(requests)  # type: ignore[method-assign]
    adapter = _qdrant_adapter_for_collection(
        collection, distance_metric="dot", sparse_weight=0.35
    )
    await _AsyncVectorAdapter(adapter).update_collection_schema(
        [
            {"FieldName": "account_id", "FieldType": "string"},
            {"FieldName": "acl_enabled", "FieldType": "bool"},
        ],
        ["account_id", "acl_enabled"],
        "default",
    )

    assert collection.get_index_meta_data("default") == {
        "IndexName": "default",
        "VectorIndex": {"IndexType": "hnsw_hybrid", "Distance": "dot"},
        "ScalarIndex": ["account_id", "tenant_custom", "acl_enabled"],
        "SparseWeight": 0.35,
    }
    assert (
        "PUT",
        "/collections/docs/index",
        {"field_name": "tenant_custom", "field_schema": "keyword"},
        {"wait": "true"},
    ) in requests
    assert (
        "PUT",
        "/collections/docs/index",
        {"field_name": "acl_enabled", "field_schema": "bool"},
        {"wait": "true"},
    ) in requests


@pytest.mark.asyncio
async def test_non_qdrant_schema_update_keeps_local_branch_operations() -> None:
    calls = []

    class LocalCollection:
        def get_meta_data(self):
            return {"Fields": [{"FieldName": "existing", "FieldType": "string"}]}

        def update(self, *, fields):
            calls.append(("fields", fields))

        def get_index_meta_data(self, index_name):
            assert index_name == "default"
            return {"ScalarIndex": ["existing_index"]}

        def update_index(self, index_name, *, scalar_index):
            calls.append(("index", index_name, scalar_index))

    class LocalAdapter:
        mode = "local"

        def get_collection(self):
            return LocalCollection()

    await _AsyncVectorAdapter(LocalAdapter()).update_collection_schema(
        [
            {"FieldName": "existing", "FieldType": "string"},
            {"FieldName": "added", "FieldType": "bool"},
        ],
        ["existing_index", "added_index"],
        "default",
    )

    assert calls == [
        ("fields", [{"FieldName": "added", "FieldType": "bool"}]),
        ("index", "default", ["existing_index", "added_index"]),
    ]


@pytest.mark.asyncio
async def test_qdrant_schema_update_retries_after_index_failure() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._schema = {
        "CollectionName": "docs",
        "Fields": [{"FieldName": "account_id", "FieldType": "string"}],
    }
    collection._write_metadata_marker = lambda: None  # type: ignore[method-assign]
    requests: list[tuple[str, str, dict[str, object], dict[str, object]]] = []
    successful_request = _index_request(requests)
    failed = False

    def request(method, path, body=None, *, params=None):
        nonlocal failed
        if method == "PUT" and path.endswith("/index") and not failed:
            failed = True
            raise QdrantError("index failed", status=503)
        return successful_request(method, path, body, params=params)

    collection._client.request = request  # type: ignore[method-assign]
    adapter = _qdrant_adapter_for_collection(collection)

    with pytest.raises(QdrantError, match="index failed"):
        await _AsyncVectorAdapter(adapter).update_collection_schema(
            [{"FieldName": "account_id", "FieldType": "string"}],
            ["account_id"],
            "default",
        )
    assert not collection.has_index("default")

    await _AsyncVectorAdapter(adapter).update_collection_schema(
        [{"FieldName": "account_id", "FieldType": "string"}],
        ["account_id"],
        "default",
    )

    assert collection.get_index_meta_data("default")["ScalarIndex"] == ["account_id"]


@pytest.mark.asyncio
async def test_update_collection_schema_preserves_custom_qdrant_indexes() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._schema = {
        "CollectionName": "docs",
        "Fields": [
            {"FieldName": "account_id", "FieldType": "string"},
            {"FieldName": "tenant_custom", "FieldType": "string"},
        ],
        "ScalarIndex": ["account_id", "tenant_custom"],
    }
    collection._indexes = {
        "default": {"ScalarIndex": ["account_id", "tenant_custom"]},
    }
    collection._write_metadata_marker = lambda: None  # type: ignore[method-assign]
    requests: list[tuple[str, str, dict[str, object], dict[str, object]]] = []

    collection._client.request = _index_request(requests)  # type: ignore[method-assign]
    await _AsyncVectorAdapter(_qdrant_adapter_for_collection(collection)).update_collection_schema(
        [
            {"FieldName": "account_id", "FieldType": "string"},
            {"FieldName": "acl_enabled", "FieldType": "bool"},
        ],
        ["account_id", "acl_enabled"],
        "default",
    )

    assert collection.get_meta_data()["ScalarIndex"] == [
        "account_id",
        "tenant_custom",
        "acl_enabled",
    ]
    assert collection.get_index_meta_data("default")["ScalarIndex"] == [
        "account_id",
        "tenant_custom",
        "acl_enabled",
    ]
    assert not any(
        method == "DELETE" and path.endswith("/tenant_custom")
        for method, path, _body, _params in requests
    )


def test_drop_index_removes_remote_payload_indexes_and_metadata() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._indexes = {"default": {"ScalarIndex": ["account_id"]}}
    marker_writes: list[dict[str, object]] = []
    collection._write_metadata_marker = lambda: marker_writes.append(  # type: ignore[method-assign]
        dict(collection._indexes)
    )

    requests: list[tuple[str, str, dict[str, object], dict[str, object]]] = []

    collection._client.request = _index_request(requests)  # type: ignore[method-assign]

    assert collection.drop_index("default") is True
    assert [request for request in requests if request[0] != "GET"] == [
        (
            "DELETE",
            "/collections/docs/index/account_id",
            {},
            {"wait": "true"},
        ),
        (
            "DELETE",
            "/collections/docs/index/uri_depth",
            {},
            {"wait": "true"},
        ),
        (
            "DELETE",
            "/collections/docs/index/scope_roots",
            {},
            {"wait": "true"},
        ),
    ]
    assert collection.list_indexes() == []
    assert marker_writes == [{}]


def test_drop_index_keeps_shared_uri_indexes_for_remaining_indexes() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._indexes = {
        "one": {"ScalarIndex": ["account_id"]},
        "two": {"ScalarIndex": ["kind"]},
    }
    collection._write_metadata_marker = lambda: None  # type: ignore[method-assign]
    requests: list[tuple[str, str, dict[str, object], dict[str, object]]] = []

    collection._client.request = _index_request(requests)  # type: ignore[method-assign]

    assert collection.drop_index("one") is True
    assert [request for request in requests if request[0] != "GET"] == [
        (
            "DELETE",
            "/collections/docs/index/account_id",
            {},
            {"wait": "true"},
        )
    ]


def test_update_index_removes_remote_fields_removed_from_metadata() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._indexes = {"default": {"ScalarIndex": ["account_id", "kind"]}}
    collection._write_metadata_marker = lambda: None  # type: ignore[method-assign]
    requests: list[tuple[str, str, dict[str, object], dict[str, object]]] = []

    collection._client.request = _index_request(requests)  # type: ignore[method-assign]

    assert collection.update_index("default", scalar_index=["account_id"]) == {
        "ScalarIndex": ["account_id"]
    }
    assert (
        "DELETE",
        "/collections/docs/index/kind",
        {},
        {"wait": "true"},
    ) in requests
    assert collection.get_index_meta_data("default") == {
        "ScalarIndex": ["account_id"]
    }


def test_update_index_ignores_missing_indexes() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    requests: list[tuple[str, str, dict[str, object], dict[str, object]]] = []

    collection._client.request = _index_request(requests)  # type: ignore[method-assign]
    collection._write_metadata_marker = lambda: pytest.fail(  # type: ignore[method-assign]
        "missing index must not publish metadata"
    )

    assert collection.update_index("missing", scalar_index=["foo"]) is None
    assert collection.list_indexes() == []
    assert requests == []


def test_drop_index_keeps_metadata_when_remote_delete_fails() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._indexes = {"default": {"ScalarIndex": ["account_id"]}}
    marker_writes: list[dict[str, object]] = []
    collection._write_metadata_marker = lambda: marker_writes.append(  # type: ignore[method-assign]
        dict(collection._indexes)
    )

    def request(*_args, **_kwargs):
        raise QdrantError("delete failed", status=503)

    collection._client.request = request  # type: ignore[method-assign]

    with pytest.raises(QdrantError, match="delete failed"):
        collection.drop_index("default")
    assert collection.get_index_meta_data("default") == {
        "ScalarIndex": ["account_id"]
    }
    assert marker_writes == []


def test_drop_index_keeps_retryable_metadata_when_marker_write_fails() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._indexes = {"default": {"ScalarIndex": ["account_id"]}}
    marker_attempts = 0

    def write_marker():
        nonlocal marker_attempts
        marker_attempts += 1
        if marker_attempts == 1:
            raise QdrantError("marker failed", status=503)

    collection._write_metadata_marker = write_marker  # type: ignore[method-assign]
    collection._client.request = _index_request([])  # type: ignore[method-assign]

    with pytest.raises(QdrantError, match="marker failed"):
        collection.drop_index("default")
    assert collection.has_index("default")

    assert collection.drop_index("default") is True
    assert not collection.has_index("default")
    assert marker_attempts == 2


def test_update_index_keeps_retryable_metadata_when_marker_write_fails() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._indexes = {"default": {"ScalarIndex": ["account_id", "kind"]}}
    marker_attempts = 0

    def write_marker():
        nonlocal marker_attempts
        marker_attempts += 1
        if marker_attempts == 1:
            raise QdrantError("marker failed", status=503)

    collection._write_metadata_marker = write_marker  # type: ignore[method-assign]
    collection._client.request = _index_request([])  # type: ignore[method-assign]

    with pytest.raises(QdrantError, match="marker failed"):
        collection.update_index("default", scalar_index=["account_id"])
    assert collection.get_index_meta_data("default") == {
        "ScalarIndex": ["account_id", "kind"]
    }

    assert collection.update_index("default", scalar_index=["account_id"]) == {
        "ScalarIndex": ["account_id"]
    }
    assert collection.get_index_meta_data("default") == {
        "ScalarIndex": ["account_id"]
    }
    assert marker_attempts == 2


def test_create_index_keeps_retryable_metadata_when_marker_write_fails() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    marker_attempts = 0

    def write_marker():
        nonlocal marker_attempts
        marker_attempts += 1
        if marker_attempts == 1:
            raise QdrantError("marker failed", status=503)

    collection._write_metadata_marker = write_marker  # type: ignore[method-assign]
    collection._client.request = _index_request([])  # type: ignore[method-assign]

    with pytest.raises(QdrantError, match="marker failed"):
        collection.create_index("default", {"ScalarIndex": ["account_id"]})
    assert not collection.has_index("default")

    assert collection.create_index("default", {"ScalarIndex": ["account_id"]}) == {
        "ScalarIndex": ["account_id"]
    }
    assert collection.has_index("default")
    assert marker_attempts == 2


def test_create_index_does_not_publish_metadata_after_remote_400() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    def request(*_args, **_kwargs):
        raise QdrantError("bad schema", status=400)

    collection._client.request = request  # type: ignore[method-assign]
    with pytest.raises(QdrantError, match="bad schema"):
        collection.create_index("default", {"ScalarIndex": ["account_id"]})
    assert collection.list_indexes() == []


def test_sparse_term_collision_raises_instead_of_merging() -> None:
    persisted: dict[str, int] = {}
    by_index = {7: "existing-term"}

    dictionary = SparseTermDictionary(
        resolve_term=lambda term: persisted.get(term),
        resolve_index=lambda index: by_index.get(index),
        persist=lambda term, index: persisted.__setitem__(term, index),
        hash_term=lambda _term: 7,
    )

    with pytest.raises(ValueError, match="sparse term index collision"):
        dictionary.index_for("new-term")


def test_sparse_term_index_fits_qdrant_uint32() -> None:
    index = stable_sparse_index("qdrant")

    assert 0 < index <= 0x7FFF_FFFF


def test_sparse_term_index_rejects_values_outside_qdrant_range() -> None:
    dictionary = SparseTermDictionary(
        resolve_term=lambda _term: None,
        resolve_index=lambda _index: None,
        persist=lambda _term, _index: None,
        hash_term=lambda _term: 0x1_0000_0000,
    )

    with pytest.raises(ValueError, match="Qdrant-compatible uint32"):
        dictionary.index_for("token")


def test_sparse_encoding_rejects_non_finite_weights() -> None:
    dictionary = SparseTermDictionary(
        resolve_term=lambda _term: None,
        resolve_index=lambda _index: None,
        persist=lambda _term, _index: None,
    )

    with pytest.raises(ValueError, match="finite"):
        dictionary.encode({"token": math.nan})


def test_contains_is_rejected_until_substring_semantics_are_defined() -> None:
    with pytest.raises(NotImplementedError, match="Contains"):
        compile_qdrant_filter(Contains("name", "partial"))


def test_legacy_raw_filter_is_compiled_instead_of_sent_to_qdrant_unchanged() -> None:
    assert compile_qdrant_filter(
        {
            "op": "and",
            "conds": [
                {"op": "must", "field": "account_id", "conds": ["acct"]},
                {
                    "op": "must",
                    "field": "uri",
                    "conds": ["viking://resources/doc.md"],
                    "para": "-d=0",
                },
            ],
        }
    ) == {
        "must": [
            {"key": "account_id", "match": {"value": "acct"}},
            {"key": "uri", "match": {"value": "/resources/doc.md"}},
        ]
    }


def test_rest_client_sends_json_and_api_key() -> None:
    transport = _ScriptedTransport((200, {"result": {"ok": True}}))
    client = QdrantRestClient(
        "http://qdrant.local/",
        api_key="secret",
        timeout_seconds=3,
        opener=transport,
    )

    assert client.request("post", "collections/demo", {"hello": "world"}, params={"wait": True}) == {
        "result": {"ok": True}
    }
    request = transport.requests[0]
    assert request["method"] == "POST"
    assert urlsplit(request["url"]).path == "/collections/demo"
    assert parse_qs(urlsplit(request["url"]).query) == {"wait": ["True"]}
    assert request["body"] == {"hello": "world"}
    assert request["timeout"] == 3.0
    assert request["headers"]["Api-key"] == "secret"


def test_rest_client_exposes_and_applies_timeout() -> None:
    transport = _ScriptedTransport((200, {"result": True}))
    client = QdrantRestClient(
        "http://qdrant.local",
        timeout_seconds=17,
        opener=transport,
    )

    client.request("GET", "/collections/demo")

    assert client.timeout_seconds == 17.0
    assert transport.requests[0]["timeout"] == 17.0


@pytest.mark.parametrize("timeout", [True, False, 0, -1, math.nan, math.inf])
def test_rest_client_rejects_invalid_timeout(timeout: object) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        QdrantRestClient("http://qdrant.local", timeout_seconds=timeout)  # type: ignore[arg-type]


def test_point_mutations_use_wait_and_strong_ordering() -> None:
    transport = _ScriptedTransport(
        (200, {"result": {"status": "completed"}}),
        (200, {"result": {"status": "completed"}}),
    )
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=transport),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    collection.upsert_data(
        [{"id": "doc-1", "uri": "viking://resources/doc.md", "vector": [0.1, 0.2]}]
    )
    collection.delete_data(["doc-1"])

    assert all(
        request["params"] == {"wait": ["true"], "ordering": ["strong"]}
        for request in transport.requests
    )


def test_point_mutations_require_completed_result() -> None:
    transport = _ScriptedTransport((200, {"result": {"status": "acknowledged"}}))
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=transport),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    with pytest.raises(QdrantError, match="did not complete"):
        collection.upsert_data(
            [{"id": "doc-1", "uri": "viking://resources/doc.md", "vector": [0.1, 0.2]}]
        )


def _readiness_collection(transport, *, timeout_seconds: float = 10.0) -> QdrantCollection:
    return QdrantCollection(
        client=QdrantRestClient(
            "http://qdrant.local",
            timeout_seconds=timeout_seconds,
            opener=transport,
        ),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )


@pytest.mark.parametrize(
    ("readiness", "match"),
    [
        ({"status": "red", "optimizer_status": "ok"}, "not ready"),
        (
            {
                "status": "green",
                "optimizer_status": {"error": "optimizer failed"},
            },
            "not ready",
        ),
    ],
)
def test_payload_index_readiness_rejects_error_states(readiness, match: str) -> None:
    transport = _ScriptedTransport(
        (
            200,
            {
                "result": {
                    **readiness,
                    "payload_schema": {"account_id": {}},
                }
            },
        )
    )
    collection = _readiness_collection(transport)

    with pytest.raises(QdrantError, match=match):
        collection._wait_payload_index("account_id", present=True)


def test_payload_index_readiness_times_out_with_pending_work() -> None:
    transport = _ScriptedTransport(
        *(
            (
                200,
                {
                    "result": {
                        "status": "green",
                        "optimizer_status": "ok",
                        "update_queue": 1,
                        "payload_schema": {"account_id": {}},
                    }
                },
            )
            for _ in range(100)
        )
    )
    collection = _readiness_collection(transport, timeout_seconds=0.01)

    with pytest.raises(QdrantError, match="did not become"):
        collection._wait_payload_index("account_id", present=True)


@pytest.mark.parametrize("status", ["yellow", "grey"])
def test_payload_index_readiness_accepts_available_states_when_index_visible(
    status: str,
) -> None:
    collection = _readiness_collection(
        _ScriptedTransport(),
        timeout_seconds=0.01,
    )
    collection._client.request = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "result": {
            "status": status,
            "optimizer_status": "ok",
            "update_queue": 0,
            "payload_schema": {"account_id": {}},
        }
    }

    collection._wait_payload_index("account_id", present=True)


def test_collection_readiness_still_requires_green_without_payload_index() -> None:
    collection = _readiness_collection(
        _ScriptedTransport(),
        timeout_seconds=0.01,
    )
    collection._client.request = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "result": {
            "status": "yellow",
            "optimizer_status": "ok",
            "update_queue": 0,
        }
    }

    with pytest.raises(QdrantError, match="did not become ready"):
        collection._wait_collection_ready("docs")


@pytest.mark.parametrize("status", ["green", "yellow", "grey"])
def test_payload_index_readiness_rejects_missing_index(status: str) -> None:
    collection = _readiness_collection(
        _ScriptedTransport(),
        timeout_seconds=0.01,
    )
    collection._client.request = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "result": {
            "status": status,
            "optimizer_status": "ok",
            "update_queue": 0,
            "payload_schema": {},
        }
    }

    with pytest.raises(QdrantError, match="did not become visible"):
        collection._wait_payload_index("account_id", present=True)


def test_payload_index_readiness_rejects_malformed_response() -> None:
    collection = _readiness_collection(
        _ScriptedTransport(),
        timeout_seconds=0.01,
    )
    collection._client.request = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "result": {"status": "green"}
    }

    with pytest.raises(QdrantError, match="malformed"):
        collection._wait_payload_index("account_id", present=True)


def test_collection_lifecycle_writes_marker_and_payload_indexes() -> None:
    transport = _ScriptedTransport(
        (404, {}),
        (404, {}),
        (200, {"result": True}),
        (200, {"result": {"status": "green", "optimizer_status": "ok"}}),
        (200, {"result": True}),
        (200, {"result": {"status": "green", "optimizer_status": "ok"}}),
        (200, {"result": {"status": "completed"}}),
    )
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=transport),
        collection_name="project__docs",
        metadata_collection_name="project__docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=3,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.25,
    )

    collection.create_remote_collection(
        {
            "CollectionName": "docs",
            "Fields": [{"FieldName": "vector", "Dim": 3}],
        }
    )
    marker_payload = transport.requests[-1]["body"]["points"][0]["payload"]
    transport.responses.extend(
        [
            (200, {"result": {"status": "completed"}}),
            (
                200,
                {
                    "result": {
                        "status": "green",
                        "optimizer_status": "ok",
                        "update_queue": 0,
                        "payload_schema": {
                            "account_id": {},
                            "search_tags": {},
                            "uri_depth": {},
                            "scope_roots": {},
                        }
                    }
                },
            ),
            (200, {"result": {"status": "completed"}}),
            (
                200,
                {
                    "result": {
                        "status": "green",
                        "optimizer_status": "ok",
                        "update_queue": 0,
                        "payload_schema": {
                            "account_id": {},
                            "search_tags": {},
                            "uri_depth": {},
                            "scope_roots": {},
                        }
                    }
                },
            ),
            (200, {"result": {"status": "completed"}}),
            (
                200,
                {
                    "result": {
                        "status": "green",
                        "optimizer_status": "ok",
                        "update_queue": 0,
                        "payload_schema": {
                            "account_id": {},
                            "search_tags": {},
                            "uri_depth": {},
                            "scope_roots": {},
                        }
                    }
                },
            ),
            (200, {"result": {"status": "completed"}}),
            (
                200,
                {
                    "result": {
                        "status": "green",
                        "optimizer_status": "ok",
                        "update_queue": 0,
                        "payload_schema": {
                            "account_id": {},
                            "search_tags": {},
                            "uri_depth": {},
                            "scope_roots": {},
                        }
                    }
                },
            ),
            (200, {"result": True}),
            (
                200,
                {
                    "result": [
                        {
                            "id": to_qdrant_point_id("openviking:metadata"),
                            "payload": marker_payload,
                        }
                    ]
                },
            ),
            (200, {"result": {"status": "completed"}}),
        ]
    )
    collection.create_index(
        "default",
        {"ScalarIndex": ["account_id", "search_tags"]},
    )

    assert len(transport.requests) == 18
    assert urlsplit(transport.requests[2]["url"]).path == "/collections/project__docs"
    assert transport.requests[2]["body"] == {
        "vectors": {"dense": {"size": 3, "distance": "Cosine"}},
        "sparse_vectors": {"sparse": {}},
    }
    assert transport.requests[2]["params"] == {"timeout": ["10"]}
    assert transport.requests[4]["params"] == {"timeout": ["10"]}
    assert urlsplit(transport.requests[4]["url"]).path == "/collections/project__docs__meta"
    marker = transport.requests[6]["body"]["points"][0]
    assert marker["vector"] == {"meta": [0.0]}
    assert marker["payload"]["_openviking_meta_version"] == 1
    index_requests = [
        request
        for request in transport.requests
        if urlsplit(request["url"]).path.endswith("/index")
    ]
    assert [urlsplit(request["url"]).path for request in index_requests] == [
        "/collections/project__docs/index",
        "/collections/project__docs/index",
        "/collections/project__docs/index",
        "/collections/project__docs/index",
    ]
    assert [request["body"]["field_name"] for request in index_requests] == [
        "account_id",
        "search_tags",
        "uri_depth",
        "scope_roots",
    ]
    assert all(request["params"] == {"wait": ["true"]} for request in index_requests)
    assert [
        urlsplit(request["url"]).path
        for request in transport.requests
        if request["method"] == "GET"
    ].count("/collections/project__docs") >= 2
    assert [
        urlsplit(request["url"]).path
        for request in transport.requests
        if request["method"] == "GET"
    ].count("/collections/project__docs__meta") >= 2


def test_collection_creation_race_fails_closed_before_metadata_marker() -> None:
    transport = _ScriptedTransport(
        (404, {}),
        (404, {}),
        (409, {}),
    )
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=transport),
        collection_name="project__docs",
        metadata_collection_name="project__docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=3,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    with pytest.raises(RuntimeError, match="appeared during creation"):
        collection.create_remote_collection(
            {
                "CollectionName": "docs",
                "Fields": [{"FieldName": "vector", "Dim": 3}],
            }
        )

    assert not any(
        urlsplit(request["url"]).path.endswith("/project__docs__meta/points")
        for request in transport.requests
    )


def test_collection_creation_race_rejects_foreign_data_collection() -> None:
    transport = _ScriptedTransport(
        (404, {}),
        (200, {"result": True}),
        (404, {}),
    )
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={"url": "http://qdrant.local"},
        project="project",
        name="docs",
        dimension=3,
    )
    adapter = QdrantCollectionAdapter.from_config(config)
    adapter._client = QdrantRestClient("http://qdrant.local", opener=transport)

    with pytest.raises(RuntimeError, match="refusing to adopt existing data"):
        adapter.create_collection(
            "docs",
            {"CollectionName": "docs", "Fields": [{"FieldName": "vector", "Dim": 3}]},
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )

    assert not any(request["method"] == "PUT" for request in transport.requests)


def test_collection_lifecycle_infers_dimension_from_vector_field_type() -> None:
    transport = _ScriptedTransport(
        (404, {}),
        (404, {}),
        (200, {"result": True}),
        (200, {"result": {"status": "green", "optimizer_status": "ok"}}),
        (200, {"result": True}),
        (200, {"result": {"status": "green", "optimizer_status": "ok"}}),
        (200, {"result": {"status": "completed"}}),
    )
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=transport),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=0,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    collection.create_remote_collection(
        {
            "CollectionName": "docs",
            "Fields": [{"FieldName": "embedding", "FieldType": "vector", "Dim": 3}],
        }
    )

    assert transport.requests[2]["body"]["vectors"] == {
        "dense": {"size": 3, "distance": "Cosine"}
    }


def test_metadata_marker_round_trips_index_metadata() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    marker: dict[str, object] = {}
    collection._client.request = _index_request([])  # type: ignore[method-assign]
    collection._upsert_points = lambda _name, points: marker.update(points[0]["payload"])  # type: ignore[method-assign]
    collection._schema = {"CollectionName": "docs", "Fields": []}
    collection.create_index("default", {"ScalarIndex": ["account_id"]})

    reloaded = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    reloaded._load_metadata_marker = lambda: marker  # type: ignore[method-assign]

    assert reloaded.get_meta_data() == {"CollectionName": "docs", "Fields": []}
    assert reloaded.has_index("default")
    assert reloaded.get_index_meta_data("default") == {"ScalarIndex": ["account_id"]}


def test_explicit_pair_routes_create_upsert_fetch_delete_to_target_names() -> None:
    marker = {
        "_openviking_meta_version": 1,
        "collection_name": "generation-data",
        "metadata_collection_name": "generation-meta",
        "logical_collection": "project/docs",
        "schema": {"CollectionName": "docs", "Fields": []},
        "dense_vector_name": "vector",
        "sparse_vector_name": "sparse_vector",
        "vector_dim": 2,
        "distance": "Cosine",
        "sparse_enabled": True,
        "sparse_weight": 0.5,
        "indexes": {},
        "setup_complete": True,
    }
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={
            "url": "http://qdrant.local",
            "data_collection_name": "generation-data",
            "metadata_collection_name": "generation-meta",
        },
        project="project",
        name="docs",
        dimension=2,
        sparse_weight=0.5,
    )
    adapter = QdrantCollectionAdapter.from_config(config)
    transport = _ScriptedTransport(
        (404, {}),
        (404, {}),
        (404, {}),
        (200, {"result": True}),
        (200, {"result": {"status": "green", "optimizer_status": "ok"}}),
        (200, {"result": True}),
        (200, {"result": {"status": "green", "optimizer_status": "ok"}}),
        (200, {"result": {"status": "completed"}}),
        (200, {"result": {"status": "completed"}}),
        (
            200,
            {
                "result": {
                    "status": "green",
                    "optimizer_status": "ok",
                    "payload_schema": {"uri_depth": {}},
                }
            },
        ),
        (200, {"result": {"status": "completed"}}),
        (
            200,
            {
                "result": {
                    "status": "green",
                    "optimizer_status": "ok",
                    "payload_schema": {"uri_depth": {}, "scope_roots": {}},
                }
            },
        ),
        (200, {"result": True}),
        (
            200,
            {
                "result": [
                    {
                        "id": to_qdrant_point_id("openviking:metadata"),
                        "payload": marker,
                    }
                ]
            },
        ),
        (200, {"result": {"status": "completed"}}),
        (200, {"result": {"status": "completed"}}),
        (
            200,
            {
                "result": [
                    {
                        "id": to_qdrant_point_id("doc-1"),
                        "payload": {"_openviking_original_id": "doc-1"},
                    }
                ]
            },
        ),
        (200, {"result": {"status": "completed"}}),
    )
    adapter._client = QdrantRestClient("http://qdrant.local", opener=transport)

    assert adapter.create_collection(
        "docs",
        {"CollectionName": "docs", "Fields": []},
        distance="cosine",
        sparse_weight=0.5,
        index_name="default",
    )
    assert adapter.upsert({"id": "doc-1", "vector": [0.1, 0.2]}) == ["doc-1"]
    assert adapter.get(["doc-1"]) == [{"id": "doc-1"}]
    assert adapter.delete(ids=["doc-1"]) == 1

    paths = [(request["method"], urlsplit(request["url"]).path) for request in transport.requests]
    assert paths[3:8] == [
        ("PUT", "/collections/generation-data"),
        ("GET", "/collections/generation-data"),
        ("PUT", "/collections/generation-meta"),
        ("GET", "/collections/generation-meta"),
        ("PUT", "/collections/generation-meta/points"),
    ]
    assert ("PUT", "/collections/generation-data/points") in paths
    assert ("POST", "/collections/generation-data/points") in paths
    assert ("POST", "/collections/generation-data/points/delete") in paths
    assert not any("project__docs" in path for _, path in paths)


def test_target_marker_round_trips_logical_and_physical_identity() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
        logical_collection="project/docs",
    )
    collection._schema = {"CollectionName": "docs", "Fields": []}
    collection._migration_marker_fields = {
        name: value
        for name, value in _target_marker().items()
        if name
        not in {
            "_openviking_meta_version",
            "collection_name",
            "metadata_collection_name",
            "logical_collection",
            "schema",
            "dense_vector_name",
            "sparse_vector_name",
            "vector_dim",
            "distance",
            "sparse_enabled",
            "sparse_weight",
            "indexes",
        }
    }
    marker = collection._metadata_payload()

    reloaded = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
        logical_collection="project/docs",
    )
    reloaded._load_metadata_marker = lambda: marker  # type: ignore[method-assign]

    assert reloaded.has_openviking_metadata()
    assert reloaded.get_meta_data() == {"CollectionName": "docs", "Fields": []}
    assert marker["collection_name"] == "generation-data"
    assert marker["metadata_collection_name"] == "generation-meta"
    assert marker["logical_collection"] == "project/docs"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("migration_id", None),
        ("migration_id", ""),
        ("migration_id", "   "),
        ("migration_state", None),
        ("migration_state", ""),
        ("migration_state", "   "),
    ],
)
def test_migration_marker_requires_nonempty_identity_fields(
    field: str,
    value: object,
) -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
        logical_collection="project/docs",
    )
    marker = _target_marker(**{field: value})
    collection._load_metadata_marker = lambda: marker  # type: ignore[method-assign]

    assert collection.has_openviking_metadata() is False
    with pytest.raises(RuntimeError, match="migration"):
        collection.get_meta_data()


@pytest.mark.parametrize("field", ["migration_id", "migration_state"])
def test_migration_marker_requires_identity_fields_to_be_present(field: str) -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
        logical_collection="project/docs",
    )
    marker = _target_marker()
    marker.pop(field)
    collection._load_metadata_marker = lambda: marker  # type: ignore[method-assign]

    assert collection.has_openviking_metadata() is False
    with pytest.raises(RuntimeError, match="migration"):
        collection.get_meta_data()


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_metadata_marker_version_requires_a_strict_integer(version: object) -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    marker = _legacy_current_marker(_openviking_meta_version=version)
    collection._load_metadata_marker = lambda: marker  # type: ignore[method-assign]

    assert collection.has_openviking_metadata() is False
    with pytest.raises(RuntimeError, match="valid current marker"):
        collection.get_meta_data()


def test_default_derived_collection_keeps_loading_ordinary_legacy_marker() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
        logical_collection="project/docs",
    )
    marker = _legacy_current_marker()
    collection._load_metadata_marker = lambda: marker  # type: ignore[method-assign]

    assert collection.has_openviking_metadata()
    assert collection.get_meta_data() == {"CollectionName": "docs", "Fields": []}


@pytest.mark.parametrize("logical_collection", [None, "project/other"])
def test_explicit_physical_collection_requires_matching_logical_marker(
    logical_collection: str | None,
) -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
        logical_collection="project/docs",
        require_logical_collection=True,
    )
    marker = _legacy_current_marker()
    if logical_collection is None:
        marker.pop("logical_collection", None)
    else:
        marker["logical_collection"] = logical_collection
    collection._load_metadata_marker = lambda: marker  # type: ignore[method-assign]

    assert collection.has_openviking_metadata() is False
    with pytest.raises(RuntimeError, match="logical collection"):
        collection.get_meta_data()


@pytest.mark.parametrize(
    "updates",
    [
        {"target_collection": "generation-data"},
        {"target_metadata_collection": "generation-meta"},
        {"target_collection": "other-data", "target_metadata_collection": "generation-meta"},
        {"target_collection": "generation-data", "target_metadata_collection": "other-meta"},
    ],
)
def test_optional_target_provenance_is_complete_and_canonical(
    updates: dict[str, str],
) -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
        logical_collection="project/docs",
    )
    marker = _target_marker()
    marker.pop("target_collection", None)
    marker.pop("target_metadata_collection", None)
    marker.update(updates)
    collection._load_metadata_marker = lambda: marker  # type: ignore[method-assign]

    assert collection.has_openviking_metadata() is False
    with pytest.raises(RuntimeError, match="target"):
        collection.get_meta_data()


def test_metadata_marker_lookup_rejects_wrong_point_id() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient(
            "http://qdrant.local",
            opener=_ScriptedTransport(
                (200, {"result": {"status": "green"}}),
                (
                    200,
                    {
                        "result": [
                            {
                                "id": "wrong-marker-id",
                                "payload": _legacy_current_marker(),
                            }
                        ]
                    },
                ),
            ),
        ),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    with pytest.raises(RuntimeError, match="marker"):
        collection._load_metadata_marker()


def test_metadata_marker_lookup_rejects_duplicate_points() -> None:
    marker_point = {
        "id": to_qdrant_point_id("openviking:metadata"),
        "payload": _legacy_current_marker(),
    }
    collection = QdrantCollection(
        client=QdrantRestClient(
            "http://qdrant.local",
            opener=_ScriptedTransport(
                (200, {"result": {"status": "green"}}),
                (200, {"result": [marker_point, dict(marker_point)]}),
            ),
        ),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    with pytest.raises(RuntimeError, match="marker"):
        collection._load_metadata_marker()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dense_vector_name", "other-dense"),
        ("sparse_vector_name", "other-sparse"),
        ("distance", "Dot"),
        ("sparse_enabled", False),
        ("sparse_weight", 0.25),
    ],
)
def test_vector_layout_and_sparse_policy_mismatch_is_rejected(
    field: str,
    value: object,
) -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
        logical_collection="project/docs",
    )
    marker = _target_marker(**{field: value})
    collection._load_metadata_marker = lambda: marker  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="differs"):
        collection.get_meta_data()


def test_migration_fields_survive_normal_marker_rewrites() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
        logical_collection="project/docs",
    )
    remote_marker = _target_marker(
        migration_id="remote-migration",
        source_fingerprint="remote-source-fingerprint",
        source_count=2,
        target_count=2,
        transformed_source_fingerprint="remote-transformed-source-fingerprint",
        target_content_fingerprint="remote-target-content-fingerprint",
    )
    remote_marker_fields = {
        name: value
        for name, value in remote_marker.items()
        if name
        not in {
            "_openviking_meta_version",
            "collection_name",
            "metadata_collection_name",
            "logical_collection",
            "schema",
            "dense_vector_name",
            "sparse_vector_name",
            "vector_dim",
            "distance",
            "sparse_enabled",
            "sparse_weight",
            "indexes",
        }
    }
    stale_marker_fields = {
        name: value
        for name, value in _target_marker().items()
        if name in remote_marker_fields
    }
    collection._schema = {"CollectionName": "docs", "Fields": []}
    collection._migration_marker_fields = stale_marker_fields
    loaded_markers: list[dict[str, Any]] = []

    def load_marker() -> dict[str, Any]:
        loaded_markers.append(remote_marker)
        return remote_marker

    collection._marker_loaded_from_remote = True
    collection._load_metadata_marker = load_marker  # type: ignore[method-assign]
    marker: dict[str, Any] = {}
    collection._upsert_points = (  # type: ignore[method-assign]
        lambda _name, points: marker.update(points[0]["payload"])
    )

    collection.update(description="rewritten")

    assert loaded_markers == [remote_marker]
    assert stale_marker_fields["migration_id"] != remote_marker_fields["migration_id"]
    assert stale_marker_fields["target_count"] != remote_marker_fields["target_count"]
    for field_name, expected in remote_marker_fields.items():
        assert marker[field_name] == expected
    assert marker["schema"]["Description"] == "rewritten"


def test_legacy_marker_is_not_loadable_by_current_adapter() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
        logical_collection="project/docs",
    )
    collection._load_metadata_marker = lambda: {  # type: ignore[method-assign]
        "kind": "collection",
        "collection_key": "legacy__context",
        "logical_collection_name": "context",
        "project_name": "legacy",
        "meta": {"CollectionName": "context", "Fields": []},
    }

    assert collection.has_openviking_metadata() is False
    with pytest.raises(RuntimeError, match="valid current marker"):
        collection.get_meta_data()


def test_migration_marker_binds_physical_and_logical_names() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
        logical_collection="project/docs",
    )
    collection._load_metadata_marker = lambda: {  # type: ignore[method-assign]
        "_openviking_meta_version": 1,
        "collection_name": "generation-data",
        "metadata_collection_name": "wrong-meta",
        "logical_collection": "project/other",
        "migration_id": "migration-1",
        "migration_state": "active",
        "schema": {"CollectionName": "docs", "Fields": []},
        "dense_vector_name": "dense",
        "sparse_vector_name": "sparse",
        "vector_dim": 2,
        "distance": "Cosine",
        "sparse_enabled": False,
        "sparse_weight": 0.0,
        "indexes": {},
        "setup_complete": True,
    }

    with pytest.raises(RuntimeError, match="metadata collection|logical collection"):
        collection.get_meta_data()


def test_migration_marker_requires_consistent_vector_dimension() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
        logical_collection="project/docs",
    )
    collection._load_metadata_marker = lambda: {  # type: ignore[method-assign]
        "_openviking_meta_version": 1,
        "collection_name": "generation-data",
        "metadata_collection_name": "generation-meta",
        "logical_collection": "project/docs",
        "migration_id": "migration-1",
        "migration_state": "active",
        "schema": {"CollectionName": "docs", "Fields": []},
        "vector_dim": 2,
        "vector_dimension": 3,
        "setup_complete": True,
    }

    with pytest.raises(RuntimeError, match="vector"):
        collection.get_meta_data()


def test_migration_marker_rejects_state_setup_mismatch() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
        logical_collection="project/docs",
    )
    collection._load_metadata_marker = lambda: {  # type: ignore[method-assign]
        "_openviking_meta_version": 1,
        "collection_name": "generation-data",
        "metadata_collection_name": "generation-meta",
        "logical_collection": "project/docs",
        "migration_id": "migration-1",
        "migration_state": "building",
        "schema": {"CollectionName": "docs", "Fields": []},
        "vector_dim": 2,
        "setup_complete": True,
    }

    with pytest.raises(RuntimeError, match="setup|state"):
        collection.get_meta_data()


def test_metadata_rewrite_preserves_latest_migration_provenance() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
        logical_collection="project/docs",
    )
    collection._schema = {"CollectionName": "docs", "Fields": []}
    collection._migration_marker_fields = {
        "migration_id": "migration-1",
        "migration_state": "active",
        "source_fingerprint": "source",
        "target_count": 1,
    }
    collection._marker_loaded_from_remote = True
    latest_marker = {
        "_openviking_meta_version": 1,
        "collection_name": "generation-data",
        "metadata_collection_name": "generation-meta",
        "logical_collection": "project/docs",
        "migration_id": "migration-1",
        "migration_state": "cutting_over",
        "source_fingerprint": "source",
        "target_count": 2,
        "schema": {"CollectionName": "docs", "Fields": []},
        "setup_complete": True,
    }
    collection._load_metadata_marker = lambda: latest_marker  # type: ignore[method-assign]
    marker: dict[str, object] = {}
    collection._upsert_points = (  # type: ignore[method-assign]
        lambda _name, points: marker.update(points[0]["payload"])
    )

    collection.update(description="updated")

    assert marker["migration_state"] == "cutting_over"
    assert marker["target_count"] == 2


def test_new_collection_refreshes_migration_provenance_before_update() -> None:
    transport = _ScriptedTransport(
        (404, {}),
        (404, {}),
        (200, {"result": True}),
        (200, {"result": {"status": "green", "optimizer_status": "ok"}}),
        (200, {"result": True}),
        (200, {"result": {"status": "green", "optimizer_status": "ok"}}),
        (200, {"result": {"status": "completed"}}),
    )
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=transport),
        collection_name="generation-data",
        metadata_collection_name="generation-meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
        logical_collection="project/docs",
    )

    collection.create_remote_collection({"CollectionName": "docs", "Fields": []})
    external_marker = dict(transport.requests[-1]["body"]["points"][0]["payload"])
    external_marker.update(
        {
            "migration_id": "migration-1",
            "migration_state": "active",
            "source_fingerprint": "source",
            "target_count": 7,
            "setup_complete": True,
            "vector_dimension": 2,
        }
    )
    transport.responses.extend(
        [
            (200, {"result": {"status": "completed"}}),
            (
                200,
                {
                    "result": [
                        {
                            "id": to_qdrant_point_id("openviking:metadata"),
                            "payload": external_marker,
                        }
                    ]
                },
            ),
            (200, {"result": {"status": "completed"}}),
        ]
    )

    collection.update(description="updated")

    rewritten_marker = transport.requests[-1]["body"]["points"][0]["payload"]
    assert rewritten_marker["migration_state"] == "active"
    assert rewritten_marker["source_fingerprint"] == "source"
    assert rewritten_marker["target_count"] == 7


def test_metadata_updates_preserve_migration_provenance() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
    )
    collection._schema = {"CollectionName": "docs", "Fields": []}
    collection._migration_marker_fields = {
        "source_collection": "legacy__docs",
        "source_metadata_collection": "__openviking_meta",
        "source_fingerprint": "source-fingerprint",
        "metadata_fingerprint": "metadata-fingerprint",
        "sparse_map_fingerprint": "sparse-map-fingerprint",
        "setup_complete": True,
        "acl_incomplete_count": 2,
        "dense_datatype": "float16",
        "sparse_modifier": "idf",
    }
    marker: dict[str, object] = {}
    collection._upsert_points = (  # type: ignore[method-assign]
        lambda _name, points: marker.update(points[0]["payload"])
    )

    collection.update(description="updated")

    for field_name, expected in collection._migration_marker_fields.items():
        assert marker[field_name] == expected
    assert marker["schema"]["Description"] == "updated"


def test_missing_original_id_does_not_fabricate_a_record_id() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    with pytest.raises(ValueError, match="_openviking_original_id"):
        collection._payload_to_record(
            {"id": to_qdrant_point_id("doc-1"), "payload": {"uri": "/resources/doc.md"}}
        )


@pytest.mark.parametrize(
    "operation",
    ["update_data", "fetch_data", "search_by_vector", "search_by_id", "search_by_scalar"],
)
def test_public_qdrant_paths_require_original_id(operation: str) -> None:
    point = {
        "id": to_qdrant_point_id("doc-1"),
        "payload": {"uri": "/resources/doc.md"},
    }
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    if operation in {"update_data", "fetch_data", "search_by_id"}:
        collection._retrieve_points = lambda *args, **kwargs: [point]  # type: ignore[method-assign]
    else:
        collection._client.request = lambda *args, **kwargs: {  # type: ignore[method-assign]
            "result": ({"points": [point]} if operation == "search_by_scalar" else [point])
        }

    with pytest.raises(ValueError, match="_openviking_original_id"):
        if operation == "update_data":
            collection.update_data([{"id": "doc-1", "name": "updated"}])
        elif operation == "fetch_data":
            collection.fetch_data(["doc-1"])
        elif operation == "search_by_vector":
            collection.search_by_vector("default", dense_vector=[0.1, 0.2])
        elif operation == "search_by_id":
            collection.search_by_id("default", "doc-1")
        else:
            collection.search_by_scalar("default", "updated_at")


def test_incomplete_migration_marker_is_not_loadable() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._load_metadata_marker = lambda: {  # type: ignore[method-assign]
        "_openviking_meta_version": 1,
        "collection_name": "docs",
        "schema": {"CollectionName": "docs", "Fields": []},
        "setup_complete": False,
    }

    assert collection.has_openviking_metadata() is False
    with pytest.raises(RuntimeError, match="incomplete"):
        collection.get_meta_data()


@pytest.mark.parametrize("setup_complete", ["false", 0, None])
def test_malformed_migration_marker_flag_is_not_loadable(setup_complete) -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._load_metadata_marker = lambda: {  # type: ignore[method-assign]
        "_openviking_meta_version": 1,
        "collection_name": "docs",
        "schema": {"CollectionName": "docs", "Fields": []},
        "setup_complete": setup_complete,
    }

    assert collection.has_openviking_metadata() is False
    with pytest.raises(RuntimeError, match="setup_complete"):
        collection.get_meta_data()


def test_collection_crud_search_count_and_scalar_scroll_use_qdrant_shapes() -> None:
    point_id = to_qdrant_point_id("doc-1")
    transport = _ScriptedTransport(
        (200, {"result": {"status": "completed"}}),
        (
            200,
            {
                "result": [
                    {
                        "id": point_id,
                        "payload": {
                            "_openviking_original_id": "doc-1",
                            "uri": "/resources/doc.md",
                            "name": "doc.md",
                        },
                    }
                ],
            },
        ),
        (200, {"result": {"status": "completed"}}),
        (200, {"result": {"count": 1}}),
        (
            200,
            {
                "result": [
                    {
                        "id": point_id,
                        "score": 0.9,
                        "payload": {
                            "_openviking_original_id": "doc-1",
                            "name": "doc.md",
                        },
                    }
                ],
            },
        ),
        (
            200,
            {
                "result": {
                    "points": [
                        {
                            "id": point_id,
                            "payload": {
                                "_openviking_original_id": "doc-1",
                                "updated_at": 7,
                            },
                        }
                    ]
                }
            },
        ),
    )
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=transport),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="dot",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    collection.upsert_data(
        [{"id": "doc-1", "uri": "viking://resources/doc.md", "vector": [0.1, 0.2]}]
    )
    fetched = collection.fetch_data(["doc-1"])
    collection.delete_data(["doc-1"])
    counted = collection.aggregate_data("default")
    searched = collection.search_by_vector(
        "default",
        dense_vector=[0.1, 0.2],
        limit=1,
        offset=0,
        filters={"must": [{"key": "account_id", "match": {"value": "acct"}}]},
        output_fields=["name"],
    )
    scalar = collection.search_by_scalar(
        "default",
        "updated_at",
        order="desc",
        limit=1,
        output_fields=["updated_at"],
    )

    assert fetched.items[0].id == "doc-1"
    assert counted.agg == {"_total": 1}
    assert searched.data[0].id == "doc-1"
    assert scalar.data[0].fields["updated_at"] == 7

    upsert_body = transport.requests[0]["body"]["points"][0]
    assert upsert_body["vector"] == {"dense": [0.1, 0.2]}
    assert upsert_body["payload"]["uri"] == "/resources/doc.md"
    search_request = transport.requests[4]
    assert urlsplit(search_request["url"]).path == "/collections/docs/points/query"
    assert search_request["body"] == {
        "query": [0.1, 0.2],
        "using": "dense",
        "filter": {"must": [{"key": "account_id", "match": {"value": "acct"}}]},
        "limit": 1,
        "offset": 0,
        "with_payload": {"include": ["name", "_openviking_original_id"]},
        "with_vector": False,
    }
    assert transport.requests[5]["body"]["order_by"] == {
        "key": "updated_at",
        "direction": "desc",
    }
    assert transport.requests[5]["body"]["with_payload"] == {
        "include": ["updated_at", "_openviking_original_id"]
    }


def test_grouped_or_conditional_aggregation_is_rejected_before_http() -> None:
    transport = _ScriptedTransport((200, {"result": {"count": 1}}))
    collection = _readiness_collection(transport)

    with pytest.raises(NotImplementedError, match="grouped or conditional"):
        collection.aggregate_data("default", field="account_id")
    with pytest.raises(NotImplementedError, match="grouped or conditional"):
        collection.aggregate_data("default", cond={"gt": 1})
    assert transport.requests == []


@pytest.mark.parametrize(
    ("output_fields", "expected_fields"),
    [
        (None, {"id": "doc-1", "name": "doc.md", "level": 2}),
        ([], {"id": "doc-1"}),
        (["name"], {"id": "doc-1", "name": "doc.md"}),
        (["name", "level"], {"id": "doc-1", "name": "doc.md", "level": 2}),
    ],
)
def test_scalar_search_preserves_projection_and_sort_score(
    output_fields: list[str] | None,
    expected_fields: dict[str, object],
) -> None:
    collection = _readiness_collection(_ScriptedTransport())
    requested_fields = None if output_fields is None else list(output_fields)
    original_fields = None if requested_fields is None else list(requested_fields)
    seen_selectors = []

    def request(_method: str, _path: str, body=None, *, params=None):
        del params
        selector = body["with_payload"]
        seen_selectors.append(selector)
        payload = {
            "_openviking_original_id": "doc-1",
            "name": "doc.md",
            "level": 2,
        }
        if isinstance(selector, dict):
            payload = {
                key: value for key, value in payload.items() if key in selector["include"]
            }
        return {
            "result": {
                "points": [
                    {
                        "id": to_qdrant_point_id("doc-1"),
                        "payload": payload,
                    }
                ]
            }
        }

    collection._client.request = request  # type: ignore[method-assign]

    result = collection.search_by_scalar(
        "default",
        "level",
        output_fields=requested_fields,
    )

    assert result.data[0].score == 2.0
    assert result.data[0].fields == expected_fields
    assert requested_fields == original_fields
    assert len(seen_selectors) == 1
    if output_fields is None:
        assert seen_selectors[0] is True
    else:
        assert set(seen_selectors[0]["include"]) == {
            "_openviking_original_id",
            "level",
            *output_fields,
        }


def test_fetch_data_converts_each_payload_once() -> None:
    collection = _readiness_collection(_ScriptedTransport())
    point = {
        "id": to_qdrant_point_id("doc-1"),
        "payload": {"_openviking_original_id": "doc-1", "name": "doc.md"},
    }
    conversions = 0

    collection._retrieve_points = lambda *_args, **_kwargs: [point]  # type: ignore[method-assign]

    def payload_to_record(_point):
        nonlocal conversions
        conversions += 1
        return {"id": "doc-1", "name": "doc.md"}

    collection._payload_to_record = payload_to_record  # type: ignore[method-assign]

    result = collection.fetch_data(["doc-1"])

    assert conversions == 1
    assert result.items[0].fields == {"id": "doc-1", "name": "doc.md"}


def test_sparse_query_uses_named_qdrant_sparse_vector_shape() -> None:
    transport = _ScriptedTransport(
        (
            200,
            {
                "result": [
                    {
                        "id": to_qdrant_point_id("doc-1"),
                        "score": 0.4,
                        "payload": {"_openviking_original_id": "doc-1"},
                    }
                ],
            },
        )
    )
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=transport),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
    )

    collection.encode_sparse_vector = lambda vector: {"indices": [7], "values": [1.5]}  # type: ignore[method-assign]
    collection.search_by_vector(
        "default",
        sparse_vector={"token": 1.5},
        limit=1,
    )

    request = transport.requests[0]
    assert urlsplit(request["url"]).path == "/collections/docs/points/query"
    assert request["body"]["query"] == {"indices": [7], "values": [1.5]}
    assert request["body"]["using"] == "sparse"


def test_sparse_decode_rejects_unknown_term_index() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
    )
    collection._resolve_sparse_index = lambda _index: None  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="unknown sparse term index"):
        collection._decode_sparse_vector({"indices": [7], "values": [1.5]})


def test_sparse_decode_rejects_malformed_vectors() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
    )

    with pytest.raises(ValueError, match="indices and values"):
        collection._decode_sparse_vector({"indices": [7], "values": []})
    with pytest.raises(ValueError, match="sparse vector"):
        collection._decode_sparse_vector([])


def test_update_data_rejects_missing_records_before_upsert() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    collection._retrieve_points = lambda *_args, **_kwargs: []  # type: ignore[method-assign]
    upserts: list[list[dict[str, object]]] = []
    collection.upsert_data = lambda data: upserts.append(data)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="record not found"):
        collection.update_data(
            [{"id": "missing", "name": "new", "vector": [0.1, 0.2]}]
        )
    assert upserts == []


def test_update_data_requires_primary_key() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    upserts: list[list[dict[str, object]]] = []
    collection.upsert_data = lambda data: upserts.append(data)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="primary key 'id' is required for update"):
        collection.update_data([{"name": "missing-id"}])
    assert upserts == []


def test_scroll_follows_qdrant_next_page_offset() -> None:
    transport = _ScriptedTransport(
        (
            200,
            {
                "result": {
                    "points": [{"id": "first", "payload": {}}],
                    "next_page_offset": "cursor-2",
                }
            },
        ),
        (
            200,
            {
                "result": {
                    "points": [{"id": "second", "payload": {}}],
                    "next_page_offset": None,
                }
            },
        ),
    )
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=transport),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    points = collection._scroll(
        "docs",
        filter=None,
        limit=2,
    )

    assert [point["id"] for point in points] == ["first", "second"]
    assert transport.requests[1]["body"]["offset"] == "cursor-2"


def test_scroll_rejects_empty_page_with_next_page_offset() -> None:
    transport = _ScriptedTransport(
        (
            200,
            {
                "result": {
                    "points": [],
                    "next_page_offset": "cursor-2",
                }
            },
        ),
    )
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=transport),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    with pytest.raises(QdrantError, match="scroll response is malformed"):
        collection._scroll("docs", filter=None, limit=1)


def test_dense_query_rejects_wrong_dimension() -> None:
    collection = QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=_ScriptedTransport()),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )

    with pytest.raises(ValueError, match="dense query vector dimension"):
        collection.search_by_vector("default", dense_vector=[1.0])


def test_existing_unmarked_collection_fails_closed() -> None:
    transport = _ScriptedTransport(
        (200, {"result": True}),
        (404, {}),
    )
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={"url": "http://qdrant.local"},
        project="project",
        name="docs",
        dimension=2,
    )
    adapter = QdrantCollectionAdapter.from_config(config)

    adapter._client = QdrantRestClient("http://qdrant.local", opener=transport)

    with pytest.raises(RuntimeError, match="metadata is missing"):
        adapter.get_collection()


def test_metadata_only_sidecar_is_not_adopted_by_adapter() -> None:
    transport = _ScriptedTransport(
        (404, {}),
        (404, {}),
        (404, {}),
        (200, {"result": True}),
    )
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={"url": "http://qdrant.local"},
        project="project",
        name="docs",
        dimension=2,
    )
    adapter = QdrantCollectionAdapter.from_config(config)
    adapter._client = QdrantRestClient("http://qdrant.local", opener=transport)

    assert adapter.collection_exists() is False
    with pytest.raises(RuntimeError, match="exists without data collection"):
        adapter.create_collection(
            "docs",
            {"CollectionName": "docs", "Fields": []},
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )

    assert not any(request["method"] == "PUT" for request in transport.requests)


def test_qdrant_rejects_sparse_weight_outside_rrf_range() -> None:
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={"url": "http://qdrant.local"},
        sparse_weight=1.1,
        dimension=2,
    )

    with pytest.raises(ValueError, match="sparse_weight"):
        QdrantCollectionAdapter.from_config(config)


def _sparse_binding(
    term: str,
    index: int,
    *,
    owner: bool,
    payload_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "_openviking_sparse_term": True,
        "term": term,
        "index": index,
    }
    if payload_updates:
        payload.update(payload_updates)
    return {
        "id": (
            sparse_owner_point_id(index)
            if owner
            else to_qdrant_point_id(f"openviking:sparse:{term}")
        ),
        "payload": payload,
    }


class _SparseTransport:
    def __init__(
        self,
        *,
        version: str = "1.16.0",
        points: list[dict[str, Any]] | None = None,
    ) -> None:
        self.version = version
        self.requests: list[dict[str, Any]] = []
        self._points = {str(point["id"]): point for point in points or []}
        self._lock = Lock()
        self.lost_write = False

    def __call__(self, request, timeout):
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        path = urlsplit(request.full_url).path
        with self._lock:
            self.requests.append(
                {
                    "method": request.method,
                    "url": request.full_url,
                    "body": body,
                    "params": dict(parse_qs(urlsplit(request.full_url).query)),
                    "timeout": timeout,
                }
            )

        if request.method == "GET" and path == "/":
            return _Response({"result": {"version": self.version}})

        if request.method == "POST" and path.endswith("/points/scroll"):
            clause = body["filter"]["must"][0]
            key = clause["key"]
            value = clause["match"]["value"]
            with self._lock:
                points = [
                    point
                    for point in self._points.values()
                    if point["payload"].get(key) == value
                ]
            return _Response({"result": {"points": points[: body["limit"]]}})

        if request.method == "PUT" and path.endswith("/points"):
            point = body["points"][0]
            point_id = str(point["id"])
            update_filter = body.get("update_filter")
            with self._lock:
                if update_filter is None:
                    self._points[point_id] = point
                else:
                    assert update_filter == {
                        "must_not": [{"has_id": [point_id]}]
                    }
                    if point_id not in self._points:
                        self._points[point_id] = point
                lost_write = self.lost_write
                self.lost_write = False
            if lost_write:
                raise QdrantError("simulated lost write response")
            return _Response({"result": {"status": "completed"}})

        if request.method == "POST" and path.endswith("/points"):
            with self._lock:
                points = [
                    self._points[point_id]
                    for point_id in body["ids"]
                    if point_id in self._points
                ]
            return _Response({"result": points})

        raise AssertionError(f"unexpected request: {request.method} {request.full_url}")


def _sparse_collection(transport: _SparseTransport) -> QdrantCollection:
    return QdrantCollection(
        client=QdrantRestClient("http://qdrant.local", opener=transport),
        collection_name="docs",
        metadata_collection_name="docs__meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
    )


def test_sparse_encoding_persists_terms_in_metadata_sidecar() -> None:
    transport = _SparseTransport()
    collection = _sparse_collection(transport)

    encoded = collection.encode_sparse_vector({"token": 1.5})

    assert encoded["indices"] and encoded["values"] == [1.5]
    assert transport.requests[0]["body"]["filter"]["must"][0]["key"] == "term"
    assert transport.requests[1]["body"]["filter"]["must"][0]["key"] == "index"
    assert all(
        request["params"] == {"consistency": ["all"]}
        for request in transport.requests
        if request["method"] == "POST"
        and urlsplit(request["url"]).path.endswith(("/points", "/points/scroll"))
    )
    version_request = next(
        request
        for request in transport.requests
        if request["method"] == "GET" and urlsplit(request["url"]).path == "/"
    )
    assert urlsplit(version_request["url"]).path == "/"
    persisted_request = next(
        request
        for request in transport.requests
        if request["method"] == "PUT"
        and urlsplit(request["url"]).path.endswith("/points")
    )
    persisted = persisted_request["body"]["points"][0]
    index = encoded["indices"][0]
    assert persisted["id"] == sparse_owner_point_id(index)
    assert persisted["payload"] == {
        "_openviking_sparse_term": True,
        "term": "token",
        "index": index,
    }
    assert persisted_request["body"]["update_filter"] == {
        "must_not": [{"has_id": [sparse_owner_point_id(index)]}]
    }
    assert persisted_request["params"] == {"wait": ["true"], "ordering": ["strong"]}
    readback_request = next(
        request
        for request in transport.requests
        if request["method"] == "POST"
        and urlsplit(request["url"]).path.endswith("/points")
    )
    assert readback_request["body"]["ids"] == [sparse_owner_point_id(index)]


def test_sparse_owner_keeps_migration_provenance_as_an_all_or_none_pair() -> None:
    transport = _SparseTransport()
    collection = _sparse_collection(transport)
    collection._logical_collection = "project/docs"
    collection._migration_marker_fields = {"migration_id": "migration-1"}

    collection.encode_sparse_vector({"token": 1.5})

    write = next(
        request
        for request in transport.requests
        if request["method"] == "PUT"
        and urlsplit(request["url"]).path.endswith("/points")
    )
    assert write["body"]["points"][0]["payload"]["logical_collection"] == "project/docs"
    assert write["body"]["points"][0]["payload"]["migration_id"] == "migration-1"


def test_sparse_provenance_is_lazily_loaded_once_when_owner_is_registered() -> None:
    transport = _SparseTransport()
    collection = _sparse_collection(transport)
    collection._logical_collection = "project/docs"
    collection._migration_marker_fields = None
    loads = 0

    def load_marker() -> dict[str, Any]:
        nonlocal loads
        loads += 1
        collection._migration_marker_fields = {"migration_id": "migration-1"}
        return {}

    collection._load_metadata_marker = load_marker  # type: ignore[method-assign]

    collection.encode_sparse_vector({"token": 1.5})

    assert loads == 1
    write = next(
        request
        for request in transport.requests
        if request["method"] == "PUT"
        and urlsplit(request["url"]).path.endswith("/points")
    )
    assert write["body"]["points"][0]["payload"]["logical_collection"] == "project/docs"
    assert write["body"]["points"][0]["payload"]["migration_id"] == "migration-1"


_INVALID_SPARSE_PROVENANCE = (
    {"logical_collection": "project/docs"},
    {"migration_id": "migration-1"},
    {"logical_collection": "", "migration_id": "migration-1"},
    {"logical_collection": "project/docs", "migration_id": ""},
    {"logical_collection": None, "migration_id": "migration-1"},
    {"logical_collection": "project/docs", "migration_id": None},
    {"logical_collection": None, "migration_id": None},
    {"logical_collection": "", "migration_id": ""},
    {"logical_collection": "foreign/docs", "migration_id": "migration-1"},
    {"logical_collection": "project/docs", "migration_id": "foreign-migration"},
)


@pytest.mark.parametrize("lookup", ["term", "index"])
@pytest.mark.parametrize("payload_updates", _INVALID_SPARSE_PROVENANCE)
def test_sparse_lookup_rejects_invalid_migration_provenance(
    lookup: str,
    payload_updates: dict[str, Any],
) -> None:
    index = stable_sparse_index("token")
    transport = _SparseTransport(
        points=[
            _sparse_binding(
                "token",
                index,
                owner=False,
                payload_updates=payload_updates,
            )
        ]
    )
    collection = _sparse_collection(transport)
    collection._logical_collection = "project/docs"
    collection._migration_marker_fields = {"migration_id": "migration-1"}

    with pytest.raises(ValueError, match="provenance"):
        if lookup == "term":
            collection._resolve_sparse_term("token")
        else:
            collection._resolve_sparse_index(index)


@pytest.mark.parametrize(
    "payload_updates",
    [None, {"logical_collection": "project/docs", "migration_id": "migration-1"}],
    ids=["absent", "matching"],
)
def test_sparse_lookup_accepts_absent_or_matching_migration_provenance(
    payload_updates: dict[str, Any] | None,
) -> None:
    index = stable_sparse_index("token")
    transport = _SparseTransport(
        points=[
            _sparse_binding(
                "token",
                index,
                owner=False,
                payload_updates=payload_updates,
            )
        ]
    )
    collection = _sparse_collection(transport)
    collection._logical_collection = "project/docs"
    collection._migration_marker_fields = {"migration_id": "migration-1"}

    assert collection._resolve_sparse_term("token") == index
    assert collection._resolve_sparse_index(index) == "token"


def test_sparse_lookup_accepts_absent_provenance_with_logical_only_marker() -> None:
    index = stable_sparse_index("token")
    transport = _SparseTransport(points=[_sparse_binding("token", index, owner=False)])
    collection = _sparse_collection(transport)
    collection._logical_collection = "project/docs"
    collection._migration_marker_fields = {}

    assert collection._resolve_sparse_term("token") == index
    assert collection._resolve_sparse_index(index) == "token"


@pytest.mark.parametrize("payload_updates", _INVALID_SPARSE_PROVENANCE)
def test_sparse_owner_readback_rejects_invalid_migration_provenance(
    payload_updates: dict[str, Any],
) -> None:
    transport = _SparseTransport()
    collection = _sparse_collection(transport)
    collection._logical_collection = "project/docs"
    collection._migration_marker_fields = {"migration_id": "migration-1"}
    retrieve_points = collection._retrieve_points

    def invalid_readback(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        points = retrieve_points(*args, **kwargs)
        assert len(points) == 1
        points[0]["payload"].pop("logical_collection", None)
        points[0]["payload"].pop("migration_id", None)
        points[0]["payload"].update(payload_updates)
        return points

    collection._retrieve_points = invalid_readback  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="provenance"):
        collection.encode_sparse_vector({"token": 1.5})


def test_sparse_owner_readback_accepts_absent_migration_provenance() -> None:
    transport = _SparseTransport()
    collection = _sparse_collection(transport)
    collection._logical_collection = "project/docs"
    collection._migration_marker_fields = {"migration_id": "migration-1"}
    retrieve_points = collection._retrieve_points

    def legacy_readback(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        points = retrieve_points(*args, **kwargs)
        assert len(points) == 1
        points[0]["payload"].pop("logical_collection", None)
        points[0]["payload"].pop("migration_id", None)
        return points

    collection._retrieve_points = legacy_readback  # type: ignore[method-assign]

    assert collection.encode_sparse_vector({"token": 1.5})["values"] == [1.5]


@pytest.mark.parametrize("owner", [False, True])
def test_colliding_sparse_term_cannot_alias_poison_existing_binding(owner: bool) -> None:
    existing_index = stable_sparse_index("69235")
    transport = _SparseTransport(
        points=[_sparse_binding("69235", existing_index, owner=owner)]
    )
    collection = _sparse_collection(transport)

    with pytest.raises(ValueError, match="collision"):
        collection.encode_sparse_vector({"95303": 1.0})

    assert not any(
        request["method"] == "PUT"
        and urlsplit(request["url"]).path.endswith("/points")
        for request in transport.requests
    )
    assert collection._resolve_sparse_index(existing_index) == "69235"


def test_legacy_sparse_binding_resolves_without_registering_owner() -> None:
    index = stable_sparse_index("token")
    transport = _SparseTransport(points=[_sparse_binding("token", index, owner=False)])
    collection = _sparse_collection(transport)

    assert collection.encode_sparse_vector({"token": 1.5}) == {
        "indices": [index],
        "values": [1.5],
    }
    assert not any(
        request["method"] in {"GET", "PUT"}
        and (
            urlsplit(request["url"]).path == "/"
            or urlsplit(request["url"]).path.endswith("/points")
        )
        for request in transport.requests
    )


def test_legacy_sparse_binding_fails_closed_when_owner_is_missing() -> None:
    index = stable_sparse_index("token")
    transport = _SparseTransport(points=[_sparse_binding("token", index, owner=False)])
    collection = _sparse_collection(transport)
    collection._resolve_sparse_index = lambda _index: None  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="existing_term=None"):
        collection.encode_sparse_vector({"token": 1.5})

    assert not any(
        request["method"] == "PUT"
        and urlsplit(request["url"]).path.endswith("/points")
        for request in transport.requests
    )


def test_same_sparse_term_registration_is_idempotent_under_concurrency() -> None:
    transport = _SparseTransport()
    collection = _sparse_collection(transport)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _unused: collection.encode_sparse_vector({"token": 1.5}),
                range(2),
            )
        )

    index = stable_sparse_index("token")
    assert results == [{"indices": [index], "values": [1.5]}] * 2
    writes = [
        request
        for request in transport.requests
        if request["method"] == "PUT"
        and urlsplit(request["url"]).path.endswith("/points")
    ]
    assert writes
    assert all(
        write["body"]["points"][0]["id"] == sparse_owner_point_id(index) for write in writes
    )


def test_lost_sparse_write_response_retries_via_owner_readback() -> None:
    transport = _SparseTransport()
    transport.lost_write = True
    collection = _sparse_collection(transport)

    with pytest.raises(QdrantError, match="lost write"):
        collection.encode_sparse_vector({"token": 1.5})

    index = stable_sparse_index("token")
    assert collection.encode_sparse_vector({"token": 1.5}) == {
        "indices": [index],
        "values": [1.5],
    }
    assert any(
        request["method"] == "POST"
        and urlsplit(request["url"]).path.endswith("/points/scroll")
        and request["body"]["filter"]["must"][0]["key"] == "term"
        for request in transport.requests
    )


def test_unsupported_qdrant_version_prevents_sparse_owner_write() -> None:
    transport = _SparseTransport(version="1.15.9")
    collection = _sparse_collection(transport)

    with pytest.raises(QdrantError, match="1.16.0"):
        collection.encode_sparse_vector({"token": 1.5})

    assert not any(
        request["method"] == "PUT"
        and urlsplit(request["url"]).path.endswith("/points")
        for request in transport.requests
    )


def test_malformed_sparse_lookup_fails_closed_before_owner_write() -> None:
    collection = _sparse_collection(_SparseTransport())

    def malformed_request(method: str, path: str, body=None, *, params=None):
        del params
        if method == "POST" and path.endswith("/points/scroll"):
            return {"result": {"points": "not-a-list"}}
        raise AssertionError(f"unexpected request: {method} {path} {body}")

    collection._client.request = malformed_request  # type: ignore[method-assign]

    with pytest.raises(QdrantError, match="scroll response is malformed"):
        collection.encode_sparse_vector({"token": 1.5})


def test_adapter_recomputes_physical_collection_name_when_logical_name_changes() -> None:
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={"url": "http://qdrant.local"},
        project="project",
        name="initial",
        dimension=2,
    )
    adapter = QdrantCollectionAdapter.from_config(config)

    adapter._collection_name = "created"

    assert adapter._new_collection()._collection_name == "project__created"


def test_explicit_qdrant_physical_names_override_custom_params() -> None:
    config = VectorDBBackendConfig(
        backend="qdrant",
        project="default",
        name="context",
        dimension=2,
        custom_params={
            "data_collection_name": "custom-data",
            "metadata_collection_name": "custom-meta",
        },
        qdrant={
            "url": "http://qdrant.local",
            "data_collection_name": "generation-data",
            "metadata_collection_name": "generation-meta",
        },
    )
    adapter = QdrantCollectionAdapter.from_config(config)
    collection = adapter._new_collection()
    assert collection._collection_name == "generation-data"
    assert collection._metadata_collection_name == "generation-meta"
    assert collection._require_logical_collection is True


def test_data_name_only_derives_metadata_sidecar() -> None:
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={
            "url": "http://qdrant.local",
            "data_collection_name": "generation-data",
        },
        dimension=2,
    )
    adapter = QdrantCollectionAdapter.from_config(config)
    collection = adapter._new_collection()
    assert collection._collection_name == "generation-data"
    assert collection._metadata_collection_name == "generation-data__openviking_meta"


def test_omitted_physical_names_keep_project_name_derivation() -> None:
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={"url": "http://qdrant.local"},
        project="project",
        name="docs",
        dimension=2,
    )
    adapter = QdrantCollectionAdapter.from_config(config)
    collection = adapter._new_collection()
    assert collection._collection_name == "project__docs"
    assert collection._require_logical_collection is False


def test_qdrant_config_accepts_nested_url_and_keeps_content_disabled() -> None:
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={"url": "http://qdrant.local", "dense_vector_name": "dense"},
        dimension=2,
    )

    adapter = QdrantCollectionAdapter.from_config(config)

    assert adapter._client.base_url == "http://qdrant.local"
    assert adapter._dense_vector_name == "dense"
    assert adapter.USE_CONTENT_FIELD is False


def test_qdrant_factory_registry_returns_qdrant_adapter() -> None:
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={"url": "http://qdrant.local"},
        dimension=2,
    )

    assert isinstance(create_collection_adapter(config), QdrantCollectionAdapter)
