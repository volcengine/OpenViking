# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import multiprocessing
import os
import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit

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
from openviking.storage.vectordb.qdrant_sparse import (
    parse_sparse_point,
    sparse_owner_point_id,
    stable_sparse_index,
)
from openviking.storage.vectordb.qdrant_utils import (
    compile_qdrant_filter,
    to_qdrant_point_id,
)
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig
from scripts.maintenance.qdrant_sparse_upgrade import SparseDictionaryUpgrade

QDRANT_URL = os.environ.get("QDRANT_URL")
requires_qdrant = pytest.mark.skipif(not QDRANT_URL, reason="QDRANT_URL not set")
requires_local_qdrant = pytest.mark.skipif(
    not QDRANT_URL or urlsplit(QDRANT_URL).hostname not in {"127.0.0.1", "localhost", "::1"},
    reason="a localhost QDRANT_URL is required for concurrency tests",
)


def _new_sparse_owner_collection(collection_name: str, metadata_name: str) -> QdrantCollection:
    assert QDRANT_URL is not None
    return QdrantCollection(
        client=QdrantRestClient(
            QDRANT_URL,
            api_key=os.environ.get("QDRANT_API_KEY"),
            timeout_seconds=30,
        ),
        collection_name=collection_name,
        metadata_collection_name=metadata_name,
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=1,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
    )


def _paused_sparse_owner_writer(
    qdrant_url: str,
    api_key: str | None,
    collection_name: str,
    metadata_name: str,
    ready,
    release,
    results,
) -> None:
    collection = QdrantCollection(
        client=QdrantRestClient(
            qdrant_url,
            api_key=api_key,
            timeout_seconds=30,
        ),
        collection_name=collection_name,
        metadata_collection_name=metadata_name,
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=1,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
    )
    original_persist = collection._persist_sparse_term

    def pause_after_precheck(term: str, index: int) -> None:
        ready.set()
        if not release.wait(30):
            raise RuntimeError("test release event was not set")
        original_persist(term, index)

    collection._persist_sparse_term = pause_after_precheck
    try:
        result = collection.encode_sparse_vector({"95303": 1.0})
    except BaseException as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    else:
        results.put(("ok", result))


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
                    {"FieldName": "level", "FieldType": "int64"},
                ],
            }
        )
        collection.create_index(
            "default",
            {"ScalarIndex": ["account_id", "search_tags", "level"]},
        )
        collection.upsert_data(
            [
                {
                    "id": "doc-a",
                    "account_id": "acct-a",
                    "level": 2,
                    "search_tags": ["team=search", "env=prod"],
                    "uri": "viking://resources/wiki/a.md",
                    "vector": [1.0, 0.0],
                    "sparse_vector": {"qdrant": 1.0},
                },
                {
                    "id": "doc-b",
                    "account_id": "acct-b",
                    "level": 1,
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
        assert (
            collection.search_by_vector(
                "default",
                dense_vector=[1.0, 0.0],
                filters=path_and_account,
                limit=2,
            )
            .data[0]
            .id
            == "doc-a"
        )
        scalar = collection.search_by_scalar(
            "default", "level", filters=path_and_account
        ).data[0]
        assert scalar.score == 2.0
        assert scalar.fields == {
            "id": "doc-a",
            "account_id": "acct-a",
            "level": 2,
            "search_tags": ["team=search", "env=prod"],
            "uri": "/resources/wiki/a.md",
        }
        projected = collection.search_by_scalar(
            "default", "level", filters=path_and_account, output_fields=["account_id"]
        ).data[0]
        assert projected.score == 2.0
        assert projected.fields == {"id": "doc-a", "account_id": "acct-a"}
        assert (
            collection.search_by_vector(
                "default",
                sparse_vector={"qdrant": 1.0},
                filters=path_and_account,
                limit=2,
            )
            .data[0]
            .id
            == "doc-a"
        )
        assert (
            collection.search_by_vector(
                "default",
                dense_vector=[1.0, 0.0],
                sparse_vector={"qdrant": 1.0},
                filters=path_and_account,
                limit=2,
            )
            .data[0]
            .id
            == "doc-a"
        )
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
        field for field in schema["Fields"] if field["FieldName"] not in ACL_CONTEXT_FIELDS
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
        assert ACL_CONTEXT_FIELDS <= {field["FieldName"] for field in migrated_meta["Fields"]}
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


@requires_local_qdrant
@pytest.mark.integration
def test_qdrant_sparse_owner_rejects_stale_colliding_writer() -> None:
    """A stale pre-check must not overwrite the owner claimed by another writer."""
    assert QDRANT_URL is not None
    suffix = uuid.uuid4().hex[:12]
    collection = _new_sparse_owner_collection(
        f"openviking_sparse_race_{suffix}",
        f"openviking_sparse_race_{suffix}_meta",
    )
    collection.create_remote_collection(
        {"CollectionName": collection._collection_name, "Fields": []}
    )

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    results = context.Queue()
    writer = context.Process(
        target=_paused_sparse_owner_writer,
        args=(
            QDRANT_URL,
            os.environ.get("QDRANT_API_KEY"),
            collection._collection_name,
            collection._metadata_collection_name,
            ready,
            release,
            results,
        ),
    )
    try:
        writer.start()
        assert ready.wait(30), "the stale writer did not reach its pre-check barrier"

        expected_index = stable_sparse_index("69235")
        encoded = collection.encode_sparse_vector({"69235": 1.0})
        assert encoded["indices"] == [expected_index]

        release.set()
        writer.join(30)
        assert writer.exitcode == 0
        result = results.get(timeout=5)
        assert result[0] == "error", result
        assert result[1] == "ValueError", result
        assert "sparse term" in result[2]
        assert "69235" in result[2]
        assert "95303" in result[2]

        owner_id = sparse_owner_point_id(expected_index)
        owner_points = collection._retrieve_points(
            collection._metadata_collection_name,
            [owner_id],
            with_vectors=False,
        )
        assert len(owner_points) == 1
        assert parse_sparse_point(owner_points[0]) == ("69235", expected_index)
        assert collection._resolve_sparse_index(expected_index) == "69235"
        assert collection.encode_sparse_vector({"69235": 1.0})["indices"] == [expected_index]
    finally:
        release.set()
        if writer.is_alive():
            writer.terminate()
        writer.join(30)
        try:
            collection.drop()
        except Exception:
            pass


@requires_local_qdrant
@pytest.mark.integration
def test_qdrant_sparse_upgrade_preserves_legacy_data_and_is_idempotent() -> None:
    suffix = uuid.uuid4().hex[:12]
    collection = _new_sparse_owner_collection(
        f"openviking_sparse_upgrade_{suffix}",
        f"openviking_sparse_upgrade_{suffix}_meta",
    )
    try:
        collection.create_remote_collection(
            {"CollectionName": collection._collection_name, "Fields": []}
        )
        # More than two converter batches, with immutable legacy aliases.
        terms = [f"upgrade-term-{index}" for index in range(205)]
        aliases = [
            {
                "id": to_qdrant_point_id(f"openviking:sparse:{term}"),
                "vector": {"meta": [0.0]},
                "payload": {
                    "_openviking_sparse_term": True,
                    "term": term,
                    "index": stable_sparse_index(term),
                },
            }
            for term in terms
        ]
        collection._client.request(
            "PUT",
            collection._path(collection._metadata_collection_name, "/points"),
            {"points": aliases},
            params={"wait": "true", "ordering": "strong"},
        )
        collection.upsert_data(
            [{"id": "legacy-doc", "vector": [1.0], "sparse_vector": {terms[0]: 1.0}}]
        )
        raw_before = collection._retrieve_points(
            collection._collection_name,
            [to_qdrant_point_id("legacy-doc")],
            with_vectors=True,
        )
        operation = SparseDictionaryUpgrade(
            client=collection._client,
            data_collection=collection._collection_name,
            metadata_collection=collection._metadata_collection_name,
        )
        # Existing-term encoding above must not opportunistically write owners.
        assert operation.preflight().missing_owner_count == len(terms)
        for _ in range(2):
            result = operation.convert(
                confirm=True, lock_held=True, barrier_held=True, old_writers_stopped=True
            )
            assert result["term_count"] == result["owner_count"] == len(terms)
            assert result["missing_owner_count"] == 0
        raw_after = collection._retrieve_points(
            collection._collection_name,
            [to_qdrant_point_id("legacy-doc")],
            with_vectors=True,
        )
        assert raw_after == raw_before
        assert collection._decode_sparse_vector(raw_after[0]["vector"]["sparse"]) == {terms[0]: 1.0}
        preserved = collection._retrieve_points(
            collection._metadata_collection_name,
            [point["id"] for point in aliases],
            with_vectors=True,
        )
        assert sorted(preserved, key=lambda point: point["id"]) == sorted(
            aliases, key=lambda point: point["id"]
        )
    finally:
        collection.drop()
