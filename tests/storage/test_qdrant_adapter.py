# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timezone

import pytest

from openviking.storage.expr import And, Contains, Eq, PathScope, TimeRange
from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking.storage.vectordb_adapters.qdrant_adapter import QdrantCollectionAdapter
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def _build_config() -> VectorDBBackendConfig:
    return VectorDBBackendConfig.model_validate(
        {
            "backend": "qdrant",
            "project": "default",
            "name": "context",
            "index_name": "default",
            "distance_metric": "cosine",
            "qdrant": {
                "url": "http://qdrant:6333/",
                "api_key": "test-key",
                "timeout_seconds": 7,
                "dense_vector_name": "vector",
                "sparse_vector_name": "sparse_vector",
                "meta_collection_name": "__openviking_meta",
                "enable_text_index": True,
            },
        }
    )


def test_qdrant_backend_config_validation():
    config = _build_config()
    assert config.backend == "qdrant"
    assert config.qdrant is not None
    assert config.qdrant.url == "http://qdrant:6333"


def test_factory_creates_qdrant_adapter():
    adapter = create_collection_adapter(_build_config())
    assert isinstance(adapter, QdrantCollectionAdapter)
    assert adapter.mode == "qdrant"
    assert adapter.collection_name == "context"
    assert adapter.index_name == "default"
    assert adapter.physical_collection_name == "default__context"


def test_existing_physical_collection_without_metadata_is_rejected_and_closed(monkeypatch):
    class FakeQdrantCollection:
        def __init__(self) -> None:
            self.closed = False

        def collection_exists(self) -> bool:
            return True

        def has_openviking_metadata(self) -> bool:
            return False

        def close(self) -> None:
            self.closed = True

    candidate = FakeQdrantCollection()
    adapter = QdrantCollectionAdapter.from_config(_build_config())
    monkeypatch.setattr(adapter, "_new_qdrant_collection", lambda: candidate)

    with pytest.raises(RuntimeError, match="OpenViking metadata is missing"):
        adapter.collection_exists()

    assert candidate.closed is True


def test_augments_path_fields_on_write_and_hides_them_on_read():
    adapter = QdrantCollectionAdapter.from_config(_build_config())
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


def test_sanitizes_scalar_index_fields():
    adapter = QdrantCollectionAdapter.from_config(_build_config())
    result = adapter.sanitize_scalar_index_fields(
        scalar_index_fields=["uri", "account_id"],
        fields_meta=[],
    )
    assert result == [
        "uri",
        "account_id",
        "parent_uri",
        "scope_roots",
        "uri_depth",
        "name",
        "description",
        "abstract",
        "tags",
    ]


def test_compiles_filter_exprs():
    adapter = QdrantCollectionAdapter.from_config(_build_config())
    compiled = adapter.compile_filter(
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

    assert compiled == {
        "must": [
            {"key": "account_id", "match": {"value": "acme"}},
            {"key": "scope_roots", "match": {"value": "/resources/acme/docs"}},
            {
                "key": "updated_at",
                "range": {
                    "gte": "2026-05-01T00:00:00+00:00",
                    "lt": "2026-06-01T00:00:00+00:00",
                },
            },
            {"key": "abstract", "match": {"text": "quarterly report"}},
        ]
    }


def test_compiles_legacy_dict_filters():
    adapter = QdrantCollectionAdapter.from_config(_build_config())
    compiled = adapter.compile_filter(
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

    assert compiled == {
        "must": [
            {"key": "account_id", "match": {"value": "acme"}},
            {
                "key": "updated_at",
                "range": {
                    "gte": "2026-05-01T00:00:00Z",
                    "lt": "2026-06-01T00:00:00Z",
                },
            },
            {"key": "scope_roots", "match": {"value": "/resources/acme/docs"}},
        ]
    }
