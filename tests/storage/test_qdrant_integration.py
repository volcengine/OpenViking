# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from openviking.storage.acl import ACL_CONTEXT_FIELDS
from openviking.storage.collection_schemas import (
    CollectionSchemas,
    _build_embedding_metadata,
    _encode_collection_description,
    init_context_collection,
)
from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.vectordb.collection.qdrant_collection import QdrantCollection
from openviking.storage.vectordb.collection.qdrant_rest import QdrantRestClient
from openviking.storage.vectordb.qdrant_utils import compile_qdrant_filter
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

QDRANT_URL = os.environ.get("QDRANT_URL")
requires_qdrant = pytest.mark.skipif(not QDRANT_URL, reason="QDRANT_URL not set")


@requires_qdrant
@pytest.mark.integration
def test_qdrant_phase_1_and_phase_2_round_trip() -> None:
    assert QDRANT_URL is not None
    suffix = uuid.uuid4().hex[:12]
    collection = QdrantCollection(
        client=QdrantRestClient(
            QDRANT_URL,
            api_key=os.environ.get("QDRANT_API_KEY"),
        ),
        collection_name=f"openviking_integration_{suffix}",
        metadata_collection_name=f"openviking_integration_{suffix}_meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
    )
    try:
        collection.create_remote_collection(
            {
                "CollectionName": collection._collection_name,
                "Fields": [
                    {"FieldName": "account_id", "FieldType": "string"},
                    {"FieldName": "search_tags", "FieldType": "list<string>"},
                ],
            }
        )
        collection.create_index(
            "default",
            {"ScalarIndex": ["account_id", "search_tags"]},
        )
        collection.upsert_data(
            [
                {
                    "id": "doc-a",
                    "account_id": "acct-a",
                    "search_tags": ["team=search", "env=prod"],
                    "uri": "viking://resources/wiki/a.md",
                    "vector": [1.0, 0.0],
                    "sparse_vector": {"qdrant": 1.0},
                },
                {
                    "id": "doc-b",
                    "account_id": "acct-b",
                    "search_tags": ["team=search"],
                    "uri": "viking://resources/wiki/b.md",
                    "vector": [0.0, 1.0],
                    "sparse_vector": {"other": 1.0},
                },
            ]
        )

        path_and_account = compile_qdrant_filter(
            And(
                [
                    Eq("account_id", "acct-a"),
                    PathScope("uri", "viking://resources/wiki", depth=-1),
                ]
            )
        )
        assert collection.aggregate_data("default", filters=path_and_account).agg == {"_total": 1}
        assert collection.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0],
            filters=path_and_account,
            limit=2,
        ).data[0].id == "doc-a"
        assert collection.search_by_vector(
            "default",
            sparse_vector={"qdrant": 1.0},
            filters=path_and_account,
            limit=2,
        ).data[0].id == "doc-a"
        assert collection.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0],
            sparse_vector={"qdrant": 1.0},
            filters=path_and_account,
            limit=2,
        ).data[0].id == "doc-a"
    finally:
        try:
            collection.drop()
        except Exception:
            pass


@requires_qdrant
@pytest.mark.integration
@pytest.mark.asyncio
async def test_qdrant_acl_migration_counts_all_accounts(monkeypatch) -> None:
    assert QDRANT_URL is not None
    suffix = uuid.uuid4().hex[:12]
    project = f"openviking_acl_{suffix}"
    collection_name = "context"
    physical_name = f"{project}__{collection_name}"
    metadata_name = f"{physical_name}__openviking_meta"
    client = QdrantRestClient(
        QDRANT_URL,
        api_key=os.environ.get("QDRANT_API_KEY"),
    )
    legacy_config = SimpleNamespace(
        storage=SimpleNamespace(
            vectordb=SimpleNamespace(
                name=collection_name,
                backend="qdrant",
                volcengine=SimpleNamespace(api_key=None),
            )
        ),
        embedding=SimpleNamespace(
            dimension=2,
            dense=SimpleNamespace(provider="local", model="integration", model_path=None),
            hybrid=None,
            sparse=None,
        ),
    )
    embedding_meta = _build_embedding_metadata(legacy_config)
    schema = CollectionSchemas.context_collection(
        collection_name,
        2,
        description=_encode_collection_description("Legacy context collection", embedding_meta),
    )
    schema["Fields"] = [
        field
        for field in schema["Fields"]
        if field["FieldName"] not in ACL_CONTEXT_FIELDS
    ]
    schema["ScalarIndex"] = [
        field for field in schema["ScalarIndex"] if field not in ACL_CONTEXT_FIELDS
    ]
    collection = QdrantCollection(
        client=client,
        collection_name=physical_name,
        metadata_collection_name=metadata_name,
        dense_vector_name="vector",
        sparse_vector_name="sparse_vector",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=False,
        sparse_weight=0.0,
    )
    backend = VikingVectorIndexBackend(
        config=VectorDBBackendConfig(
            backend="qdrant",
            url=QDRANT_URL,
            project=project,
            name=collection_name,
            dimension=2,
            qdrant={"url": QDRANT_URL, "api_key": os.environ.get("QDRANT_API_KEY")},
        )
    )
    warnings = []
    monkeypatch.setattr(
        "openviking.storage.collection_schemas.logger.warning",
        lambda message, *args: warnings.append(message % args if args else message),
    )
    try:
        collection.create_remote_collection(schema)
        collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "hnsw", "Distance": "Cosine"},
                "ScalarIndex": schema["ScalarIndex"],
            },
        )
        collection.upsert_data(
            [
                {
                    "id": "doc-a",
                    "account_id": "acct-a",
                    "uri": "viking://resources/a",
                    "vector": [1.0, 0.0],
                },
                {
                    "id": "doc-b",
                    "account_id": "acct-b",
                    "uri": "viking://resources/b",
                    "vector": [0.0, 1.0],
                },
            ]
        )
        monkeypatch.setattr(
            "openviking_cli.utils.config.get_openviking_config",
            lambda: legacy_config,
        )

        assert await init_context_collection(backend) is False
        migrated_meta = await backend.get_collection_meta()
        assert ACL_CONTEXT_FIELDS <= {
            field["FieldName"] for field in migrated_meta["Fields"]
        }
        assert ACL_CONTEXT_FIELDS <= set(migrated_meta["ScalarIndex"])
        assert await backend.count_unscoped() == 2
        assert await backend.count(ctx=SimpleNamespace(account_id="acct-a")) == 1
        assert any(
            "without backfilling records" in message and "2 vector(s)" in message
            for message in warnings
        )
    finally:
        await backend.close()
        try:
            collection.drop()
        except Exception:
            pass
