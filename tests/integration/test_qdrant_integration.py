# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Integration tests for the Qdrant vectordb adapter.

Usage:

    QDRANT_URL=http://127.0.0.1:6333 pytest tests/integration/test_qdrant_integration.py -v -s
"""

from __future__ import annotations

import uuid

import pytest

from openviking.storage.expr import Contains, Eq, PathScope
from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig
from tests.integration.conftest import QDRANT_API_KEY, QDRANT_URL, requires_qdrant

pytestmark = [pytest.mark.integration, requires_qdrant]


def _build_qdrant_config(collection_name: str) -> VectorDBBackendConfig:
    return VectorDBBackendConfig.model_validate(
        {
            "backend": "qdrant",
            "project": "it",
            "name": collection_name,
            "index_name": "default",
            "distance_metric": "cosine",
            "qdrant": {
                "url": QDRANT_URL,
                "api_key": QDRANT_API_KEY or None,
                "timeout_seconds": 10,
                "dense_vector_name": "vector",
                "sparse_vector_name": "sparse_vector",
                "meta_collection_name": "__openviking_meta",
                "enable_text_index": True,
            },
        }
    )


def _build_schema(collection_name: str) -> dict:
    return {
        "CollectionName": collection_name,
        "Description": "Qdrant integration test collection",
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "uri", "FieldType": "path"},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
            {"FieldName": "created_at", "FieldType": "date_time"},
            {"FieldName": "updated_at", "FieldType": "date_time"},
            {"FieldName": "active_count", "FieldType": "int64"},
            {"FieldName": "level", "FieldType": "int64"},
            {"FieldName": "abstract", "FieldType": "string"},
            {"FieldName": "account_id", "FieldType": "string"},
        ],
        "ScalarIndex": [
            "uri",
            "updated_at",
            "active_count",
            "level",
            "account_id",
            "abstract",
        ],
    }


@pytest.fixture
def qdrant_adapter():
    collection_name = f"qdrant_it_{uuid.uuid4().hex[:8]}"
    adapter = create_collection_adapter(_build_qdrant_config(collection_name))
    try:
        yield adapter, collection_name
    finally:
        try:
            if adapter.collection_exists():
                adapter.drop_collection()
        finally:
            adapter.close()


def test_qdrant_adapter_end_to_end(qdrant_adapter):
    adapter, collection_name = qdrant_adapter
    schema = _build_schema(collection_name)

    created = adapter.create_collection(
        collection_name,
        schema,
        distance="cosine",
        sparse_weight=0.0,
        index_name="default",
    )
    assert created is True
    assert adapter.physical_collection_name == f"it__{collection_name}"
    assert adapter.get_collection().list_indexes() == ["default"]

    records = [
        {
            "id": "doc_1",
            "uri": "viking://resources/acme/docs/a.md",
            "vector": [1.0, 0.0, 0.0, 0.0],
            "created_at": "2026-05-29T12:00:00Z",
            "updated_at": "2026-05-29T12:00:00Z",
            "active_count": 5,
            "level": 2,
            "abstract": "alpha quarterly report",
            "account_id": "acme",
        },
        {
            "id": "doc_2",
            "uri": "viking://resources/acme/docs/b.md",
            "vector": [0.9, 0.1, 0.0, 0.0],
            "created_at": "2026-05-29T12:01:00Z",
            "updated_at": "2026-05-29T12:01:00Z",
            "active_count": 3,
            "level": 2,
            "abstract": "beta design note",
            "account_id": "acme",
        },
        {
            "id": "doc_3",
            "uri": "viking://resources/beta/notes/c.md",
            "vector": [0.0, 1.0, 0.0, 0.0],
            "created_at": "2026-05-29T12:02:00Z",
            "updated_at": "2026-05-29T12:02:00Z",
            "active_count": 1,
            "level": 2,
            "abstract": "gamma meeting note",
            "account_id": "beta",
        },
    ]

    ids = adapter.upsert(records)
    assert len(ids) == 3

    fetched = adapter.get(ids[:2])
    assert len(fetched) == 2
    assert fetched[0]["uri"].startswith("viking://resources/")
    assert "parent_uri" not in fetched[0]
    assert "scope_roots" not in fetched[0]

    assert adapter.count() == 3
    assert adapter.count(Eq("account_id", "acme")) == 2
    assert adapter.count({"op": "must", "field": "account_id", "conds": ["beta"]}) == 1

    vector_hits = adapter.query(
        query_vector=[1.0, 0.0, 0.0, 0.0],
        filter=Eq("account_id", "acme"),
        limit=2,
        output_fields=["uri", "account_id", "active_count"],
    )
    assert len(vector_hits) >= 1
    assert all(item["account_id"] == "acme" for item in vector_hits)
    assert vector_hits[0]["uri"] == "viking://resources/acme/docs/a.md"

    id_hits = adapter.get_collection().search_by_id(
        index_name="default",
        id="doc_1",
        limit=2,
        output_fields=["uri", "account_id"],
    )
    assert id_hits.data[0].id == "doc_2"
    assert id_hits.data[0].fields["uri"] == "viking://resources/acme/docs/b.md"

    path_hits = adapter.query(
        filter=PathScope("uri", "viking://resources/acme/docs", depth=-1),
        limit=10,
        output_fields=["uri", "account_id"],
    )
    assert len(path_hits) == 2
    assert {item["uri"] for item in path_hits} == {
        "viking://resources/acme/docs/a.md",
        "viking://resources/acme/docs/b.md",
    }

    parent_hits = adapter.query(
        filter=PathScope("uri", "viking://resources/acme/docs", depth=1),
        limit=10,
        output_fields=["uri"],
    )
    assert len(parent_hits) == 2

    ordered = adapter.query(
        filter=Eq("account_id", "acme"),
        limit=10,
        order_by="active_count",
        order_desc=True,
        output_fields=["uri", "active_count"],
    )
    assert [item["active_count"] for item in ordered][:2] == [5, 3]

    meta = adapter.get_collection_info()
    assert meta["CollectionName"] == collection_name
    assert meta["Description"] == "Qdrant integration test collection"
    assert len(meta["Fields"]) == 10

    deleted = adapter.delete(filter=Eq("account_id", "beta"))
    assert deleted == 1
    assert adapter.count() == 2


def test_qdrant_hybrid_dense_sparse_query(qdrant_adapter):
    adapter, collection_name = qdrant_adapter
    schema = _build_schema(collection_name)

    created = adapter.create_collection(
        collection_name,
        schema,
        distance="cosine",
        sparse_weight=0.5,
        index_name="default",
    )
    assert created is True

    records = [
        {
            "id": str(uuid.uuid4()),
            "uri": "viking://resources/acme/hybrid/doc1.md",
            "vector": [1.0, 0.0, 0.0, 0.0],
            "sparse_vector": {"alpha": 1.0, "beta": 0.5},
            "created_at": "2026-05-29T12:10:00Z",
            "updated_at": "2026-05-29T12:10:00Z",
            "active_count": 10,
            "level": 2,
            "abstract": "alpha beta anchor",
            "account_id": "acme",
        },
        {
            "id": str(uuid.uuid4()),
            "uri": "viking://resources/acme/hybrid/doc2.md",
            "vector": [0.98, 0.02, 0.0, 0.0],
            "sparse_vector": {"gamma": 1.0},
            "created_at": "2026-05-29T12:11:00Z",
            "updated_at": "2026-05-29T12:11:00Z",
            "active_count": 8,
            "level": 2,
            "abstract": "dense only neighbor",
            "account_id": "acme",
        },
        {
            "id": str(uuid.uuid4()),
            "uri": "viking://resources/acme/hybrid/doc3.md",
            "vector": [0.0, 1.0, 0.0, 0.0],
            "sparse_vector": {"alpha": 1.0},
            "created_at": "2026-05-29T12:12:00Z",
            "updated_at": "2026-05-29T12:12:00Z",
            "active_count": 6,
            "level": 2,
            "abstract": "sparse only booster",
            "account_id": "acme",
        },
    ]
    adapter.upsert(records)

    dense_only = adapter.query(
        query_vector=[1.0, 0.0, 0.0, 0.0],
        filter=Eq("account_id", "acme"),
        limit=2,
        output_fields=["uri"],
    )
    dense_uris = [item["uri"] for item in dense_only]
    assert dense_uris == [
        "viking://resources/acme/hybrid/doc1.md",
        "viking://resources/acme/hybrid/doc2.md",
    ]

    hybrid_hits = adapter.query(
        query_vector=[1.0, 0.0, 0.0, 0.0],
        sparse_query_vector={"alpha": 1.0},
        filter=Eq("account_id", "acme"),
        limit=2,
        output_fields=["uri"],
    )
    hybrid_uris = [item["uri"] for item in hybrid_hits]

    # doc1 should remain top because it is strong on both dense and sparse.
    assert hybrid_uris[0] == "viking://resources/acme/hybrid/doc1.md"
    # doc3 should be pulled up by sparse matching into top2.
    assert "viking://resources/acme/hybrid/doc3.md" in hybrid_uris


def test_qdrant_contains_and_keywords_search(qdrant_adapter):
    adapter, collection_name = qdrant_adapter
    schema = _build_schema(collection_name)

    created = adapter.create_collection(
        collection_name,
        schema,
        distance="cosine",
        sparse_weight=0.0,
        index_name="default",
    )
    assert created is True

    records = [
        {
            "id": str(uuid.uuid4()),
            "uri": "viking://resources/acme/text/report.md",
            "vector": [1.0, 0.0, 0.0, 0.0],
            "created_at": "2026-05-29T12:20:00Z",
            "updated_at": "2026-05-29T12:20:00Z",
            "active_count": 2,
            "level": 2,
            "abstract": "quarterly report for product alpha",
            "account_id": "acme",
        },
        {
            "id": str(uuid.uuid4()),
            "uri": "viking://resources/acme/text/design.md",
            "vector": [0.8, 0.2, 0.0, 0.0],
            "created_at": "2026-05-29T12:21:00Z",
            "updated_at": "2026-05-29T12:21:00Z",
            "active_count": 1,
            "level": 2,
            "abstract": "design draft for project beta",
            "account_id": "acme",
        },
    ]
    ids = adapter.upsert(records)
    assert len(ids) == 2

    contains_hits = adapter.query(
        filter=Contains("abstract", "quarterly"),
        limit=10,
        output_fields=["uri", "abstract"],
    )
    assert len(contains_hits) == 1
    assert contains_hits[0]["uri"] == "viking://resources/acme/text/report.md"

    collection = adapter.get_collection()
    keyword_hits = collection.search_by_keywords(
        index_name="default",
        keywords=["quarterly"],
        limit=10,
        output_fields=["uri", "abstract"],
    )
    assert len(keyword_hits.data) >= 1
    assert keyword_hits.data[0].fields["uri"] == "viking://resources/acme/text/report.md"
