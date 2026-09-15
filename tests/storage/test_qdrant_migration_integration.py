# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.vectordb.collection.qdrant_rest import QdrantRestClient
from openviking.storage.vectordb.qdrant_sparse import stable_sparse_index
from openviking.storage.vectordb.qdrant_utils import (
    compile_qdrant_filter,
    to_qdrant_point_id,
)
from openviking.storage.vectordb_adapters.qdrant_adapter import QdrantCollectionAdapter
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig
from scripts.maintenance.qdrant_migrate import (
    QdrantMigration,
    _legacy_collection_metadata_id,
    _legacy_index_metadata_id,
)

QDRANT_URL = os.environ.get("QDRANT_URL")
requires_qdrant = pytest.mark.skipif(not QDRANT_URL, reason="QDRANT_URL not set")


def _result(response: Mapping[str, Any]) -> Any:
    return response.get("result", response)


def _scroll_all(client: QdrantRestClient, collection: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    offset: Any = None
    while True:
        body: dict[str, Any] = {
            "limit": 100,
            "with_payload": True,
            "with_vector": True,
        }
        if offset is not None:
            body["offset"] = offset
        result = _result(client.request("POST", f"/collections/{collection}/points/scroll", body))
        assert isinstance(result, Mapping)
        page = result.get("points")
        assert isinstance(page, list)
        points.extend(copy.deepcopy(point) for point in page if isinstance(point, dict))
        offset = result.get("next_page_offset")
        if not page or offset is None:
            return points


def _delete_collection(client: QdrantRestClient, collection: str) -> None:
    try:
        client.request("DELETE", f"/collections/{collection}", params={"timeout": 30})
    except Exception as exc:
        if getattr(exc, "status", None) != 404:
            raise


def _collection_params(info: Mapping[str, Any]) -> Mapping[str, Any]:
    config = info.get("config")
    if isinstance(config, Mapping) and isinstance(config.get("params"), Mapping):
        return config["params"]
    params = info.get("params")
    assert isinstance(params, Mapping)
    return params


def _seed_legacy_collections(
    client: QdrantRestClient,
    source: str,
    source_metadata: str,
    *,
    source_body: Mapping[str, Any],
    metadata_body: Mapping[str, Any],
    source_points: list[Mapping[str, Any]],
    metadata_points: list[Mapping[str, Any]],
) -> None:
    for collection, body in (
        (source, source_body),
        (source_metadata, metadata_body),
    ):
        client.request(
            "PUT",
            f"/collections/{collection}",
            dict(body),
            params={"wait": "true"},
        )
    client.request(
        "PUT",
        f"/collections/{source}/points",
        {"points": source_points},
        params={"wait": "true"},
    )
    client.request(
        "PUT",
        f"/collections/{source_metadata}/points",
        {"points": metadata_points},
        params={"wait": "true"},
    )


@requires_qdrant
@pytest.mark.integration
def test_cli_subprocess_phase_chain_round_trips_through_local_qdrant(tmp_path) -> None:
    assert QDRANT_URL is not None
    suffix = f"cli_{uuid.uuid4().hex[:12]}"
    source = f"ov_legacy_{suffix}__context"
    source_metadata = f"{source}__meta"
    target = f"ov_current_{suffix}__context"
    target_metadata = f"{target}__openviking_meta"
    client = QdrantRestClient(QDRANT_URL, timeout_seconds=30)
    collections = (source, source_metadata, target, target_metadata)
    script = (
        Path(__file__).parents[2] / "scripts" / "maintenance" / "qdrant_migrate.py"
    )
    fields = [
        {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
        {"FieldName": "uri", "FieldType": "path"},
        {"FieldName": "level", "FieldType": "int64"},
        {"FieldName": "context_type", "FieldType": "string"},
        {"FieldName": "owner_user_id", "FieldType": "string"},
        {"FieldName": "account_id", "FieldType": "string"},
        {"FieldName": "name", "FieldType": "string"},
        {"FieldName": "acl_enabled", "FieldType": "bool"},
        {"FieldName": "acl_direct_grants", "FieldType": "array"},
        {"FieldName": "acl_inherited_grants", "FieldType": "array"},
        {"FieldName": "vector", "FieldType": "vector", "Dim": 2},
    ]
    schema = {
        "CollectionName": "context",
        "Fields": fields,
        "ScalarIndex": ["uri", "level", "account_id", "owner_user_id"],
    }
    source_points = [
        {
            "id": 1,
            "vector": {"vector": [1.0, 0.0]},
            "payload": {
                "_openviking_original_id": 1,
                "uri": "/resources/cli.md",
                "level": 2,
                "context_type": "resource",
                "owner_user_id": "alice",
                "account_id": "acct",
                "name": "cli",
                "acl_enabled": False,
                "acl_direct_grants": [],
                "acl_inherited_grants": [],
            },
        }
    ]
    metadata_points = [
        {
            "id": _legacy_collection_metadata_id(source),
            "vector": {"meta": [0.0]},
            "payload": {
                "kind": "collection",
                "collection_key": source,
                "logical_collection_name": "context",
                "project_name": "legacy",
                "meta": schema,
            },
        },
        {
            "id": _legacy_index_metadata_id(source, "default"),
            "vector": {"meta": [0.0]},
            "payload": {
                "kind": "index",
                "collection_key": source,
                "index_name": "default",
                "meta": {
                    "IndexName": "default",
                    "VectorIndex": {
                        "IndexType": "hnsw",
                        "Distance": "Cosine",
                    },
                    "ScalarIndex": ["uri", "level", "account_id"],
                },
            },
        },
    ]
    try:
        _seed_legacy_collections(
            client,
            source,
            source_metadata,
            source_body={"vectors": {"vector": {"size": 2, "distance": "Cosine"}}},
            metadata_body={"vectors": {"meta": {"size": 1, "distance": "Dot"}}},
            source_points=source_points,
            metadata_points=metadata_points,
        )
        source_before = _scroll_all(client, source)
        metadata_before = _scroll_all(client, source_metadata)

        common = [
            sys.executable,
            str(script),
            "--url",
            QDRANT_URL,
            "--source-collection",
            source,
            "--target-collection",
            target,
            "--source-metadata-collection",
            source_metadata,
            "--target-metadata-collection",
            target_metadata,
            "--logical-collection",
            f"{suffix}/context",
            "--migration-id",
            suffix,
            "--batch-size",
            "1",
            "--timeout-seconds",
            "30",
        ]
        env = os.environ.copy()
        env.pop("QDRANT_API_KEY", None)

        def run_phase(*args: str) -> dict[str, Any]:
            completed = subprocess.run(
                [*common, *args],
                cwd=script.parents[2],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=90,
            )
            assert completed.returncode == 0, completed.stderr
            return json.loads(completed.stdout)

        plan_path = tmp_path / "cli-plan.json"
        preflight = run_phase("preflight")
        assert preflight["acl_incomplete_count"] == 1
        plan_path.write_text(json.dumps(preflight), encoding="utf-8")
        phase_args = (
            "--plan",
            str(plan_path),
            "--confirm",
            "--lock-held",
            "--allow-acl-fail-open",
        )
        assert run_phase("prepare", *phase_args)["migration_state"] == "building"
        assert run_phase("backfill", *phase_args)["backfill_complete"] is True
        assert run_phase("reconcile", *phase_args)["migration_state"] == "building"
        failed = subprocess.run(
            [
                *common,
                "verify",
                "--plan",
                str(plan_path),
                "--lock-held",
                "--allow-acl-fail-open",
            ],
            cwd=script.parents[2],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )
        assert failed.returncode != 0
        assert "confirm" in failed.stderr
        verified = run_phase("verify", *phase_args)
        assert verified["migration_state"] == "ready"
        assert verified["source_count"] == verified["target_count"] == 1
        assert _scroll_all(client, source) == source_before
        assert _scroll_all(client, source_metadata) == metadata_before
    finally:
        for collection in reversed(collections):
            _delete_collection(client, collection)


@requires_qdrant
@pytest.mark.integration
def test_pre3872_migration_round_trips_through_current_adapter() -> None:
    assert QDRANT_URL is not None
    suffix = uuid.uuid4().hex[:12]
    source = f"ov_legacy_{suffix}__context"
    source_metadata = f"{source}__meta"
    project = f"ov_current_{suffix}"
    target = f"{project}__context"
    target_metadata = f"{target}__openviking_meta"
    client = QdrantRestClient(
        QDRANT_URL,
        api_key=os.environ.get("QDRANT_API_KEY"),
        timeout_seconds=30,
    )
    migration = QdrantMigration(
        client=client,
        source_collection=source,
        target_collection=target,
        source_metadata_collection=source_metadata,
        target_metadata_collection=target_metadata,
        batch_size=1,
        sparse_map={111: "hello", 222: "world"},
        logical_collection=f"{project}/context",
        migration_id=suffix,
        timeout_seconds=30,
    )
    fields = [
        {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
        {"FieldName": "uri", "FieldType": "path"},
        {"FieldName": "level", "FieldType": "int64"},
        {"FieldName": "context_type", "FieldType": "string"},
        {"FieldName": "owner_user_id", "FieldType": "string"},
        {"FieldName": "account_id", "FieldType": "string"},
        {"FieldName": "name", "FieldType": "string"},
        {"FieldName": "vector", "FieldType": "vector", "Dim": 2},
        {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
    ]
    schema = {
        "CollectionName": "context",
        "Fields": fields,
        "ScalarIndex": ["uri", "level", "account_id", "owner_user_id"],
    }
    source_points = [
        {
            "id": 1,
            "vector": {
                "": [1.0, 0.0],
                "sparse_vector": {"indices": [111], "values": [0.8]},
            },
            "payload": {
                "_openviking_original_id": 1,
                "uri": "/resources/a.md",
                "level": 2,
                "context_type": "resource",
                "owner_user_id": "alice",
                "account_id": "acct-a",
                "name": "a.md",
                "acl_mode": "none",
                "acl_direct_grants": [],
                "acl_inherited_grants": [],
            },
        },
        {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "vector": {
                "": [0.0, 1.0],
                "sparse_vector": {"indices": [222], "values": [0.4]},
            },
            "payload": {
                "_openviking_original_id": "550e8400-e29b-41d4-a716-446655440000",
                "uri": "/resources/b.md",
                "level": 1,
                "context_type": "resource",
                "owner_user_id": "bob",
                "account_id": "acct-b",
                "name": "b.md",
                "acl_mode": "none",
                "acl_direct_grants": [],
                "acl_inherited_grants": [],
            },
        },
        {
            "id": to_qdrant_point_id("sparse-only"),
            "vector": {
                "sparse_vector": {"indices": [111, 222], "values": [0.2, 0.6]},
            },
            "payload": {
                "_openviking_original_id": "sparse-only",
                "uri": "/resources/sparse.md",
                "level": 0,
                "context_type": "resource",
                "owner_user_id": "alice",
                "account_id": "acct-a",
                "name": "sparse.md",
                "acl_mode": "none",
                "acl_direct_grants": [],
                "acl_inherited_grants": [],
            },
        },
    ]
    metadata_points = [
        {
            "id": _legacy_collection_metadata_id(source),
            "vector": [0.0],
            "payload": {
                "kind": "collection",
                "collection_key": source,
                "logical_collection_name": "context",
                "project_name": "legacy",
                "meta": schema,
            },
        },
        {
            "id": _legacy_index_metadata_id(source, "default"),
            "vector": [0.0],
            "payload": {
                "kind": "index",
                "collection_key": source,
                "index_name": "default",
                "meta": {
                    "IndexName": "default",
                    "VectorIndex": {
                        "IndexType": "hnsw_hybrid",
                        "Distance": "Cosine",
                    },
                    "ScalarIndex": ["uri", "level", "account_id", "owner_user_id"],
                    "SparseWeight": 0.5,
                },
            },
        },
    ]
    collection_names = (source, source_metadata, target, target_metadata)
    try:
        _seed_legacy_collections(
            client,
            source,
            source_metadata,
            source_body={
                "vectors": {"size": 2, "distance": "Cosine", "datatype": "float32"},
                "sparse_vectors": {
                    "sparse_vector": {
                        "modifier": "idf",
                        "index": {"datatype": "float16"},
                    }
                },
            },
            metadata_body={"vectors": {"size": 1, "distance": "Dot"}},
            source_points=source_points,
            metadata_points=metadata_points,
        )
        source_before = _scroll_all(client, source)
        metadata_before = _scroll_all(client, source_metadata)

        plan = migration.preflight()
        assert plan.source_count == 3
        assert plan.acl_incomplete_count == 0
        assert plan.dense_vector_name == "vector"
        assert plan.sparse_vector_name == "sparse_vector"
        assert plan.vector_dimension == 2
        assert plan.dense_datatype == "float32"
        assert plan.sparse_modifier == "idf"
        assert plan.sparse_datatype == "float16"
        assert plan.sparse_term_count == 2

        prepared = migration.prepare(
            confirm=True,
            plan=plan,
            lock_held=True,
        )
        assert prepared["migration_state"] == "building"
        backfilled = migration.backfill(
            confirm=True,
            plan=plan,
            lock_held=True,
        )
        assert backfilled["backfill_complete"] is True
        assert backfilled["target_count"] == 3
        reconciled = migration.reconcile(
            confirm=True,
            plan=plan,
            lock_held=True,
        )
        assert reconciled["migration_state"] == "building"
        verified = migration.verify(
            plan=plan,
            confirm=True,
            lock_held=True,
        )
        assert verified["migration_state"] == "ready"
        assert verified["source_count"] == 3
        assert verified["target_count"] == 3

        target_info = _result(client.request("GET", f"/collections/{target}"))
        assert isinstance(target_info, Mapping)
        target_params = _collection_params(target_info)
        vectors = target_params["vectors"]
        assert isinstance(vectors, Mapping)
        assert vectors["vector"]["size"] == 2
        assert str(vectors["vector"]["distance"]).lower() == "cosine"
        assert str(vectors["vector"].get("datatype")).lower() == "float32"
        sparse_vectors = target_params["sparse_vectors"]
        assert sparse_vectors["sparse_vector"]["modifier"] == "idf"
        assert sparse_vectors["sparse_vector"]["index"]["datatype"] == "float16"
        payload_schema = target_info["payload_schema"]
        assert isinstance(payload_schema, Mapping)
        assert payload_schema["account_id"]["data_type"] == "keyword"
        assert payload_schema["owner_user_id"]["data_type"] == "keyword"
        assert payload_schema["level"]["data_type"] == "integer"
        assert payload_schema["uri"]["data_type"] == "keyword"
        assert payload_schema["uri_depth"]["data_type"] == "integer"
        assert payload_schema["scope_roots"]["data_type"] == "keyword"

        target_points = _scroll_all(client, target)
        target_by_original = {
            point["payload"]["_openviking_original_id"]: point for point in target_points
        }
        assert target_by_original["1"]["vector"]["vector"] == [1.0, 0.0]
        assert target_by_original["550e8400-e29b-41d4-a716-446655440000"]["vector"][
            "vector"
        ] == [0.0, 1.0]
        assert "vector" not in target_by_original["sparse-only"]["vector"]
        sparse_vector = target_by_original["sparse-only"]["vector"]["sparse_vector"]
        assert isinstance(sparse_vector, Mapping)
        sparse_values = dict(
            zip(sparse_vector["indices"], sparse_vector["values"], strict=True)
        )
        assert sparse_values == {
            stable_sparse_index("hello"): 0.2,
            stable_sparse_index("world"): 0.6,
        }

        target_metadata_points = _scroll_all(client, target_metadata)
        sparse_terms = {
            point["payload"]["term"]
            for point in target_metadata_points
            if point["payload"].get("_openviking_sparse_term") is True
        }
        assert sparse_terms == {"hello", "world"}
        marker = next(
            point["payload"]
            for point in target_metadata_points
            if point["id"] == to_qdrant_point_id("openviking:metadata")
        )
        assert marker["setup_complete"] is True
        assert marker["migration_state"] == "ready"
        assert marker["migrator_version"] == "qdrant-blue-green-v1"
        assert marker["collection_name"] == target
        assert marker["metadata_collection_name"] == target_metadata
        assert marker["logical_collection"] == f"{project}/context"
        assert marker["source_collection"] == source
        assert marker["source_metadata_collection"] == source_metadata
        assert marker["source_fingerprint"] == plan.source_fingerprint
        assert marker["metadata_fingerprint"] == plan.metadata_fingerprint
        assert marker["sparse_map_fingerprint"] == plan.sparse_map_fingerprint
        assert marker["source_count"] == 3
        assert marker["target_count"] == 3
        assert marker["vector_dimension"] == 2
        assert marker["dense_vector_name"] == "vector"
        assert marker["sparse_vector_name"] == "sparse_vector"
        assert marker["dense_datatype"] == "float32"
        assert marker["sparse_modifier"] == "idf"
        assert marker["sparse_datatype"] == "float16"
        assert marker["transformed_source_fingerprint"] == verified[
            "transformed_source_fingerprint"
        ]
        assert marker["target_content_fingerprint"] == verified[
            "target_content_fingerprint"
        ]
        assert (
            verified["transformed_source_fingerprint"]
            == verified["target_content_fingerprint"]
        )

        assert _scroll_all(client, source) == source_before
        assert _scroll_all(client, source_metadata) == metadata_before

        adapter = QdrantCollectionAdapter.from_config(
            VectorDBBackendConfig(
                backend="qdrant",
                qdrant={
                    "url": QDRANT_URL,
                    "api_key": os.environ.get("QDRANT_API_KEY"),
                    "timeout_seconds": 30,
                    "data_collection_name": target,
                    "metadata_collection_name": target_metadata,
                },
                project=project,
                name="context",
                dimension=2,
                sparse_weight=0.5,
            )
        )
        collection = adapter.get_collection()
        fetched = collection.fetch_data([1, "550e8400-e29b-41d4-a716-446655440000"])
        assert {str(item.id) for item in fetched.items} == {
            "1",
            "550e8400-e29b-41d4-a716-446655440000",
        }
        assert collection.fetch_data(["sparse-only"]).items[0].id == "sparse-only"

        account_filter = compile_qdrant_filter(Eq("account_id", "acct-a"))
        path_filter = compile_qdrant_filter(
            And(
                [
                    Eq("account_id", "acct-a"),
                    PathScope("uri", "viking://resources", depth=-1),
                ]
            )
        )
        assert collection.aggregate_data("default", filters=account_filter).agg == {"_total": 2}
        dense = collection.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0],
            filters=path_filter,
            limit=3,
        )
        assert dense.data and dense.data[0].id == "1"
        sparse = collection.search_by_vector(
            "default",
            sparse_vector={"hello": 1.0},
            filters=path_filter,
            limit=3,
        )
        assert {str(item.id) for item in sparse.data} >= {"1", "sparse-only"}
        hybrid = collection.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0],
            sparse_vector={"hello": 1.0},
            filters=path_filter,
            limit=3,
        )
        assert hybrid.data and hybrid.data[0].id == "1"
        assert collection.search_by_id(
            "default",
            "sparse-only",
            limit=3,
            filters=path_filter,
        ).data

        target_only_id = f"task9-target-only-{suffix}"
        assert adapter.upsert(
            {
                "id": target_only_id,
                "uri": "viking://resources/task9-target-only.md",
                "vector": [0.3, 0.7],
                "account_id": "acct-target",
                "owner_user_id": "target",
            }
        ) == [target_only_id]
        assert adapter.get([target_only_id]) == [
            {
                "id": target_only_id,
                "uri": "viking://resources/task9-target-only.md",
                "account_id": "acct-target",
                "owner_user_id": "target",
            }
        ]
        assert adapter.delete(ids=[target_only_id]) == 1
        assert adapter.get([target_only_id]) == []
        assert _scroll_all(client, source) == source_before
        assert _scroll_all(client, source_metadata) == metadata_before
    finally:
        for collection_name in reversed(collection_names):
            _delete_collection(client, collection_name)
