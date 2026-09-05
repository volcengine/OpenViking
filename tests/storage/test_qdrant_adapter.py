# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import io
import json
import math
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from openviking.storage.expr import And, Contains, Eq, In, Or, PathScope, RawDSL
from openviking.storage.vectordb.collection.qdrant_collection import QdrantCollection
from openviking.storage.vectordb.collection.qdrant_rest import QdrantError, QdrantRestClient
from openviking.storage.vectordb.qdrant_sparse import SparseTermDictionary, stable_sparse_index
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

    adapter = type(
        "_Adapter",
        (),
        {"mode": "qdrant", "get_collection": lambda self: collection},
    )()

    await _AsyncVectorAdapter(adapter).update_collection_schema(
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

    def request(method: str, path: str, body=None, *, params=None):
        requests.append((method, path, body or {}, params or {}))
        return {}

    collection._client.request = request  # type: ignore[method-assign]
    adapter = type(
        "_Adapter",
        (),
        {
            "mode": "qdrant",
            "_distance_metric": "cosine",
            "_sparse_weight": 0.0,
            "get_collection": lambda self: collection,
            "_build_default_index_meta": lambda self, **kwargs: {
                "IndexName": kwargs["index_name"],
                "ScalarIndex": kwargs["scalar_index_fields"],
            },
        },
    )()

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
        "ScalarIndex": ["account_id", "tenant_custom", "acl_enabled"],
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

    def request(method: str, path: str, body=None, *, params=None):
        requests.append((method, path, body or {}, params or {}))
        return {}

    collection._client.request = request  # type: ignore[method-assign]
    adapter = type(
        "_Adapter",
        (),
        {"mode": "qdrant", "get_collection": lambda self: collection},
    )()

    await _AsyncVectorAdapter(adapter).update_collection_schema(
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

    def request(method: str, path: str, body=None, *, params=None):
        requests.append((method, path, body or {}, params or {}))
        return {}

    collection._client.request = request  # type: ignore[method-assign]

    assert collection.drop_index("default") is True
    assert requests == [
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

    def request(method: str, path: str, body=None, *, params=None):
        requests.append((method, path, body or {}, params or {}))
        return {}

    collection._client.request = request  # type: ignore[method-assign]

    assert collection.drop_index("one") is True
    assert requests == [
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

    def request(method: str, path: str, body=None, *, params=None):
        requests.append((method, path, body or {}, params or {}))
        return {}

    collection._client.request = request  # type: ignore[method-assign]

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

    def request(method: str, path: str, body=None, *, params=None):
        requests.append((method, path, body or {}, params or {}))
        return {}

    collection._client.request = request  # type: ignore[method-assign]
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
    collection._client.request = lambda *_args, **_kwargs: {}  # type: ignore[method-assign]

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
    collection._client.request = lambda *_args, **_kwargs: {}  # type: ignore[method-assign]

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
    collection._client.request = lambda *_args, **_kwargs: {}  # type: ignore[method-assign]

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


def test_collection_lifecycle_writes_marker_and_payload_indexes() -> None:
    transport = _ScriptedTransport(
        (404, {}),
        (200, {"result": True}),
        (404, {}),
        (200, {"result": True}),
        (200, {"result": True}),
        (200, {"result": True}),
        (200, {"result": True}),
        (200, {"result": True}),
        (200, {"result": True}),
        (200, {"result": True}),
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
    collection.create_index(
        "default",
        {"ScalarIndex": ["account_id", "search_tags"]},
    )

    assert [request["method"] for request in transport.requests] == [
        "GET",
        "PUT",
        "GET",
        "PUT",
        "PUT",
        "PUT",
        "PUT",
        "PUT",
        "PUT",
        "PUT",
    ]
    assert urlsplit(transport.requests[1]["url"]).path == "/collections/project__docs"
    assert transport.requests[1]["body"] == {
        "vectors": {"dense": {"size": 3, "distance": "Cosine"}},
        "sparse_vectors": {"sparse": {}},
    }
    assert urlsplit(transport.requests[3]["url"]).path == "/collections/project__docs__meta"
    marker = transport.requests[4]["body"]["points"][0]
    assert marker["vector"] == {"meta": [0.0]}
    assert marker["payload"]["_openviking_meta_version"] == 1
    index_requests = transport.requests[5:9]
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


def test_collection_lifecycle_infers_dimension_from_vector_field_type() -> None:
    transport = _ScriptedTransport(
        (404, {}),
        (200, {"result": True}),
        (404, {}),
        (200, {"result": True}),
        (200, {"result": True}),
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

    assert transport.requests[1]["body"]["vectors"] == {
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
    collection._client.request = lambda *args, **kwargs: {}  # type: ignore[method-assign]
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


def test_collection_crud_search_count_and_scalar_scroll_use_qdrant_shapes() -> None:
    point_id = to_qdrant_point_id("doc-1")
    transport = _ScriptedTransport(
        (200, {"result": True}),
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
        (200, {"result": True}),
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


def test_qdrant_rejects_sparse_weight_outside_rrf_range() -> None:
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={"url": "http://qdrant.local"},
        sparse_weight=1.1,
        dimension=2,
    )

    with pytest.raises(ValueError, match="sparse_weight"):
        QdrantCollectionAdapter.from_config(config)


def test_sparse_encoding_persists_terms_in_metadata_sidecar() -> None:
    transport = _ScriptedTransport(
        (200, {"result": {"points": []}}),
        (200, {"result": {"points": []}}),
        (200, {"result": True}),
        (200, {"result": {"points": []}}),
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

    encoded = collection.encode_sparse_vector({"token": 1.5})

    assert encoded["indices"] and encoded["values"] == [1.5]
    assert transport.requests[0]["body"]["filter"]["must"][0]["key"] == "term"
    assert transport.requests[1]["body"]["filter"]["must"][0]["key"] == "index"
    persisted = transport.requests[2]["body"]["points"][0]
    assert persisted["payload"] == {
        "_openviking_sparse_term": True,
        "term": "token",
        "index": encoded["indices"][0],
    }


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
