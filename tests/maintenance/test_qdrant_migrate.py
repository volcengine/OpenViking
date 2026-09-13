from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest
import yaml

from openviking.storage.vectordb.qdrant_sparse import (
    sparse_owner_point_id,
    stable_sparse_index,
)
from openviking.storage.vectordb.qdrant_utils import to_qdrant_point_id
from scripts.maintenance.qdrant_migrate import (
    DeploymentHooks,
    MigrationError,
    QdrantMigration,
    SparseMigrationError,
    _fingerprint_values,
    _legacy_collection_metadata_id,
    _legacy_index_metadata_id,
    _load_plan,
    _parser,
    _ScanManifest,
    _SparseDictionaryManifest,
    main,
)

REQUIRED_QDRANT_TESTS = (
    "tests/maintenance/test_qdrant_migrate.py",
    "tests/storage/test_qdrant_adapter.py",
    "tests/storage/test_qdrant_sparse.py",
    "tests/maintenance/test_qdrant_sparse_upgrade.py",
    "tests/storage/test_qdrant_migration_integration.py",
    "tests/storage/test_qdrant_integration.py",
    "tests/storage/test_collection_schemas.py",
)
REQUIRED_QDRANT_SHARED_DEPENDENCIES = (
    "openviking/storage/acl.py",
    "openviking/storage/collection_schemas.py",
    "openviking/storage/viking_vector_index_backend.py",
    "openviking/storage/vectordb_adapters/base.py",
    "openviking/storage/vectordb_adapters/factory.py",
    "openviking/storage/expr.py",
)
DEFAULT_CUVS_TESTS = (
    "tests/vectordb/test_cuvs_config.py",
    "tests/vectordb/test_cuvs_index.py",
    "tests/vectordb/test_cuvs_collection.py",
    "tests/vectordb/test_str_to_uint64.py",
)


class FakeQdrant:
    """Small in-memory REST double that exercises the migration HTTP contract."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, object]] = {}
        self.requests: list[tuple[str, str, dict[str, object] | None]] = []
        self.request_params: list[dict[str, object] | None] = []
        self.count_overrides: dict[str, int] = {}

    def add_collection(
        self,
        name: str,
        *,
        vectors: dict[str, object] | object,
        sparse_vectors: dict[str, object] | None = None,
        points: list[dict[str, object]] | None = None,
    ) -> None:
        self.collections[name] = {
            "config": {
                "params": {
                    "vectors": vectors,
                    **({"sparse_vectors": sparse_vectors} if sparse_vectors is not None else {}),
                }
            },
            "points": {str(point["id"]): copy.deepcopy(point) for point in points or []},
            "indexes": {},
            "payload_schema": {},
        }

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        *,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.requests.append((method, path, copy.deepcopy(body)))
        self.request_params.append(copy.deepcopy(params))
        if method == "GET" and path == "/":
            return {"title": "qdrant", "version": "1.19.1"}
        parts = [unquote(part) for part in urlsplit(path).path.split("/") if part]
        if parts[:1] != ["collections"] or len(parts) < 2:
            raise AssertionError(path)
        name = parts[1]
        collection = self.collections.get(name)
        suffix = parts[2:]

        if method == "GET" and not suffix:
            if collection is None:
                raise _FakeHttpError(404)
            config = copy.deepcopy(collection["config"])
            config["points_count"] = len(collection["points"])
            config["payload_schema"] = copy.deepcopy(collection["payload_schema"])
            config["status"] = "green"
            config["optimizer_status"] = "ok"
            config["update_queue"] = 0
            return {"result": config}

        if method == "PUT" and not suffix:
            if collection is not None:
                raise _FakeHttpError(409)
            self.collections[name] = {
                "config": copy.deepcopy(body or {}),
                "points": {},
                "indexes": {},
                "payload_schema": {},
            }
            return {"result": True}

        if collection is None:
            raise _FakeHttpError(404)

        points: dict[str, dict[str, object]] = collection["points"]
        def filtered_points(request: dict[str, object]) -> list[dict[str, object]]:
            selected = list(points.values())
            point_filter = request.get("filter")
            if not isinstance(point_filter, dict):
                return selected
            must = point_filter.get("must")
            if not isinstance(must, list):
                return selected
            for clause in must:
                if not isinstance(clause, dict) or clause.get("key") != "collection_key":
                    continue
                match = clause.get("match")
                expected = match.get("value") if isinstance(match, dict) else None
                selected = [
                    point
                    for point in selected
                    if isinstance(point.get("payload"), dict)
                    and point["payload"].get("collection_key") == expected
                ]
            return selected
        if method == "DELETE" and not suffix:
            del self.collections[name]
            return {"result": True}

        if suffix == ["points"] and method == "PUT":
            request = body or {}
            update_filter = request.get("update_filter")
            excluded_ids: set[str] = set()
            if update_filter is not None:
                assert update_filter == {
                    "must_not": [
                        {
                            "has_id": [
                                str(point["id"])
                                for point in request.get("points", [])
                            ]
                        }
                    ]
                }
                excluded_ids = {
                    str(point_id)
                    for point_id in update_filter["must_not"][0]["has_id"]
                }
            for point in request.get("points", []):
                # Qdrant's update_filter is evaluated for each point in a
                # batch. An existing owner is therefore skipped, while a
                # concurrently absent owner is inserted.
                if str(point["id"]) in excluded_ids and str(point["id"]) in points:
                    continue
                points[str(point["id"])] = copy.deepcopy(point)
            return {"result": {"status": "completed"}}

        if suffix == ["points"] and method == "POST":
            result = [
                copy.deepcopy(points[str(point_id)])
                for point_id in (body or {}).get("ids", [])
                if str(point_id) in points
            ]
            return {"result": result}

        if suffix == ["points", "delete"] and method == "POST":
            selector = (body or {}).get("points")
            if not isinstance(selector, list):
                raise AssertionError(body)
            for point_id in selector:
                points.pop(str(point_id), None)
            return {"result": {"status": "completed"}}

        if suffix == ["points", "count"] and method == "POST":
            return {
                "result": {
                    "count": self.count_overrides.get(
                        name,
                        len(filtered_points(body or {})),
                    )
                }
            }

        if suffix == ["points", "scroll"] and method == "POST":
            request = body or {}
            offset = int(request.get("offset") or 0)
            limit = int(request.get("limit") or 1)
            ordered = filtered_points(request)
            page = copy.deepcopy(ordered[offset : offset + limit])
            result: dict[str, object] = {"points": page}
            if offset + len(page) < len(ordered):
                result["next_page_offset"] = offset + len(page)
            return {"result": result}

        if suffix == ["index"] and method == "PUT":
            field_name = str((body or {}).get("field_name"))
            collection["indexes"][field_name] = copy.deepcopy(body)
            collection["payload_schema"][field_name] = {
                "data_type": (body or {}).get("field_schema")
            }
            return {"result": {"status": "completed"}}

        raise AssertionError((method, path, body))


class _FakeHttpError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class _RecordingClient:
    timeout_seconds = 37.0

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, object] | None, dict[str, object] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        *,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.requests.append((method, path, copy.deepcopy(body), copy.deepcopy(params)))
        if (
            path.count("/") >= 3
            and (
                path.endswith("/points")
                or path.endswith("/points/delete")
                or "/index" in path
            )
        ):
            return {"result": {"status": "completed"}}
        return {"result": True}


class _ReadinessClient:
    def __init__(self, responses: list[dict[str, object]], *, timeout_seconds: float = 1.0):
        self.timeout_seconds = timeout_seconds
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict[str, object] | None, dict[str, object] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        *,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.requests.append((method, path, copy.deepcopy(body), copy.deepcopy(params)))
        if not self.responses:
            raise AssertionError("readiness response sequence exhausted")
        return self.responses.pop(0)


def test_migration_target_mutations_use_strong_ordering_and_timeout() -> None:
    client = _RecordingClient()
    migration = QdrantMigration(
        client=client,
        source_collection="legacy",
        target_collection="index-data",
        source_metadata_collection="legacy__meta",
        target_metadata_collection="index-data__meta",
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=37,
    )

    migration._request(
        "PUT",
        migration._path(migration.target_collection, "/points"),
        {"points": [{"id": "one"}]},
        mutation=True,
    )
    migration._request(
        "POST",
        migration._path(migration.target_collection, "/points/delete"),
        {"points": ["one"]},
        mutation=True,
    )
    migration._request(
        "PUT",
        migration._path(migration.target_collection),
        {"vectors": {"vector": {"size": 2, "distance": "Cosine"}}},
        mutation=True,
    )
    migration._request(
        "PUT",
        migration._path(migration.target_collection, "/index"),
        {"field_name": "account_id", "field_schema": "keyword"},
        mutation=True,
    )
    migration._request(
        "DELETE",
        migration._path(migration.target_collection),
        mutation=True,
    )

    point_params = client.requests[0][3]
    delete_params = client.requests[1][3]
    collection_params = client.requests[2][3]
    index_params = client.requests[3][3]
    collection_delete_params = client.requests[4][3]
    assert point_params == {"wait": "true", "ordering": "strong"}
    assert delete_params == {"wait": "true", "ordering": "strong"}
    assert collection_params == {"timeout": 37}
    assert index_params == {"wait": "true", "timeout": 37}
    assert collection_delete_params == {"timeout": 37}


def test_migration_collection_named_points_keeps_collection_contract() -> None:
    client = _RecordingClient()
    migration = QdrantMigration(
        client=client,
        source_collection="legacy",
        target_collection="points",
        source_metadata_collection="legacy__meta",
        target_metadata_collection="points__meta",
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=37,
    )

    migration._request(
        "PUT",
        migration._path(migration.target_collection),
        {"vectors": {"vector": {"size": 2, "distance": "Cosine"}}},
        mutation=True,
    )
    migration._request(
        "PUT",
        migration._path(migration.target_collection, "/points"),
        {"points": [{"id": "one"}]},
        mutation=True,
    )

    assert client.requests[0][3] == {"timeout": 37}
    assert client.requests[1][3] == {"wait": "true", "ordering": "strong"}


def test_migration_point_lookup_rejects_malformed_points() -> None:
    client = _RecordingClient()
    client.request = lambda *args, **kwargs: {"result": ["not-a-point"]}  # type: ignore[method-assign]
    migration = QdrantMigration(
        client=client,
        source_collection="legacy",
        target_collection="current",
        source_metadata_collection="legacy__meta",
        target_metadata_collection="current__meta",
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=37,
    )

    with pytest.raises(MigrationError, match="invalid Qdrant point lookup point"):
        migration._retrieve("legacy", ["id"], with_vectors=False)


def test_migration_point_mutation_rejects_acknowledged_result() -> None:
    client = _RecordingClient()
    client.request = lambda *args, **kwargs: {"result": {"status": "acknowledged"}}  # type: ignore[method-assign]
    migration = QdrantMigration(
        client=client,
        source_collection="legacy",
        target_collection="current",
        source_metadata_collection="legacy__meta",
        target_metadata_collection="current__meta",
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=37,
    )

    with pytest.raises(MigrationError, match="did not complete"):
        migration._request(
            "PUT",
            migration._path(migration.target_collection, "/points"),
            {"points": [{"id": "one"}]},
            mutation=True,
        )


@pytest.mark.parametrize("response", [{"result": False}, {}])
def test_collection_mutations_require_literal_true_receipts(
    response: dict[str, object],
) -> None:
    client = _RecordingClient()
    client.request = lambda *args, **kwargs: response  # type: ignore[method-assign]
    migration = QdrantMigration(
        client=client,
        source_collection="legacy",
        target_collection="current",
        source_metadata_collection="legacy__meta",
        target_metadata_collection="current__meta",
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=37,
    )

    with pytest.raises(MigrationError, match="did not complete"):
        migration._create_collection(
            migration.target_collection,
            {"vectors": {"vector": {"size": 2, "distance": "Cosine"}}},
        )
    with pytest.raises(MigrationError, match="did not complete"):
        migration._delete_collection(
            migration.target_collection,
            allow_unmarked=True,
        )


def test_migration_rejects_qdrant_versions_below_sparse_owner_floor() -> None:
    client = _ReadinessClient([{"title": "qdrant", "version": "1.15.9"}])
    migration = QdrantMigration(
        client=client,
        source_collection="legacy",
        target_collection="current",
        source_metadata_collection="legacy__meta",
        target_metadata_collection="current__meta",
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=1.0,
    )

    with pytest.raises(MigrationError, match="minimum 1.16.0"):
        migration._assert_strong_ordering_support()


@pytest.mark.parametrize("version", ["1.16.0-rc1", "1.16.0-rc1+build.1"])
def test_migration_rejects_qdrant_prerelease_versions(version: str) -> None:
    client = _ReadinessClient([{"title": "qdrant", "version": version}])
    migration = QdrantMigration(
        client=client,
        source_collection="legacy",
        target_collection="current",
        source_metadata_collection="legacy__meta",
        target_metadata_collection="current__meta",
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=1.0,
    )

    with pytest.raises(MigrationError, match="unparseable"):
        migration._assert_strong_ordering_support()


def test_migration_accepts_qdrant_build_metadata_on_stable_version() -> None:
    client = _ReadinessClient([{"title": "qdrant", "version": "1.16.0+build.1"}])
    migration = QdrantMigration(
        client=client,
        source_collection="legacy",
        target_collection="current",
        source_metadata_collection="legacy__meta",
        target_metadata_collection="current__meta",
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=1.0,
    )

    migration._assert_strong_ordering_support()


def test_migration_readiness_polls_until_green_and_indexes_visible() -> None:
    client = _ReadinessClient(
        [
            {"result": {"status": "yellow", "optimizer_status": "ok"}},
            {
                "result": {
                    "status": "green",
                    "optimizer_status": "ok",
                    "payload_schema": {},
                }
            },
            {
                "result": {
                    "status": "green",
                    "optimizer_status": "ok",
                    "payload_schema": {"account_id": {"data_type": "keyword"}},
                }
            },
        ],
        timeout_seconds=0.2,
    )
    migration = QdrantMigration(
        client=client,
        source_collection="legacy",
        target_collection="current",
        source_metadata_collection="legacy__meta",
        target_metadata_collection="current__meta",
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=0.2,
    )

    migration._wait_collection_ready("current", payload_fields={"account_id"})

    assert len(client.requests) == 3


def test_migration_readiness_rejects_red_collection() -> None:
    client = _ReadinessClient(
        [{"result": {"status": "red", "optimizer_status": "ok"}}]
    )
    migration = QdrantMigration(
        client=client,
        source_collection="legacy",
        target_collection="current",
        source_metadata_collection="legacy__meta",
        target_metadata_collection="current__meta",
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=1.0,
    )

    with pytest.raises(MigrationError, match="not ready"):
        migration._wait_collection_ready("current")


def test_migration_readiness_times_out_while_collection_is_yellow() -> None:
    client = _ReadinessClient(
        [{"result": {"status": "yellow", "optimizer_status": "ok"}}] * 100,
        timeout_seconds=0.01,
    )
    migration = QdrantMigration(
        client=client,
        source_collection="legacy",
        target_collection="current",
        source_metadata_collection="legacy__meta",
        target_metadata_collection="current__meta",
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=0.01,
    )

    with pytest.raises(MigrationError, match="did not become ready"):
        migration._wait_collection_ready("current")


def _point(
    point_id: object,
    original_id: object,
    *,
    uri: str = "/resources/a.md",
    level: int = 2,
    context_type: str = "resource",
    owner_user_id: str = "alice",
    account_id: str = "acct",
    vector: list[float] | None = None,
    sparse: dict[str, object] | None = None,
) -> dict[str, object]:
    vectors: dict[str, object] = {"vector": vector or [1.0, 0.0]}
    if sparse is not None:
        vectors["sparse_vector"] = sparse
    return {
        "id": point_id,
        "vector": vectors,
        "payload": {
            "_openviking_original_id": original_id,
            "uri": uri,
            "level": level,
            "context_type": context_type,
            "owner_user_id": owner_user_id,
            "account_id": account_id,
            "name": "doc",
        },
    }


def _legacy_fixture(
    *,
    sparse: bool = True,
    sparse_datatype: str | None = None,
) -> FakeQdrant:
    qdrant = FakeQdrant()
    vectors: dict[str, object] = {"vector": {"size": 2, "distance": "Cosine"}}
    sparse_vectors = (
        {
            "sparse_vector": (
                {"index": {"datatype": sparse_datatype}}
                if sparse_datatype is not None
                else {}
            )
        }
        if sparse
        else None
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
    ]
    if sparse:
        fields.append({"FieldName": "sparse_vector", "FieldType": "sparse_vector"})
    source_points = [
        _point(
            1,
            1,
            sparse=(
                {"indices": [111], "values": [0.7]}
                if sparse
                else None
            ),
        ),
        _point(
            "550e8400-e29b-41d4-a716-446655440000",
            "550e8400-e29b-41d4-a716-446655440000",
            uri="/resources/b.md",
            vector=[0.0, 1.0],
            sparse=(
                {"indices": [222], "values": [0.3]}
                if sparse
                else None
            ),
        ),
    ]
    qdrant.add_collection(
        "legacy__context",
        vectors=vectors,
        sparse_vectors=sparse_vectors,
        points=source_points,
    )
    qdrant.add_collection(
        "legacy__context__openviking_meta",
        vectors={"size": 1, "distance": "Cosine"},
        points=[
            {
                "id": _legacy_collection_metadata_id("legacy__context"),
                "vector": [0.0],
                "payload": {
                    "kind": "collection",
                    "collection_key": "legacy__context",
                    "logical_collection_name": "context",
                    "project_name": "legacy",
                    "meta": {
                        "CollectionName": "context",
                        "Fields": fields,
                        "ScalarIndex": ["uri", "level", "context_type", "owner_user_id", "account_id"],
                    },
                },
            },
            {
                "id": _legacy_index_metadata_id("legacy__context", "default"),
                "vector": [0.0],
                "payload": {
                    "kind": "index",
                    "collection_key": "legacy__context",
                    "index_name": "default",
                    "meta": {
                        "IndexName": "default",
                        "VectorIndex": {
                            "IndexType": "hnsw_hybrid",
                            "Distance": "Cosine",
                        },
                        "ScalarIndex": ["uri", "level", "account_id"],
                        "SparseWeight": 0.5,
                    },
                },
            },
        ],
    )
    return qdrant


def _migration(qdrant: FakeQdrant, **kwargs: object) -> QdrantMigration:
    kwargs.setdefault("logical_collection", "legacy/context")
    kwargs.setdefault("migration_id", "mig-1")
    return QdrantMigration(
        client=qdrant,
        source_collection="legacy__context",
        target_collection="current__context",
        source_metadata_collection="legacy__context__openviking_meta",
        target_metadata_collection="current__context__openviking_meta",
        **kwargs,
    )


def _add_current_marker(
    qdrant: FakeQdrant,
    *,
    migration_id: str = "mig-1",
    migration_state: str = "building",
) -> None:
    migration = _migration(qdrant, migration_id=migration_id)
    plan = migration.preflight()
    metadata = migration._legacy_metadata()
    layout = migration._layout(
        migration._collection_info(migration.source_collection),
    )
    marker = migration._marker_payload(
        layout=layout,
        metadata=metadata,
        sparse_weight=plan.sparse_weight,
        source_fingerprint=plan.source_fingerprint,
        metadata_fingerprint=plan.metadata_fingerprint,
        sparse_map_fingerprint=plan.sparse_map_fingerprint,
        setup_complete=migration_state in {"ready", "cutting_over", "active", "retained"},
        acl_incomplete_count=plan.acl_incomplete_count,
        sparse_term_count=plan.sparse_term_count,
        sparse_term_fingerprint=plan.sparse_term_fingerprint,
        migration_state=migration_state,
        source_count=plan.source_count,
        target_count=0,
    )
    marker["migration_id"] = migration_id
    qdrant.add_collection(
        migration.target_metadata_collection,
        vectors={"meta": {"size": 1, "distance": "Dot"}},
        points=[
            {
                "id": to_qdrant_point_id("openviking:metadata"),
                "vector": {"meta": [0.0]},
                "payload": marker,
            }
        ],
    )


def _mark_current_target_building(
    qdrant: FakeQdrant,
    migration: QdrantMigration,
) -> None:
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["migration_state"] = "building"
    marker["setup_complete"] = False
    # Model an explicit ready-to-building reconciliation window, not an
    # interrupted apply whose durable backfill progress must be preserved.
    marker["last_source_cursor"] = None
    marker["backfill_complete"] = False


def test_preflight_plan_is_compact_and_binds_identity() -> None:
    plan = _migration(
        _legacy_fixture(sparse=False),
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=23,
    ).preflight()

    value = plan.to_dict()

    assert value["logical_collection"] == "legacy/context"
    assert value["migration_id"] == "mig-1"
    assert value["timeout_seconds"] == 23.0
    assert value["target_absent"] is True
    assert value["target_state"] is None
    assert "id_map" not in value
    assert "existing_target_ids" not in value
    assert "sparse_terms" not in value
    assert "vectors" not in value
    assert "payloads" not in value
    assert "url" not in value
    assert "api_key" not in value


def test_fingerprint_manifest_digest_does_not_materialize_ordered_rows(
    monkeypatch,
) -> None:
    class NoSort:
        def __iter__(self):
            yield from ("a", "b", "c")

    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate.sorted",
        lambda _values: (_ for _ in ()).throw(AssertionError("sorted called")),
        raising=False,
    )
    assert _fingerprint_values(NoSort(), ordered=True)


def test_cli_help_describes_online_barrier_split() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[2] / "scripts" / "maintenance" / "qdrant_migrate.py"),
            "--help",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    help_text = completed.stdout
    assert "offline" in help_text
    assert "barrier-held" in help_text


def test_foreign_target_marker_is_rejected() -> None:
    qdrant = _legacy_fixture(sparse=False)
    _add_current_marker(qdrant, migration_id="other")

    with pytest.raises(MigrationError, match="migration ID"):
        _migration(qdrant, migration_id="mig-1").preflight()


def test_state_transition_preserves_setup_gate() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _add_current_marker(qdrant)

    assert migration._transition("building")["setup_complete"] is False
    assert migration._transition("ready")["setup_complete"] is True


def test_state_transition_rejects_tampered_marker_layout() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _add_current_marker(qdrant)
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["vector_dim"] = 999

    with pytest.raises(MigrationError, match="dimension"):
        migration._transition("ready")


def test_preflight_rejects_complete_marker_without_target_collection() -> None:
    qdrant = _legacy_fixture(sparse=False)
    _add_current_marker(qdrant, migration_state="ready")

    with pytest.raises(MigrationError, match="target collection"):
        _migration(qdrant).preflight()


def test_cli_phase_arguments_require_identity_and_timeout() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                "--url",
                "http://qdrant.invalid",
                "--source-collection",
                "legacy__context",
                "--target-collection",
                "current__context",
                "preflight",
            ]
        )

    args = _parser().parse_args(
        [
            "--url",
            "http://qdrant.invalid",
            "--source-collection",
            "legacy__context",
            "--target-collection",
            "current__context",
            "--logical-collection",
            "legacy/context",
            "--migration-id",
            "mig-1",
            "--timeout-seconds",
            "23",
            "preflight",
        ]
    )
    assert args.logical_collection == "legacy/context"
    assert args.migration_id == "mig-1"
    assert args.timeout_seconds == 23.0

    prepare_args = _parser().parse_args(
        [
            "--url",
            "http://qdrant.invalid",
            "--source-collection",
            "legacy__context",
            "--target-collection",
            "current__context",
            "--logical-collection",
            "legacy/context",
            "--migration-id",
            "mig-1",
            "--timeout-seconds",
            "23",
            "prepare",
            "--plan",
            "plan.json",
            "--confirm",
            "--lock-held",
        ]
    )
    assert prepare_args.command == "prepare"
    assert prepare_args.plan == "plan.json"
    assert prepare_args.lock_held is True


def test_cli_apply_requires_a_reviewed_plan() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                "--url",
                "http://qdrant.invalid",
                "--source-collection",
                "legacy__context",
                "--target-collection",
                "current__context",
                "--logical-collection",
                "legacy/context",
                "--migration-id",
                "mig-1",
                "--timeout-seconds",
                "23",
                "apply",
                "--confirm",
            ]
        )


def test_prepare_creates_both_collections_before_marker_write() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()

    result = migration.prepare(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    marker_path = migration._path(migration.target_metadata_collection, "/points")
    marker_index = next(
        index
        for index, (method, path, body) in enumerate(qdrant.requests)
        if method == "PUT"
        and path == marker_path
        and body
        and body["points"][0]["id"] == to_qdrant_point_id("openviking:metadata")
    )
    data_create_index = next(
        index
        for index, (method, path, _body) in enumerate(qdrant.requests)
        if method == "PUT" and path == migration._path(migration.target_collection)
    )
    metadata_create_index = next(
        index
        for index, (method, path, _body) in enumerate(qdrant.requests)
        if method == "PUT" and path == migration._path(migration.target_metadata_collection)
    )

    assert data_create_index < marker_index
    assert metadata_create_index < marker_index
    assert result["migration_state"] == "building"
    assert result["setup_complete"] is False


def test_prepare_rejects_source_target_name_collision() -> None:
    qdrant = _legacy_fixture(sparse=False)

    with pytest.raises(ValueError, match="must differ"):
        QdrantMigration(
            client=qdrant,
            source_collection="legacy__context",
            target_collection="legacy__context",
            source_metadata_collection="legacy__context__openviking_meta",
            target_metadata_collection="current__context__openviking_meta",
            logical_collection="legacy/context",
            migration_id="mig-1",
        )


def test_prepare_rejects_foreign_marker_and_shared_metadata_sidecar() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()
    _add_current_marker(qdrant, migration_id="other")

    with pytest.raises(MigrationError, match="migration ID"):
        migration.prepare(confirm=True, plan=plan, lock_held=True)

    with pytest.raises(ValueError, match="pairwise distinct"):
        QdrantMigration(
            client=qdrant,
            source_collection="legacy__context",
            target_collection="current__context",
            source_metadata_collection="legacy__context__openviking_meta",
            target_metadata_collection="legacy__context__openviking_meta",
            logical_collection="legacy/context",
            migration_id="mig-1",
        )


def test_pre_marker_orphan_cleanup_requires_target_absent_review_and_confirm() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()
    qdrant.add_collection(
        migration.target_collection,
        vectors={"vector": {"size": 2, "distance": "Cosine"}},
    )
    qdrant.add_collection(
        migration.target_metadata_collection,
        vectors={"meta": {"size": 1, "distance": "Dot"}},
    )

    with pytest.raises(MigrationError, match="confirm"):
        migration._cleanup_pre_marker_orphan(
            reviewed_plan=plan,
            confirm=False,
            lock_held=True,
        )
    with pytest.raises(MigrationError, match="lock"):
        migration._cleanup_pre_marker_orphan(
            reviewed_plan=plan,
            confirm=True,
            lock_held=False,
        )
    wrong_plan = copy.copy(plan)
    wrong_plan.migration_id = "other"
    with pytest.raises(MigrationError, match="migration_id"):
        migration._cleanup_pre_marker_orphan(
            reviewed_plan=wrong_plan,
            confirm=True,
            lock_held=True,
        )

    migration._cleanup_pre_marker_orphan(
        reviewed_plan=plan,
        confirm=True,
        lock_held=True,
    )

    assert migration.target_collection not in qdrant.collections
    assert migration.target_metadata_collection not in qdrant.collections


def test_apply_checks_frozen_source_before_pre_marker_orphan_cleanup() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()
    layout = migration._layout(
        migration._collection_info(migration.source_collection),
    )
    target_body = migration._target_collection_body(layout)
    qdrant.add_collection(
        migration.target_collection,
        vectors=target_body["vectors"],
    )
    qdrant.add_collection(
        migration.target_metadata_collection,
        vectors={"meta": {"size": 1, "distance": "Dot"}},
    )
    qdrant.collections[migration.source_collection]["points"]["1"]["payload"][
        "name"
    ] = "changed-after-review"

    with pytest.raises(MigrationError, match="stale|source changed"):
        migration.apply(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    assert migration.target_collection in qdrant.collections
    assert migration.target_metadata_collection in qdrant.collections


def test_prepare_race_re_reads_409_and_accepts_only_same_migration_marker() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()
    original_request = qdrant.request
    raced = False

    def race_on_data_create(method, path, body=None, *, params=None):
        nonlocal raced
        if (
            not raced
            and method == "PUT"
            and path == migration._path(migration.target_collection)
        ):
            raced = True
            original_request(method, path, body, params=params)
            metadata = migration._legacy_metadata()
            layout = migration._layout(
                migration._collection_info(migration.source_collection),
            )
            qdrant.add_collection(
                migration.target_metadata_collection,
                vectors={"meta": {"size": 1, "distance": "Dot"}},
                points=[
                    {
                        "id": to_qdrant_point_id("openviking:metadata"),
                        "vector": {"meta": [0.0]},
                        "payload": migration._marker_payload(
                            layout=layout,
                            metadata=metadata,
                            sparse_weight=plan.sparse_weight,
                            source_fingerprint=plan.source_fingerprint,
                            metadata_fingerprint=plan.metadata_fingerprint,
                            sparse_map_fingerprint=plan.sparse_map_fingerprint,
                            setup_complete=False,
                            acl_incomplete_count=plan.acl_incomplete_count,
                            sparse_term_count=plan.sparse_term_count,
                            sparse_term_fingerprint=plan.sparse_term_fingerprint,
                            source_count=plan.source_count,
                            target_count=0,
                        ),
                    }
                ],
            )
            raise _FakeHttpError(409)
        return original_request(method, path, body, params=params)

    qdrant.request = race_on_data_create  # type: ignore[method-assign]

    result = migration.prepare(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert result["migration_id"] == "mig-1"
    assert result["migration_state"] == "building"


def test_prepare_rejects_rolled_back_creation_race() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()

    original_request = qdrant.request
    raced = False

    def race_to_rolled_back_marker(method, path, body=None, *, params=None):
        nonlocal raced
        if (
            not raced
            and method == "PUT"
            and path == migration._path(migration.target_collection)
        ):
            raced = True
            original_request(method, path, body, params=params)
            metadata = migration._legacy_metadata()
            layout = migration._layout(
                migration._collection_info(migration.source_collection),
            )
            qdrant.add_collection(
                migration.target_metadata_collection,
                vectors={"meta": {"size": 1, "distance": "Dot"}},
                points=[
                    {
                        "id": to_qdrant_point_id("openviking:metadata"),
                        "vector": {"meta": [0.0]},
                        "payload": migration._marker_payload(
                            layout=layout,
                            metadata=metadata,
                            sparse_weight=plan.sparse_weight,
                            source_fingerprint=plan.source_fingerprint,
                            metadata_fingerprint=plan.metadata_fingerprint,
                            sparse_map_fingerprint=plan.sparse_map_fingerprint,
                            setup_complete=False,
                            migration_state="rolled_back",
                            acl_incomplete_count=plan.acl_incomplete_count,
                            sparse_term_count=plan.sparse_term_count,
                            sparse_term_fingerprint=plan.sparse_term_fingerprint,
                            source_count=plan.source_count,
                            target_count=0,
                        ),
                    }
                ],
            )
            raise _FakeHttpError(409)
        return original_request(method, path, body, params=params)

    qdrant.request = race_to_rolled_back_marker  # type: ignore[method-assign]

    with pytest.raises(MigrationError, match="rolled_back"):
        migration.prepare(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )
    assert raced is True
    assert migration.target_collection in qdrant.collections
    assert (
        qdrant.collections[migration.target_metadata_collection]["points"][
            to_qdrant_point_id("openviking:metadata")
        ]["payload"]["migration_state"]
        == "rolled_back"
    )


def test_sparse_dictionary_write_is_chunked_and_verified() -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(
        qdrant,
        sparse_map={111: "hello", 222: "world"},
        batch_size=1,
    )
    plan = migration.preflight()

    migration.prepare(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    dictionary_writes = [
        body["points"]
        for method, path, body in qdrant.requests
        if method == "PUT"
        and path == migration._path(migration.target_metadata_collection, "/points")
        and body
        and any(
            point.get("payload", {}).get("_openviking_sparse_term") is True
            for point in body["points"]
        )
    ]
    assert [len(points) for points in dictionary_writes] == [1, 1]
    assert all(
        point["id"] == sparse_owner_point_id(point["payload"]["index"])
        for points in dictionary_writes
        for point in points
    )
    dictionary_requests = [
        (body, params)
        for (method, path, body), params in zip(
            qdrant.requests,
            qdrant.request_params,
            strict=True,
        )
        if method == "PUT"
        and path == migration._path(migration.target_metadata_collection, "/points")
        and body
        and any(
            point.get("payload", {}).get("_openviking_sparse_term") is True
            for point in body["points"]
        )
    ]
    assert all(
        params == {"wait": "true", "ordering": "strong"}
        for _body, params in dictionary_requests
    )
    assert all(
        body["update_filter"]
        == {
            "must_not": [
                {
                    "has_id": [
                        str(point["id"])
                        for point in body["points"]
                    ]
                }
            ]
        }
        for body, _params in dictionary_requests
    )
    assert len(
        [
            point
            for point in qdrant.collections[migration.target_metadata_collection]["points"].values()
            if point.get("payload", {}).get("_openviking_sparse_term") is True
        ]
    ) == 2


def test_backfill_validates_sparse_dictionary_once_per_invocation() -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(
        qdrant,
        batch_size=1,
        sparse_map={111: "hello", 222: "world"},
    )
    plan = migration.preflight()
    migration.prepare(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    qdrant.requests.clear()
    qdrant.request_params.clear()

    result = migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert result["backfill_complete"] is True
    dictionary_scroll_path = migration._path(
        migration.target_metadata_collection,
        "/points/scroll",
    )
    dictionary_scans = [
        request
        for request in qdrant.requests
        if request[0] == "POST" and request[1] == dictionary_scroll_path
    ]
    # The marker plus two dictionary terms require three pages at batch_size=1,
    # regardless of the two source pages copied by this invocation.
    assert len(dictionary_scans) == 3
    term_ids = {
        sparse_owner_point_id(stable_sparse_index("hello")),
        sparse_owner_point_id(stable_sparse_index("world")),
    }
    per_term_lookups = [
        request
        for request in qdrant.requests
        if request[0] == "POST"
        and request[1] == migration._path(migration.target_metadata_collection, "/points")
        and isinstance(request[2], dict)
        and term_ids.intersection(str(point_id) for point_id in request[2].get("ids", []))
    ]
    assert per_term_lookups == []


def test_backfill_rejects_missing_sparse_dictionary_before_writes_and_revalidates_retry() -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(
        qdrant,
        batch_size=1,
        sparse_map={111: "hello", 222: "world"},
    )
    plan = migration.preflight()
    migration.prepare(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    sparse_points = qdrant.collections[migration.target_metadata_collection]["points"]
    missing_id = sparse_owner_point_id(stable_sparse_index("hello"))
    missing_point = sparse_points.pop(missing_id)
    qdrant.requests.clear()
    qdrant.request_params.clear()

    with pytest.raises(SparseMigrationError, match="incomplete"):
        migration.backfill(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    source_scroll_path = migration._path(migration.source_collection, "/points/scroll")
    target_data_path = migration._path(migration.target_collection, "/points")
    assert not any(
        request[0] == "POST" and request[1] == source_scroll_path
        for request in qdrant.requests
    )
    assert not any(
        request[0] == "PUT" and request[1] == target_data_path
        for request in qdrant.requests
    )
    first_invocation_dictionary_scans = sum(
        request[0] == "POST"
        and request[1] == migration._path(
            migration.target_metadata_collection,
            "/points/scroll",
        )
        for request in qdrant.requests
    )
    assert first_invocation_dictionary_scans == 2

    sparse_points[missing_id] = missing_point
    qdrant.requests.clear()
    qdrant.request_params.clear()
    result = migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert result["backfill_complete"] is True
    second_invocation_dictionary_scans = sum(
        request[0] == "POST"
        and request[1] == migration._path(
            migration.target_metadata_collection,
            "/points/scroll",
        )
        for request in qdrant.requests
    )
    assert second_invocation_dictionary_scans == 3


def _apply(migration: QdrantMigration, **kwargs: object):
    kwargs.setdefault("plan", migration.preflight())
    kwargs.setdefault("lock_held", True)
    return migration.apply(**kwargs)


def test_preflight_aggregates_metadata_and_remaps_ids_without_writes() -> None:
    qdrant = _legacy_fixture()
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})

    plan = migration.preflight()

    assert plan.source_count == 2
    assert plan.dense_vector_name == "vector"
    assert plan.vector_dimension == 2
    assert plan.sparse_term_count == 2
    assert plan.target_absent is True
    assert all(method in {"GET", "POST"} for method, _, _ in qdrant.requests)


@pytest.mark.parametrize("kind", ["collection", "index"])
def test_legacy_metadata_point_ids_must_match_deterministic_encoding(kind: str) -> None:
    qdrant = _legacy_fixture(sparse=False)
    expected_id = (
        _legacy_collection_metadata_id("legacy__context")
        if kind == "collection"
        else _legacy_index_metadata_id("legacy__context", "default")
    )
    point = qdrant.collections["legacy__context__openviking_meta"]["points"].pop(expected_id)
    point["id"] = "replaced-metadata-id"
    qdrant.collections["legacy__context__openviking_meta"]["points"][
        "replaced-metadata-id"
    ] = point

    with pytest.raises(MigrationError, match="deterministic encoding"):
        _migration(qdrant).preflight()


@pytest.mark.parametrize("kind", [None, "unexpected"])
def test_legacy_metadata_unknown_kind_fails_closed(kind: str | None) -> None:
    qdrant = _legacy_fixture(sparse=False)
    point = {
        "id": "unexpected-metadata",
        "vector": [0.0],
        "payload": {
            "collection_key": "legacy__context",
            **({"kind": kind} if kind is not None else {}),
        },
    }
    qdrant.collections["legacy__context__openviking_meta"]["points"][
        point["id"]
    ] = point

    with pytest.raises(MigrationError, match="unknown kind"):
        _migration(qdrant).preflight()


def test_apply_creates_current_marker_indexes_and_data_but_never_changes_source() -> None:
    qdrant = _legacy_fixture()
    source_before = copy.deepcopy(qdrant.collections["legacy__context"])
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"}, batch_size=1)

    result = _apply(migration, confirm=True, allow_acl_fail_open=True)

    assert result.migrated_count == 2
    assert result.skipped_count == 0
    assert qdrant.collections["legacy__context"] == source_before
    target = qdrant.collections["current__context"]
    target_meta = qdrant.collections["current__context__openviking_meta"]
    assert len(target["points"]) == 2
    marker = target_meta["points"][to_qdrant_point_id("openviking:metadata")]
    marker_payload = marker["payload"]
    assert marker_payload["_openviking_meta_version"] == 1
    assert marker_payload["collection_name"] == "current__context"
    assert marker_payload["metadata_collection_name"] == (
        "current__context__openviking_meta"
    )
    assert marker_payload["logical_collection"] == "legacy/context"
    assert marker_payload["migration_id"] == "mig-1"
    assert marker_payload["migration_state"] == "ready"
    assert marker_payload["setup_complete"] is True
    assert marker_payload["last_source_cursor"] is None
    assert marker_payload["backfill_complete"] is True
    assert marker_payload["vector_dim"] == marker_payload["vector_dimension"] == 2
    assert marker_payload["source_fingerprint"]
    assert marker_payload["metadata_fingerprint"]
    assert marker_payload["sparse_map_fingerprint"]
    assert marker_payload["indexes"]
    assert result.target_count == 2


def test_apply_reconciles_existing_target_records_from_source() -> None:
    qdrant = _legacy_fixture()
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    target = qdrant.collections["current__context"]["points"]
    existing_id = to_qdrant_point_id("1")
    target[existing_id]["payload"]["name"] = "newer-target-value"
    _mark_current_target_building(qdrant, migration)
    data_writes_before = len(
        [
            request
            for request in qdrant.requests
            if request[0] == "PUT" and request[1].endswith("/current__context/points")
        ]
    )

    result = _apply(migration, confirm=True, allow_acl_fail_open=True)

    assert result.migrated_count == 1
    assert result.skipped_count == 1
    assert target[existing_id]["payload"]["name"] == "doc"
    assert (
        len(
            [
                request
                for request in qdrant.requests
                if request[0] == "PUT" and request[1].endswith("/current__context/points")
            ]
        )
        == data_writes_before + 1
    )


def test_reviewed_plan_can_be_reused_after_target_creation() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()

    first = _apply(migration, confirm=True, plan=plan, allow_acl_fail_open=True)
    _mark_current_target_building(qdrant, migration)
    second = _apply(migration, confirm=True, plan=plan, allow_acl_fail_open=True)

    assert first.migrated_count == 2
    assert second.migrated_count == 0
    assert second.skipped_count == 2


def test_incomplete_marker_resumes_after_data_setup_crash(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()
    original_create_collection = migration._create_collection

    def fail_data_collection(name, body):
        if name == migration.target_collection:
            raise RuntimeError("simulated setup crash")
        return original_create_collection(name, body)

    monkeypatch.setattr(migration, "_create_collection", fail_data_collection)
    with pytest.raises(RuntimeError, match="simulated setup crash"):
        migration.apply(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    assert migration.target_metadata_collection not in qdrant.collections
    assert migration.target_collection not in qdrant.collections

    monkeypatch.setattr(migration, "_create_collection", original_create_collection)
    result = migration.apply(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert result.migrated_count == 2
    assert result.target_count == 2


def test_resume_reconciles_newer_target_vectors() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    existing_id = to_qdrant_point_id("1")
    qdrant.collections["current__context"]["points"][existing_id]["vector"]["vector"] = [
        9.0,
        9.0,
    ]
    _mark_current_target_building(qdrant, migration)

    result = _apply(_migration(qdrant), confirm=True, allow_acl_fail_open=True)

    assert result.migrated_count == 1
    assert result.skipped_count == 1
    assert qdrant.collections["current__context"]["points"][existing_id]["vector"]["vector"] == [
        1.0,
        0.0,
    ]


def test_resume_repairs_malformed_existing_target_vectors() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    existing_id = to_qdrant_point_id("1")
    qdrant.collections["current__context"]["points"][existing_id]["vector"].pop("vector")
    _mark_current_target_building(qdrant, migration)

    result = _apply(_migration(qdrant), confirm=True, allow_acl_fail_open=True)

    assert result.migrated_count == 1
    assert (
        qdrant.collections["current__context"]["points"][existing_id]["vector"]["vector"]
        == [1.0, 0.0]
    )


def test_resume_repairs_sparse_indexes_missing_from_dictionary() -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    existing_id = to_qdrant_point_id("1")
    qdrant.collections["current__context"]["points"][existing_id]["vector"][
        "sparse_vector"
    ]["indices"] = [7]
    _mark_current_target_building(qdrant, migration)

    result = _apply(
        _migration(qdrant, sparse_map={111: "hello", 222: "world"}),
        confirm=True,
        allow_acl_fail_open=True,
    )

    assert result.migrated_count == 1
    assert (
        qdrant.collections["current__context"]["points"][existing_id]["vector"][
            "sparse_vector"
        ]["indices"]
        == [stable_sparse_index("hello")]
    )


def test_resume_repairs_missing_acl_on_existing_complete_target() -> None:
    qdrant = _legacy_fixture(sparse=False)
    for point in qdrant.collections["legacy__context"]["points"].values():
        point["payload"].update(
            {
                "acl_mode": "none",
                "acl_direct_grants": [],
                "acl_inherited_grants": [],
            }
        )
    migration = _migration(qdrant)
    _apply(migration, confirm=True)
    existing_id = to_qdrant_point_id("1")
    qdrant.collections["current__context"]["points"][existing_id]["payload"].pop(
        "acl_mode"
    )
    _mark_current_target_building(qdrant, migration)

    result = _apply(_migration(qdrant), confirm=True)

    assert result.migrated_count == 1
    assert "acl_mode" in qdrant.collections["current__context"]["points"][
        existing_id
    ]["payload"]


def test_missing_original_id_fails_before_target_creation() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context"]["points"]["1"]["payload"].pop(
        "_openviking_original_id"
    )

    with pytest.raises(MigrationError, match="original id"):
        _migration(qdrant).preflight()

    assert "current__context" not in qdrant.collections


def test_numeric_and_string_ids_that_map_to_one_point_fail_closed() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context"]["points"][to_qdrant_point_id("1")] = _point(
        to_qdrant_point_id("1"),
        "1",
        uri="/resources/c.md",
    )

    with pytest.raises(MigrationError, match="collision"):
        _migration(qdrant).preflight()


def test_duplicate_logical_source_ids_fail_closed() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context"]["points"][to_qdrant_point_id("1")] = _point(
        to_qdrant_point_id("1"),
        "1",
        uri="/resources/c.md",
    )

    with pytest.raises(MigrationError, match="collision"):
        _migration(qdrant).preflight()


def test_source_count_must_match_pagination() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.count_overrides["legacy__context"] = 3

    with pytest.raises(MigrationError, match="source count"):
        _migration(qdrant).preflight()


def _prepare_backfill(
    qdrant: FakeQdrant,
    *,
    batch_size: int = 1,
) -> tuple[QdrantMigration, object]:
    migration = _migration(qdrant, batch_size=batch_size)
    plan = migration.preflight()
    migration.prepare(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    return migration, plan


def _prepare_reconcile(
    qdrant: FakeQdrant,
    *,
    batch_size: int = 1,
) -> tuple[QdrantMigration, object]:
    migration, plan = _prepare_backfill(qdrant, batch_size=batch_size)
    migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    return migration, plan


def test_reconcile_upserts_source_payload_and_vector_changes() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    source = qdrant.collections["legacy__context"]["points"]["1"]
    source["payload"]["name"] = "changed"
    source["vector"]["vector"] = [0.0, 1.0]

    result = migration.reconcile(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    target = qdrant.collections["current__context"]["points"][
        to_qdrant_point_id("1")
    ]
    assert target["payload"]["name"] == "changed"
    assert target["vector"]["vector"] == [0.0, 1.0]
    assert result["source_count"] == 2
    assert result["migration_state"] == "building"
    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["migration_state"] == "building"
    assert marker["setup_complete"] is False


def test_reconcile_deletes_target_extras() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    extra = _point(to_qdrant_point_id("extra"), "extra", uri="/resources/extra.md")
    qdrant.collections["current__context"]["points"][extra["id"]] = extra

    migration.reconcile(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert extra["id"] not in qdrant.collections["current__context"]["points"]


def test_reconcile_rejects_valid_state_change_before_point_write(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    qdrant.collections["legacy__context"]["points"]["1"]["payload"]["name"] = "changed"
    original_retrieve = migration._retrieve
    injected = False
    in_round = False

    def retrieve(collection, point_ids, *, with_vectors):
        nonlocal injected
        result = original_retrieve(
            collection,
            point_ids,
            with_vectors=with_vectors,
        )
        if collection == migration.target_collection and in_round and not injected:
            injected = True
            _set_marker_state(qdrant, migration, "ready")
        return result

    original_round = migration._reconcile_round

    def round_wrapper(*, layout, schema, metadata, state):
        nonlocal in_round
        in_round = True
        try:
            return original_round(
                layout=layout,
                schema=schema,
                metadata=metadata,
                state=state,
            )
        finally:
            in_round = False

    monkeypatch.setattr(migration, "_retrieve", retrieve)
    monkeypatch.setattr(migration, "_reconcile_round", round_wrapper)
    before_writes = sum(
        method == "PUT" and path.endswith(f"/{migration.target_collection}/points")
        for method, path, _body in qdrant.requests
    )
    with pytest.raises(MigrationError, match="expected state"):
        migration.reconcile(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )
    after_writes = sum(
        method == "PUT" and path.endswith(f"/{migration.target_collection}/points")
        for method, path, _body in qdrant.requests
    )
    assert injected
    assert after_writes == before_writes
    assert (
        qdrant.collections[migration.target_metadata_collection]["points"][
            to_qdrant_point_id("openviking:metadata")
        ]["payload"]["migration_state"]
        == "ready"
    )


def test_reconcile_rejects_valid_state_change_before_delete(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    extra = _point(to_qdrant_point_id("extra"), "extra", uri="/resources/extra.md")
    qdrant.collections[migration.target_collection]["points"][extra["id"]] = extra
    original_scroll = migration._scroll
    injected = False
    in_round = False

    def scroll(collection, *, with_vectors, filter=None):
        nonlocal injected
        for point in original_scroll(
            collection,
            with_vectors=with_vectors,
            filter=filter,
        ):
            if collection == migration.target_collection and in_round and not injected:
                injected = True
                _set_marker_state(qdrant, migration, "ready")
            yield point

    original_round = migration._reconcile_round

    def round_wrapper(*, layout, schema, metadata, state):
        nonlocal in_round
        in_round = True
        try:
            return original_round(
                layout=layout,
                schema=schema,
                metadata=metadata,
                state=state,
            )
        finally:
            in_round = False

    monkeypatch.setattr(migration, "_scroll", scroll)
    monkeypatch.setattr(migration, "_reconcile_round", round_wrapper)
    with pytest.raises(MigrationError, match="expected state"):
        migration.reconcile(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )
    assert injected
    assert extra["id"] in qdrant.collections[migration.target_collection]["points"]


@pytest.mark.parametrize("boundary", ["indexes", "dictionary"])
def test_prepare_rejects_valid_state_change_before_setup_mutation(
    monkeypatch,
    boundary: str,
) -> None:
    qdrant = _legacy_fixture(sparse=boundary == "dictionary")
    migration = _migration(
        qdrant,
        sparse_map=({111: "hello", 222: "world"} if boundary == "dictionary" else None),
    )
    plan = migration.preflight()
    method_name = "_write_indexes" if boundary == "indexes" else "_write_sparse_dictionary"
    original = getattr(migration, method_name)

    def mutate_then_write(*args, **kwargs):
        _set_marker_state(qdrant, migration, "ready")
        return original(*args, **kwargs)

    monkeypatch.setattr(migration, method_name, mutate_then_write)
    before_indexes = sum(
        method == "PUT" and path.endswith("/index")
        for method, path, _body in qdrant.requests
    )
    before_dictionary = sum(
        method == "PUT" and path.endswith(f"/{migration.target_metadata_collection}/points")
        for method, path, _body in qdrant.requests
    )
    with pytest.raises(MigrationError, match="expected state|prepare may resume"):
        migration.prepare(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["migration_state"] == "ready"
    assert marker["setup_complete"] is True
    after_indexes = sum(
        method == "PUT" and path.endswith("/index")
        for method, path, _body in qdrant.requests
    )
    after_dictionary = sum(
        method == "PUT" and path.endswith(f"/{migration.target_metadata_collection}/points")
        for method, path, _body in qdrant.requests
    )
    if boundary == "indexes":
        assert after_indexes == before_indexes
    else:
        assert after_dictionary == before_dictionary + 1  # first marker only


def test_transition_rejects_state_change_before_marker_write(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, _plan = _prepare_reconcile(qdrant)
    _set_marker_state(qdrant, migration, "ready")
    original = migration._write_marker
    injected = False

    def mutate_then_write(marker, **kwargs):
        nonlocal injected
        injected = True
        _set_marker_state(qdrant, migration, "active")
        return original(marker, **kwargs)

    monkeypatch.setattr(migration, "_write_marker", mutate_then_write)
    with pytest.raises(MigrationError, match="expected state"):
        migration._transition("cutting_over")
    assert injected
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["migration_state"] == "active"


@pytest.mark.parametrize("tamper", ["building", "foreign", "missing"])
def test_retire_rejects_state_or_ownership_change_before_delete(monkeypatch, tamper) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "active")
    plan = migration.preflight()
    original = migration._validate_retire_pair
    tampered = False

    def validate(**kwargs):
        nonlocal tampered
        result = original(**kwargs)
        if kwargs["expected_state"] == "retained" and not tampered:
            tampered = True
            if tamper == "building":
                _set_marker_state(qdrant, migration, "building")
            elif tamper == "foreign":
                qdrant.collections[migration.target_metadata_collection]["points"][
                    to_qdrant_point_id("openviking:metadata")
                ]["payload"]["migration_id"] = "foreign"
            else:
                qdrant.collections.pop(migration.target_metadata_collection)
        return result

    monkeypatch.setattr(migration, "_validate_retire_pair", validate)
    with pytest.raises(MigrationError):
        migration.retire(
            confirm=True,
            plan=plan,
            lock_held=True,
            hooks=_LifecycleHooks(),
        )
    assert tampered
    assert migration.target_collection in qdrant.collections


def test_reconcile_uses_sqlite_manifest_not_an_unbounded_id_set(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    paths: list[str] = []
    lookups: list[str] = []

    class TrackingScanManifest(_ScanManifest):
        def __init__(self) -> None:
            super().__init__()
            paths.append(self._path)

        def has_source_target(self, target_id: str) -> bool:
            lookups.append(target_id)
            return super().has_source_target(target_id)

    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate._ScanManifest",
        TrackingScanManifest,
    )
    migration.reconcile(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert lookups
    assert paths
    assert all(not Path(path).exists() for path in paths)


def test_reconcile_requires_external_lock_even_with_barrier() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["migration_state"] = "cutting_over"
    marker["setup_complete"] = True
    before_points = copy.deepcopy(qdrant.collections["current__context"]["points"])
    before_requests = len(qdrant.requests)

    with pytest.raises(MigrationError, match="lock"):
        migration.reconcile(
            confirm=True,
            plan=plan,
            barrier_held=True,
            allow_acl_fail_open=True,
            lock_held=False,
        )

    assert qdrant.collections["current__context"]["points"] == before_points
    assert len(qdrant.requests) == before_requests


def test_reconcile_rechecks_fingerprint_candidates_with_direct_payload_vector_compare(
    monkeypatch,
) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    target = qdrant.collections["current__context"]["points"][
        to_qdrant_point_id("1")
    ]
    target["payload"]["name"] = "stale"
    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate._point_fingerprint",
        lambda **_kwargs: "same",
    )

    migration.reconcile(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert (
        qdrant.collections["current__context"]["points"][
            to_qdrant_point_id("1")
        ]["payload"]["name"]
        == "doc"
    )


def test_reconcile_compares_canonical_float32_vector_values() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context"]["points"]["1"]["vector"]["vector"] = [
        0.1,
        0.2,
    ]
    migration, plan = _prepare_reconcile(qdrant)
    target = qdrant.collections["current__context"]["points"][
        to_qdrant_point_id("1")
    ]
    target["vector"]["vector"] = [0.10000000149011612, 0.20000000298023224]
    result = migration.reconcile(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert result["migrated_count"] == 0
    assert target["vector"]["vector"] == [0.10000000149011612, 0.20000000298023224]


def test_reconcile_fails_closed_on_metadata_or_sparse_map_drift(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    original = migration._upsert_target_batch

    def mutate_map(points, **kwargs):
        migration._sparse_map[7] = "changed"
        return original(points, **kwargs)

    monkeypatch.setattr(migration, "_upsert_target_batch", mutate_map)
    with pytest.raises(MigrationError, match="sparse map"):
        migration.reconcile(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["migration_state"] == "failed"


def test_reconcile_fails_closed_on_metadata_drift(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    original = migration._reconcile_round
    rounds = 0

    def mutate_metadata_after_round(*, layout, schema, metadata, state):
        nonlocal rounds
        snapshot = original(
            layout=layout,
            schema=schema,
            metadata=metadata,
            state=state,
        )
        rounds += 1
        if rounds == 1:
            metadata_point = qdrant.collections[
                "legacy__context__openviking_meta"
            ]["points"][_legacy_collection_metadata_id("legacy__context")]
            metadata_point["payload"]["meta"]["CollectionName"] = "changed"
        return snapshot

    monkeypatch.setattr(migration, "_reconcile_round", mutate_metadata_after_round)
    with pytest.raises(MigrationError, match="metadata"):
        migration.reconcile(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["migration_state"] == "failed"


def test_reconcile_fails_after_three_non_converging_rounds(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    original = migration._reconcile_round
    calls = 0

    def changing_source(*, layout, schema, metadata, state):
        nonlocal calls
        calls += 1
        snapshot = original(
            layout=layout,
            schema=schema,
            metadata=metadata,
            state=state,
        )
        source = qdrant.collections["legacy__context"]["points"]["1"]
        source["payload"]["name"] = f"changed-{calls}"
        return snapshot

    monkeypatch.setattr(migration, "_reconcile_round", changing_source)
    with pytest.raises(MigrationError, match="round 3"):
        migration.reconcile(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )
    assert calls == 3


def test_reconcile_publishes_only_final_stable_source_snapshot(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    original = migration._reconcile_round
    snapshots = []

    def mutate_once(*, layout, schema, metadata, state):
        snapshot = original(
            layout=layout,
            schema=schema,
            metadata=metadata,
            state=state,
        )
        snapshots.append(snapshot)
        if len(snapshots) == 1:
            source = qdrant.collections["legacy__context"]["points"]["1"]
            source["payload"]["name"] = "final"
        return snapshot

    monkeypatch.setattr(migration, "_reconcile_round", mutate_once)
    result = migration.reconcile(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert len(snapshots) == 3
    assert snapshots[0].fingerprint != snapshots[-1].fingerprint
    assert marker["source_fingerprint"] == snapshots[-1].fingerprint
    assert marker["migration_state"] == "building"
    assert result["rounds"] == 3
    assert (
        qdrant.collections["current__context"]["points"][
            to_qdrant_point_id("1")
        ]["payload"]["name"]
        == "final"
    )


@pytest.mark.parametrize("tamper", ["foreign", "ready"])
def test_reconcile_rechecks_final_marker_before_publication(
    monkeypatch,
    tamper: str,
) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    original_round = migration._reconcile_round
    original_load = migration._load_current_marker
    rounds = 0
    tampered = False
    writes_at_tamper: list[int] = []

    def mutation_count() -> int:
        return sum(
            method == "PUT" and path.endswith("/points")
            or method == "POST" and path.endswith("/points/delete")
            for method, path, _body in qdrant.requests
        )

    def track_round(*, layout, schema, metadata, state):
        nonlocal rounds
        snapshot = original_round(
            layout=layout,
            schema=schema,
            metadata=metadata,
            state=state,
        )
        rounds += 1
        return snapshot

    def tamper_final_marker():
        nonlocal tampered
        if rounds >= 2 and not tampered:
            tampered = True
            marker = qdrant.collections[
                "current__context__openviking_meta"
            ]["points"][to_qdrant_point_id("openviking:metadata")]["payload"]
            if tamper == "foreign":
                marker["migration_id"] = "foreign"
            else:
                marker["migration_state"] = "ready"
                marker["setup_complete"] = True
            writes_at_tamper.append(mutation_count())
        return original_load()

    monkeypatch.setattr(migration, "_reconcile_round", track_round)
    monkeypatch.setattr(migration, "_load_current_marker", tamper_final_marker)
    with pytest.raises(MigrationError, match="migration ID|state changed"):
        migration.reconcile(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert writes_at_tamper
    assert mutation_count() == writes_at_tamper[0]
    if tamper == "foreign":
        assert marker["migration_id"] == "foreign"
    else:
        assert marker["migration_state"] == "ready"
        assert marker["setup_complete"] is True


def test_cutover_reconcile_preserves_cutting_over_and_setup_gate() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["migration_state"] = "cutting_over"
    marker["setup_complete"] = True

    migration.reconcile(
        confirm=True,
        plan=plan,
        barrier_held=True,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert marker["migration_state"] == "cutting_over"
    assert marker["setup_complete"] is True


def test_interrupted_reconcile_rebuilds_and_deletes_manifest(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    paths: list[str] = []

    class TrackingScanManifest(_ScanManifest):
        def __init__(self) -> None:
            super().__init__()
            paths.append(self._path)

    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate._ScanManifest",
        TrackingScanManifest,
    )
    monkeypatch.setattr(
        migration,
        "_upsert_target_batch",
        lambda _points, **_kwargs: (_ for _ in ()).throw(RuntimeError("stop")),
    )
    with pytest.raises(RuntimeError, match="stop"):
        migration.reconcile(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )
    assert paths
    assert all(not Path(path).exists() for path in paths)


def test_count_requests_strong_consistency() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)

    assert migration._count(migration.source_collection) == 2

    count_params = [
        params
        for (method, path, _body), params in zip(
            qdrant.requests,
            qdrant.request_params,
            strict=True,
        )
        if method == "POST" and path.endswith("/points/count")
    ]
    assert count_params == [{"consistency": "all"}]


def test_backfill_persists_integer_cursor_after_each_batch() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_backfill(qdrant, batch_size=1)
    written_markers: list[dict[str, object]] = []
    original_write_marker = migration._write_marker

    def record_marker(marker, **kwargs):
        written_markers.append(copy.deepcopy(marker))
        return original_write_marker(marker, **kwargs)

    migration._write_marker = record_marker  # type: ignore[method-assign]

    result = migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert result["backfill_complete"] is True
    assert [marker["last_source_cursor"] for marker in written_markers] == [1, None]
    assert written_markers[0]["backfill_complete"] is False
    assert written_markers[-1]["backfill_complete"] is True


def test_backfill_persists_string_cursor_without_coercion(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_backfill(qdrant, batch_size=1)
    source_points = list(
        qdrant.collections[migration.source_collection]["points"].values()
    )
    original_scroll_page = migration._scroll_page
    cursor = "550e8400-e29b-41d4-a716-446655440001"
    pages = {
        None: ([copy.deepcopy(source_points[0])], cursor),
        cursor: ([copy.deepcopy(source_points[1])], None),
    }

    def scroll_page(collection, *, offset, with_vectors, filter=None):
        if collection != migration.source_collection:
            return original_scroll_page(
                collection,
                offset=offset,
                with_vectors=with_vectors,
                filter=filter,
            )
        assert with_vectors is True
        return pages[offset]

    monkeypatch.setattr(migration, "_scroll_page", scroll_page)

    result = migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert result["backfill_complete"] is True
    assert result["last_source_cursor"] is None
    assert qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]["last_source_cursor"] is None


def test_backfill_rejects_malformed_or_repeated_cursor(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_backfill(qdrant, batch_size=1)
    source_point = next(
        iter(qdrant.collections[migration.source_collection]["points"].values())
    )
    original_scroll_page = migration._scroll_page

    def malformed(collection, *, offset, with_vectors, filter=None):
        if collection != migration.source_collection:
            return original_scroll_page(
                collection,
                offset=offset,
                with_vectors=with_vectors,
                filter=filter,
            )
        return [copy.deepcopy(source_point)], {"not": "an offset"}

    monkeypatch.setattr(migration, "_scroll_page", malformed)
    with pytest.raises(MigrationError, match="offset"):
        migration.backfill(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["last_source_cursor"] = 0
    marker["backfill_complete"] = False

    def repeated(collection, *, offset, with_vectors, filter=None):
        if collection != migration.source_collection:
            return original_scroll_page(
                collection,
                offset=offset,
                with_vectors=with_vectors,
                filter=filter,
            )
        assert offset == 0
        return [copy.deepcopy(source_point)], 0

    monkeypatch.setattr(migration, "_scroll_page", repeated)
    with pytest.raises(MigrationError, match="repeated"):
        migration.backfill(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )


def test_backfill_rejects_invalid_uuid_cursor(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_backfill(qdrant, batch_size=1)
    source_point = next(
        iter(qdrant.collections[migration.source_collection]["points"].values())
    )
    original_scroll_page = migration._scroll_page

    def malformed_uuid(collection, *, offset, with_vectors, filter=None):
        if collection != migration.source_collection:
            return original_scroll_page(
                collection,
                offset=offset,
                with_vectors=with_vectors,
                filter=filter,
            )
        return [copy.deepcopy(source_point)], "not-a-qdrant-uuid"

    monkeypatch.setattr(migration, "_scroll_page", malformed_uuid)
    with pytest.raises(MigrationError, match="UUID"):
        migration.backfill(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )


def test_backfill_rejects_completed_marker_with_cursor() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_backfill(qdrant, batch_size=1)
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["backfill_complete"] = True
    marker["last_source_cursor"] = 1

    with pytest.raises(MigrationError, match="completion"):
        migration.backfill(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )


def test_failed_batch_can_be_retried_without_source_mutation(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_backfill(qdrant, batch_size=1)
    source_before = copy.deepcopy(qdrant.collections[migration.source_collection])
    original_write_points = migration._write_points
    failed = False

    def fail_once(collection, points, **kwargs):
        nonlocal failed
        if collection == migration.target_collection and not failed:
            failed = True
            raise MigrationError("target write failed")
        return original_write_points(collection, points, **kwargs)

    monkeypatch.setattr(migration, "_write_points", fail_once)
    with pytest.raises(MigrationError, match="target write failed"):
        migration.backfill(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["last_source_cursor"] is None
    assert marker["backfill_complete"] is False
    assert qdrant.collections[migration.source_collection] == source_before

    result = migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    assert result["backfill_complete"] is True


def test_backfill_holds_one_page_and_one_write_batch(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_backfill(qdrant, batch_size=1)
    source_points = list(
        qdrant.collections[migration.source_collection]["points"].values()
    )
    pages = {
        None: ([copy.deepcopy(source_points[0])], 1),
        1: ([copy.deepcopy(source_points[1])], None),
    }
    original_scroll_page = migration._scroll_page
    page_sizes: list[int] = []
    batch_sizes: list[int] = []
    original_upsert = migration._upsert_target_batch

    def scroll_page(collection, *, offset, with_vectors, filter=None):
        if collection != migration.source_collection:
            return original_scroll_page(
                collection,
                offset=offset,
                with_vectors=with_vectors,
                filter=filter,
            )
        page = pages[offset]
        page_sizes.append(len(page[0]))
        return page

    def upsert(points, **kwargs):
        batch_sizes.append(len(points))
        return original_upsert(points, **kwargs)

    monkeypatch.setattr(migration, "_scroll_page", scroll_page)
    monkeypatch.setattr(migration, "_upsert_target_batch", upsert)
    migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert page_sizes == [1, 1]
    assert batch_sizes == [1, 1]


def test_completed_backfill_resume_requires_acl_ack_without_scanning_source(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_backfill(qdrant, batch_size=1)
    migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    original_scroll_page = migration._scroll_page

    def reject_source_scan(collection, *, offset, with_vectors, filter=None):
        if collection == migration.source_collection:
            raise AssertionError("completed backfill scanned the source")
        return original_scroll_page(
            collection,
            offset=offset,
            with_vectors=with_vectors,
            filter=filter,
        )

    monkeypatch.setattr(migration, "_scroll_page", reject_source_scan)
    before = copy.deepcopy(qdrant.collections)
    with pytest.raises(MigrationError, match="refusing backfill without --allow-acl-fail-open"):
        migration.backfill(confirm=True, plan=plan, lock_held=True)
    assert qdrant.collections == before

    result = migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert result["backfill_complete"] is True
    assert result["migrated_count"] == 0


def test_marker_failure_after_target_write_retries_same_page(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_backfill(qdrant, batch_size=1)
    original_write_marker = migration._write_marker
    failed = False

    def fail_once(marker, **kwargs):
        nonlocal failed
        if marker["last_source_cursor"] == 1 and not failed:
            failed = True
            raise MigrationError("marker write failed")
        return original_write_marker(marker, **kwargs)

    monkeypatch.setattr(migration, "_write_marker", fail_once)
    with pytest.raises(MigrationError, match="marker write failed"):
        migration.backfill(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["last_source_cursor"] is None
    assert marker["backfill_complete"] is False
    assert to_qdrant_point_id("1") in qdrant.collections[
        migration.target_collection
    ]["points"]

    result = migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    assert result["backfill_complete"] is True


def test_cross_page_duplicate_source_point_fails_closed(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_backfill(qdrant, batch_size=1)
    source_point = next(
        iter(qdrant.collections[migration.source_collection]["points"].values())
    )
    original_scroll_page = migration._scroll_page

    def duplicate_page(collection, *, offset, with_vectors, filter=None):
        if collection != migration.source_collection:
            return original_scroll_page(
                collection,
                offset=offset,
                with_vectors=with_vectors,
                filter=filter,
            )
        if offset is None:
            return [copy.deepcopy(source_point)], 1
        return [copy.deepcopy(source_point)], None

    monkeypatch.setattr(migration, "_scroll_page", duplicate_page)
    with pytest.raises(MigrationError, match="duplicate point id"):
        migration.backfill(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["last_source_cursor"] == 1
    assert marker["backfill_complete"] is False


def test_backfill_preserves_canonical_observations_across_batch_boundaries() -> None:
    qdrant = _legacy_fixture(sparse=True)
    second = next(
        point
        for point in qdrant.collections["legacy__context"]["points"].values()
        if point["payload"]["_openviking_original_id"]
        == "550e8400-e29b-41d4-a716-446655440000"
    )
    second["vector"]["sparse_vector"] = {"indices": [111], "values": [0.3]}
    migration = _migration(
        qdrant,
        sparse_map={111: "hello"},
        batch_size=1,
    )
    plan = migration.preflight()
    migration.prepare(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    marker_before = copy.deepcopy(
        qdrant.collections[migration.target_metadata_collection]["points"][
            to_qdrant_point_id("openviking:metadata")
        ]["payload"]
    )

    migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    marker_after = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    for field_name in (
        "source_count",
        "source_fingerprint",
        "acl_incomplete_count",
        "sparse_term_count",
        "sparse_term_fingerprint",
    ):
        assert marker_after[field_name] == marker_before[field_name] == plan.to_dict()[
            field_name
        ]


def test_apply_resumes_from_persisted_backfill_cursor(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant, batch_size=1)
    plan = migration.preflight()
    original_upsert = migration._upsert_target_batch
    calls = 0

    def fail_second_batch(points, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise MigrationError("simulated failed apply batch")
        return original_upsert(points, **kwargs)

    monkeypatch.setattr(migration, "_upsert_target_batch", fail_second_batch)
    with pytest.raises(MigrationError, match="simulated failed apply batch"):
        migration.apply(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["last_source_cursor"] == 1
    assert marker["backfill_complete"] is False

    monkeypatch.setattr(migration, "_upsert_target_batch", original_upsert)
    result = migration.apply(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert result.migrated_count == 1
    assert result.target_count == 2


def test_target_count_must_match_pagination() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    qdrant.count_overrides["current__context"] = 3

    with pytest.raises(MigrationError, match="target count"):
        _migration(qdrant).preflight()


def test_sparse_hash_collisions_fail_closed(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=True)
    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate.stable_sparse_index",
        lambda term: 7,
    )

    with pytest.raises(SparseMigrationError, match="collision"):
        _migration(qdrant, sparse_map={111: "hello", 222: "world"}).preflight()


def test_sparse_manifest_accepts_legacy_alias_and_owner_for_same_binding() -> None:
    term = "hello"
    index = stable_sparse_index(term)
    with _SparseDictionaryManifest() as manifest:
        manifest.add(
            term,
            index,
            point_id=to_qdrant_point_id(f"openviking:sparse:{term}"),
        )
        manifest.add(term, index, point_id=sparse_owner_point_id(index))

        assert manifest.index_for_term(term) == index
        assert manifest.term_for_index(index) == term
        assert manifest.has_owner(index)


def test_existing_sparse_dictionary_accepts_retained_alias_without_provenance() -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    alias_id = to_qdrant_point_id("openviking:sparse:hello")
    qdrant.collections[migration.target_metadata_collection]["points"][alias_id] = {
        "id": alias_id,
        "vector": {"meta": [0.0]},
        "payload": {
            "_openviking_sparse_term": True,
            "term": "hello",
            "index": stable_sparse_index("hello"),
        },
    }

    migration.preflight()


def test_existing_sparse_dictionary_rejects_partial_provenance() -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    alias_id = to_qdrant_point_id("openviking:sparse:hello")
    qdrant.collections[migration.target_metadata_collection]["points"][alias_id] = {
        "id": alias_id,
        "vector": {"meta": [0.0]},
        "payload": {
            "_openviking_sparse_term": True,
            "term": "hello",
            "index": stable_sparse_index("hello"),
            "logical_collection": migration.logical_collection,
        },
    }

    with pytest.raises(SparseMigrationError, match="incomplete migration provenance"):
        migration.preflight()


def test_existing_sparse_dictionary_rejects_foreign_provenance() -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    alias_id = to_qdrant_point_id("openviking:sparse:hello")
    qdrant.collections[migration.target_metadata_collection]["points"][alias_id] = {
        "id": alias_id,
        "vector": {"meta": [0.0]},
        "payload": {
            "_openviking_sparse_term": True,
            "term": "hello",
            "index": stable_sparse_index("hello"),
            "logical_collection": "other/context",
            "migration_id": "other-migration",
        },
    }

    with pytest.raises(SparseMigrationError, match="another migration"):
        migration.preflight()


def test_existing_sparse_dictionary_collisions_fail_closed() -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    metadata_points = qdrant.collections["current__context__openviking_meta"]["points"]
    hello_id = sparse_owner_point_id(stable_sparse_index("hello"))
    metadata_points[hello_id]["payload"]["term"] = "different"

    with pytest.raises(SparseMigrationError, match="invalid sparse point"):
        _migration(qdrant, sparse_map={111: "hello", 222: "world"}).preflight()


def test_existing_sparse_dictionary_point_id_collision_fails_closed() -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    metadata_points = qdrant.collections["current__context__openviking_meta"]["points"]
    hello_id = sparse_owner_point_id(stable_sparse_index("hello"))
    metadata_points[hello_id]["payload"].update(
        {"term": "different", "index": 999}
    )

    with pytest.raises(SparseMigrationError, match="invalid sparse point"):
        _migration(qdrant, sparse_map={111: "hello", 222: "world"}).preflight()


def test_sparse_dictionary_write_is_verified_before_completion(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})
    original_write_points = migration._write_points

    def drop_dictionary_write(collection, points, **kwargs):
        if collection == migration.target_metadata_collection and any(
            point.get("payload", {}).get("_openviking_sparse_term") is True
            for point in points
        ):
            return
        return original_write_points(collection, points, **kwargs)

    monkeypatch.setattr(migration, "_write_points", drop_dictionary_write)

    with pytest.raises(SparseMigrationError, match="incomplete"):
        _apply(migration, confirm=True, allow_acl_fail_open=True)

    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["setup_complete"] is False


def test_existing_target_allows_newer_schema_fields_and_sparse_policy() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["schema"]["Fields"].append(
        {"FieldName": "acl_enabled", "FieldType": "bool"}
    )
    marker["sparse_weight"] = 0.9

    plan = _migration(qdrant).preflight()

    assert plan.target_absent is False


def test_complete_marker_indexes_must_exist_physically() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["indexes"]["bogus"] = {"ScalarIndex": ["not_physical"]}

    with pytest.raises(MigrationError, match="missing payload index.*not_physical"):
        _migration(qdrant).preflight()


def test_marker_scalar_index_must_have_valid_shape() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["indexes"]["bogus"] = {"ScalarIndex": "not-a-list"}

    with pytest.raises(MigrationError, match="ScalarIndex is malformed"):
        _migration(qdrant).preflight()


def test_marker_index_map_must_match_legacy_metadata() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["indexes"]["extra"] = {"VectorIndex": {"IndexType": "hnsw"}}

    with pytest.raises(MigrationError, match="index map differs.*extra"):
        _migration(qdrant).preflight()


def test_marker_index_metadata_must_match_legacy_metadata() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["indexes"]["default"]["Description"] = "tampered"

    with pytest.raises(MigrationError, match="index 'default' changed"):
        _migration(qdrant).preflight()


@pytest.mark.parametrize("size", [2.5, True, "2"])
def test_dense_vector_size_must_be_a_positive_integer(size: object) -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context"]["config"]["params"]["vectors"]["vector"][
        "size"
    ] = size

    with pytest.raises(MigrationError, match="positive integer"):
        _migration(qdrant).preflight()


def test_apply_rejects_ready_target_before_copy() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["operator_extension"] = {"retention": "audit"}
    marker_before = copy.deepcopy(marker)
    target_before = copy.deepcopy(qdrant.collections["current__context"]["points"])
    data_writes_before = len(
        [
            request
            for request in qdrant.requests
            if request[0] == "PUT" and request[1].endswith("/current__context/points")
        ]
    )

    with pytest.raises(MigrationError, match="ready"):
        _apply(_migration(qdrant), confirm=True, allow_acl_fail_open=True)

    assert marker == marker_before
    assert qdrant.collections["current__context"]["points"] == target_before
    assert (
        len(
            [
                request
                for request in qdrant.requests
                if request[0] == "PUT"
                and request[1].endswith("/current__context/points")
            ]
        )
        == data_writes_before
    )


def test_apply_rechecks_marker_state_after_prepare_before_copy(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()
    original_prepare = migration.prepare

    def prepare_then_ready(**kwargs):
        marker = original_prepare(**kwargs)
        migration._transition("ready")
        return marker

    monkeypatch.setattr(migration, "prepare", prepare_then_ready)

    with pytest.raises(MigrationError, match="ready"):
        migration.apply(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["migration_state"] == "ready"
    assert marker["setup_complete"] is True
    assert qdrant.collections[migration.target_collection]["points"] == {}


def test_existing_target_rejects_changed_source_field_type() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    level = next(field for field in marker["schema"]["Fields"] if field["FieldName"] == "level")
    level["FieldType"] = "string"

    with pytest.raises(MigrationError, match="schema"):
        _migration(qdrant).preflight()


def test_existing_target_requires_a_marker_owned_by_target_collection() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.add_collection(
        "current__context",
        vectors={"vector": {"size": 2, "distance": "Cosine"}},
    )

    with pytest.raises(MigrationError, match="current marker"):
        _migration(qdrant).preflight()


def test_sparse_data_without_authoritative_mapping_fails_closed() -> None:
    qdrant = _legacy_fixture(sparse=True)

    with pytest.raises(SparseMigrationError, match="authoritative"):
        _migration(qdrant).preflight()


def test_conflicting_sparse_weights_fail_closed() -> None:
    qdrant = _legacy_fixture(sparse=True)
    secondary_id = _legacy_index_metadata_id("legacy__context", "secondary")
    qdrant.collections["legacy__context__openviking_meta"]["points"][secondary_id] = {
        "id": secondary_id,
        "vector": [0.0],
        "payload": {
            "kind": "index",
            "collection_key": "legacy__context",
            "index_name": "secondary",
            "meta": {
                "IndexName": "secondary",
                "VectorIndex": {"IndexType": "hnsw_hybrid", "Distance": "Cosine"},
                "SparseWeight": 0.7,
            },
        },
    }

    with pytest.raises(MigrationError, match="conflicting sparse weights"):
        _migration(qdrant, sparse_map={111: "hello", 222: "world"}).preflight()


def test_uri_sidecars_are_recomputed_and_payload_is_not_dropped() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context"]["points"]["1"]["payload"].update(
        {
            "uri": "viking://resources/nested/a.md",
            "parent_uri": "viking://resources/nested",
            "scope_roots": ["/wrong"],
            "uri_depth": 99,
            "tags": ["keep", "this"],
        }
    )
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    payload = qdrant.collections["current__context"]["points"][to_qdrant_point_id("1")][
        "payload"
    ]

    assert payload["uri"] == "/resources/nested/a.md"
    assert payload["parent_uri"] == "/resources/nested"
    assert payload["uri_depth"] == 3
    assert payload["scope_roots"] == ["/", "/resources", "/resources/nested", "/resources/nested/a.md"]
    assert payload["tags"] == ["keep", "this"]


def test_ownerless_uri_does_not_require_owner_user_id() -> None:
    qdrant = _legacy_fixture(sparse=False)
    payload = qdrant.collections["legacy__context"]["points"]["1"]["payload"]
    payload["uri"] = "/user"
    payload.pop("owner_user_id")

    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)

    target_payload = qdrant.collections["current__context"]["points"][
        to_qdrant_point_id("1")
    ]["payload"]
    assert "owner_user_id" not in target_payload
    _mark_current_target_building(qdrant, migration)
    result = _apply(migration, confirm=True, allow_acl_fail_open=True)
    assert result.migrated_count == 0


def test_missing_owner_user_id_is_backfilled_from_user_uri() -> None:
    qdrant = _legacy_fixture(sparse=False)
    payload = qdrant.collections["legacy__context"]["points"]["1"]["payload"]
    payload["uri"] = "/user/alice/memories/a.md"
    payload.pop("owner_user_id")

    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)

    target_payload = qdrant.collections["current__context"]["points"][
        to_qdrant_point_id("1")
    ]["payload"]
    assert target_payload["owner_user_id"] == "alice"


def test_owner_user_id_mismatch_fails_closed() -> None:
    qdrant = _legacy_fixture(sparse=False)
    payload = qdrant.collections["legacy__context"]["points"]["1"]["payload"]
    payload["uri"] = "/user/alice/memories/a.md"
    payload["owner_user_id"] = "bob"

    with pytest.raises(MigrationError, match="owner_user_id"):
        _migration(qdrant).preflight()


def test_owner_normalization_resumes_legacy_target(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    payload = qdrant.collections["legacy__context"]["points"]["1"]["payload"]
    payload["uri"] = "/user/alice/memories/a.md"
    payload.pop("owner_user_id")

    migration = _migration(qdrant)
    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate._normalize_owner_user_id",
        lambda payload, *, uri, point_id, source_keys: None,
    )
    monkeypatch.setattr(
        QdrantMigration,
        "_validate_target_payload",
        staticmethod(lambda *args, **kwargs: None),
    )
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    monkeypatch.undo()

    _mark_current_target_building(qdrant, migration)
    plan = migration.preflight()
    result = migration.apply(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )

    assert result.migrated_count == 1
    assert qdrant.collections["current__context"]["points"][
        to_qdrant_point_id("1")
    ]["payload"]["owner_user_id"] == "alice"


def test_apply_requires_explicit_confirmation() -> None:
    qdrant = _legacy_fixture(sparse=False)

    with pytest.raises(MigrationError, match="confirm"):
        _migration(qdrant).apply()

    assert "current__context" not in qdrant.collections


def test_apply_requires_external_lock_before_requests() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()
    qdrant.requests.clear()

    with pytest.raises(MigrationError, match="lock"):
        migration.apply(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
        )

    assert qdrant.requests == []
    assert "current__context" not in qdrant.collections


def test_apply_rejects_cutting_over_target_state() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["migration_state"] = "cutting_over"
    marker["setup_complete"] = True
    target_before = copy.deepcopy(qdrant.collections[migration.target_collection])

    plan = _migration(qdrant).preflight()
    with pytest.raises(MigrationError, match="cutting_over"):
        _migration(qdrant).apply(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    assert qdrant.collections[migration.target_collection] == target_before


def test_target_metadata_collection_collision_is_rejected() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.add_collection(
        "current__context__openviking_meta",
        vectors={"meta": {"size": 1, "distance": "Dot"}},
    )

    with pytest.raises(MigrationError, match="metadata collection"):
        _migration(qdrant).preflight()


def test_missing_legacy_index_metadata_fails_closed() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context__openviking_meta"]["points"].pop(
        _legacy_index_metadata_id("legacy__context", "default")
    )

    with pytest.raises(MigrationError, match="no index documents"):
        _migration(qdrant).preflight()


def test_cli_json_plan_is_serializable() -> None:
    qdrant = _legacy_fixture(sparse=False)
    plan = _migration(qdrant).preflight()

    encoded = json.dumps(plan.to_dict(), sort_keys=True)

    assert '"source_count": 2' in encoded


def test_cli_apply_requires_reviewed_plan() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--url",
                "http://qdrant.invalid",
                "--source-collection",
                "legacy__context",
                "--target-collection",
                "current__context",
                "apply",
                "--confirm",
            ]
        )

    assert exc_info.value.code == 2


def test_reviewed_plan_json_round_trips(tmp_path) -> None:
    qdrant = _legacy_fixture(sparse=True)
    plan = _migration(qdrant, sparse_map={111: "hello", 222: "world"}).preflight()
    path = tmp_path / "preflight.json"
    path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

    loaded = _load_plan(str(path))

    assert loaded is not None
    assert loaded.to_dict() == plan.to_dict()


def test_default_legacy_metadata_collection_is_global() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["__openviking_meta"] = qdrant.collections.pop(
        "legacy__context__openviking_meta"
    )

    migration = QdrantMigration(
        client=qdrant,
        source_collection="legacy__context",
        target_collection="current__context",
        logical_collection="legacy/context",
        migration_id="mig-1",
    )

    assert migration.source_metadata_collection == "__openviking_meta"
    assert migration.preflight().source_count == 2


def test_cli_reports_invalid_sparse_map_without_traceback(tmp_path, capsys) -> None:
    sparse_map = tmp_path / "sparse.json"
    sparse_map.write_text("[]", encoding="utf-8")

    result = main(
        [
            "--url",
            "http://qdrant.invalid",
            "--source-collection",
            "legacy__context",
            "--target-collection",
            "current__context",
            "--logical-collection",
            "legacy/context",
            "--migration-id",
            "mig-1",
            "--timeout-seconds",
            "10",
            "--sparse-map",
            str(sparse_map),
            "preflight",
        ]
    )

    assert result == 2
    assert "qdrant migration failed" in capsys.readouterr().err


def test_acl_incomplete_records_require_explicit_acknowledgement() -> None:
    qdrant = _legacy_fixture(sparse=False)

    with pytest.raises(MigrationError, match="ACL"):
        _apply(_migration(qdrant), confirm=True)

    assert "current__context" not in qdrant.collections


def test_malformed_acl_records_complete_with_explicit_acknowledgement() -> None:
    qdrant = _legacy_fixture(sparse=False)
    for point in qdrant.collections["legacy__context"]["points"].values():
        point["payload"].update(
            {
                "acl_mode": "inherit",
                "acl_direct_grants": ["not-an-acl-token"],
                "acl_inherited_grants": [],
            }
        )
    migration = _migration(qdrant)
    plan = migration.preflight()
    assert plan.acl_incomplete_count == 2
    with pytest.raises(MigrationError, match="ACL"):
        migration.apply(confirm=True, plan=plan, lock_held=True)

    result = migration.apply(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    assert result.target_count == 2
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["acl_incomplete_count"] == 2


def test_malformed_logical_id_types_fail_before_target_mutation() -> None:
    for malformed in (True, 1.5, [], {}):
        qdrant = _legacy_fixture(sparse=False)
        qdrant.collections["legacy__context"]["points"]["1"]["payload"][
            "_openviking_original_id"
        ] = malformed
        with pytest.raises(MigrationError, match="logical ID"):
            _migration(qdrant).preflight()
        assert "current__context" not in qdrant.collections


def test_logical_id_uint64_boundaries_remain_supported() -> None:
    for logical_id in (0, 2**64 - 1, "550e8400-e29b-41d4-a716-446655440001"):
        qdrant = _legacy_fixture(sparse=False)
        source = qdrant.collections["legacy__context"]["points"]["1"]
        source["payload"]["_openviking_original_id"] = logical_id
        source["id"] = logical_id if isinstance(logical_id, int) else logical_id
        plan = _migration(qdrant).preflight()
        assert plan.source_count == 2


def test_reconcile_persists_independent_content_receipts_before_verify() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    result = migration.reconcile(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert result["migration_state"] == "building"
    assert marker["transformed_source_fingerprint"]
    assert marker["target_content_fingerprint"]
    assert marker["transformed_source_fingerprint"] == marker["target_content_fingerprint"]


def test_reconcile_persists_each_round_receipt_before_next_round(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    original = migration._reconcile_round
    snapshots = []
    marker_at_next_round = []

    def track_round(*, layout, schema, metadata, state):
        if snapshots:
            marker = qdrant.collections[migration.target_metadata_collection]["points"][
                to_qdrant_point_id("openviking:metadata")
            ]["payload"]
            marker_at_next_round.append(copy.deepcopy(marker))
        snapshot = original(
            layout=layout,
            schema=schema,
            metadata=metadata,
            state=state,
        )
        snapshots.append(snapshot)
        if len(snapshots) == 1:
            qdrant.collections["legacy__context"]["points"]["1"]["payload"][
                "name"
            ] = "changed"
        return snapshot

    monkeypatch.setattr(migration, "_reconcile_round", track_round)
    migration.reconcile(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    assert len(snapshots) >= 2
    assert marker_at_next_round
    first = snapshots[0]
    marker = marker_at_next_round[0]
    assert marker["source_count"] == first.source_count
    assert marker["source_fingerprint"] == first.fingerprint
    assert marker["acl_incomplete_count"] == first.acl_incomplete_count
    assert marker["sparse_term_count"] == first.sparse_term_count
    assert marker["sparse_term_fingerprint"] == first.sparse_term_fingerprint
    assert marker["target_count"] == first.source_count
    assert marker["transformed_source_fingerprint"] == first.transformed_source_fingerprint
    assert marker["target_content_fingerprint"] == first.target_content_fingerprint
    assert marker["migration_state"] == "building"
    assert marker["setup_complete"] is False


def test_reconcile_retains_first_round_receipt_after_later_failure(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    original = migration._reconcile_round
    snapshots = []

    def fail_later(*, layout, schema, metadata, state):
        if snapshots:
            raise RuntimeError("later round interrupted")
        snapshot = original(
            layout=layout,
            schema=schema,
            metadata=metadata,
            state=state,
        )
        snapshots.append(snapshot)
        qdrant.collections["legacy__context"]["points"]["1"]["payload"][
            "name"
        ] = "changed"
        return snapshot

    monkeypatch.setattr(migration, "_reconcile_round", fail_later)
    with pytest.raises(RuntimeError, match="later round interrupted"):
        migration.reconcile(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )
    assert len(snapshots) == 1
    first = snapshots[0]
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["source_fingerprint"] == first.fingerprint
    assert marker["transformed_source_fingerprint"] == first.transformed_source_fingerprint
    assert marker["target_content_fingerprint"] == first.target_content_fingerprint
    assert marker["target_count"] == first.source_count
    assert marker["migration_state"] == "failed"
    assert marker["setup_complete"] is False


def test_reconcile_updates_receipts_when_source_changes_across_rounds(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    original = migration._reconcile_round
    snapshots = []

    def change_each_round(*, layout, schema, metadata, state):
        snapshot = original(
            layout=layout,
            schema=schema,
            metadata=metadata,
            state=state,
        )
        snapshots.append(snapshot)
        if len(snapshots) == 1:
            qdrant.collections["legacy__context"]["points"]["1"]["payload"][
                "name"
            ] = f"round-{len(snapshots)}"
        return snapshot

    monkeypatch.setattr(migration, "_reconcile_round", change_each_round)
    migration.reconcile(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    assert len(snapshots) == 3
    assert snapshots[0].fingerprint != snapshots[-1].fingerprint
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["source_fingerprint"] == snapshots[-1].fingerprint
    assert marker["transformed_source_fingerprint"] == snapshots[-1].transformed_source_fingerprint
    assert marker["target_content_fingerprint"] == snapshots[-1].target_content_fingerprint


def test_cutover_reconcile_persists_receipts_without_opening_setup_gate(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    _set_marker_state(qdrant, migration, "cutting_over")
    original = migration._reconcile_round
    snapshots = []
    receipts = []

    def track_marker(marker, **kwargs):
        receipts.append(
            (
                marker.get("migration_state"),
                marker.get("setup_complete"),
                marker.get("target_content_fingerprint"),
            )
        )
        return original_write_marker(marker, **kwargs)

    def track_round(*, layout, schema, metadata, state):
        snapshot = original(
            layout=layout,
            schema=schema,
            metadata=metadata,
            state=state,
        )
        snapshots.append(snapshot)
        if len(snapshots) == 1:
            qdrant.collections["legacy__context"]["points"]["1"]["payload"][
                "name"
            ] = "changed"
        return snapshot

    original_write_marker = migration._write_marker
    monkeypatch.setattr(migration, "_write_marker", track_marker)
    monkeypatch.setattr(migration, "_reconcile_round", track_round)
    migration.reconcile(
        confirm=True,
        plan=plan,
        barrier_held=True,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    assert len(snapshots) >= 2
    assert len(receipts) >= 2
    assert all(state == "cutting_over" and setup is True for state, setup, _ in receipts)
    assert receipts[0][2] == snapshots[0].target_content_fingerprint


def test_source_mutation_between_preflight_and_apply_is_rejected(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()
    original_scan = migration._scan_source
    calls = 0

    def scan_source(*, layout, schema, manifest=None, point_callback=None):
        nonlocal calls
        snapshot = original_scan(
            layout=layout,
            schema=schema,
            manifest=manifest,
            point_callback=point_callback,
        )
        calls += 1
        if calls == 1:
            points = qdrant.collections["legacy__context"]["points"]
            points.pop("1")
            points["3"] = _point(3, 3, uri="/resources/c.md")
        return snapshot

    monkeypatch.setattr(migration, "_scan_source", scan_source)

    with pytest.raises(MigrationError, match="source changed"):
        migration.apply(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    assert "current__context" not in qdrant.collections


def test_apply_rejects_a_stale_plan() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()
    qdrant.collections["legacy__context"]["points"]["1"]["payload"]["name"] = "changed"

    with pytest.raises(MigrationError, match="stale"):
        migration.apply(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )

    assert "current__context" not in qdrant.collections


def test_incomplete_marker_is_rejected_before_copy() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker.pop("setup_complete")

    with pytest.raises(MigrationError, match="required fields"):
        _migration(qdrant).preflight()


def test_incompatible_target_metadata_layout_is_rejected() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    qdrant.collections["current__context__openviking_meta"]["config"]["vectors"] = {
        "meta": {"size": 2, "distance": "Dot"}
    }

    with pytest.raises(MigrationError, match="incompatible vector layout"):
        _migration(qdrant).preflight()


def test_source_vector_datatype_and_sparse_modifier_are_preserved() -> None:
    qdrant = _legacy_fixture(sparse=True)
    source_params = qdrant.collections["legacy__context"]["config"]["params"]
    source_params["vectors"]["vector"]["datatype"] = "float16"
    source_params["sparse_vectors"]["sparse_vector"]["modifier"] = "idf"

    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})
    plan = migration.preflight()

    assert plan.dense_datatype == "float16"
    assert plan.sparse_modifier == "idf"
    _apply(migration, confirm=True, allow_acl_fail_open=True)

    target_params = qdrant.collections["current__context"]["config"]["vectors"]
    assert target_params["vector"]["datatype"] == "float16"
    assert (
        qdrant.collections["current__context"]["config"]["sparse_vectors"][
            "sparse_vector"
        ]["modifier"]
        == "idf"
    )


def test_partial_index_setup_is_repaired_on_resume(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    original_write_indexes = migration._write_indexes
    failed = False

    def fail_once(schema, indexes, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected index failure")
        return original_write_indexes(schema, indexes, **kwargs)

    monkeypatch.setattr(migration, "_write_indexes", fail_once)
    with pytest.raises(RuntimeError, match="index failure"):
        _apply(migration, confirm=True, allow_acl_fail_open=True)

    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["setup_complete"] is False
    assert qdrant.collections["current__context"]["indexes"] == {}

    result = _apply(_migration(qdrant), confirm=True, allow_acl_fail_open=True)

    assert result.target_count == 2
    assert qdrant.collections["current__context"]["indexes"]
    resumed_marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert resumed_marker["setup_complete"] is True


def test_existing_target_records_absent_from_source_remain_for_reconcile() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _mark_current_target_building(qdrant, migration)
    extra_id = to_qdrant_point_id("extra")
    qdrant.collections["current__context"]["points"][extra_id] = _point(
        extra_id,
        "extra",
        uri="/resources/extra.md",
    )

    plan = _migration(qdrant).preflight()

    assert plan.target_absent is False
    with pytest.raises(MigrationError, match="extras"):
        _migration(qdrant).apply(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )


def test_existing_target_extra_requires_deterministic_original_id() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    qdrant.collections["current__context"]["points"]["extra"] = _point(
        "extra",
        "extra",
        uri="/resources/extra.md",
    )

    with pytest.raises(MigrationError, match="deterministic"):
        _migration(qdrant).preflight()


def test_sparse_only_source_record_is_preserved() -> None:
    qdrant = _legacy_fixture(sparse=True)
    qdrant.collections["legacy__context"]["points"]["1"]["vector"].pop("vector")
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})

    _apply(migration, confirm=True, allow_acl_fail_open=True)

    target_point = qdrant.collections["current__context"]["points"][
        to_qdrant_point_id("1")
    ]
    assert "vector" not in target_point["vector"]
    assert target_point["vector"]["sparse_vector"]["indices"]


def test_multiple_named_sparse_vectors_require_selection() -> None:
    qdrant = _legacy_fixture(sparse=True)
    qdrant.collections["legacy__context"]["config"]["params"]["sparse_vectors"] = {
        "sparse_vector": {},
        "other_sparse": {},
    }

    with pytest.raises(SparseMigrationError, match="multiple named sparse"):
        _migration(qdrant, sparse_map={111: "hello", 222: "world"}).preflight()


def test_unsupported_manhattan_distance_fails_closed() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context"]["config"]["params"]["vectors"]["vector"][
        "distance"
    ] = "Manhattan"

    with pytest.raises(MigrationError, match="unsupported Qdrant distance"):
        _migration(qdrant).preflight()


def test_malformed_legacy_schema_fields_fail_during_preflight() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context__openviking_meta"]["points"][
        _legacy_collection_metadata_id("legacy__context")
    ][
        "payload"
    ]["meta"]["Fields"] = ["malformed"]

    with pytest.raises(MigrationError, match="malformed field"):
        _migration(qdrant).preflight()


def test_malformed_legacy_index_metadata_fails_during_preflight() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context__openviking_meta"]["points"][
        _legacy_index_metadata_id("legacy__context", "default")
    ][
        "payload"
    ]["meta"]["ScalarIndex"] = "malformed"

    with pytest.raises(MigrationError, match="ScalarIndex is malformed"):
        _migration(qdrant).preflight()


def test_metadata_count_must_match_filtered_pagination() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.count_overrides["legacy__context__openviking_meta"] = 3

    with pytest.raises(MigrationError, match="metadata count"):
        _migration(qdrant).preflight()


def test_acl_fields_must_have_valid_types_and_grants() -> None:
    qdrant = _legacy_fixture(sparse=False)
    for point in qdrant.collections["legacy__context"]["points"].values():
        point["payload"].update(
            {
                "acl_mode": "inherit",
                "acl_direct_grants": ["not-an-acl-token"],
                "acl_inherited_grants": [],
            }
        )

    plan = _migration(qdrant).preflight()

    assert plan.acl_incomplete_count == 2
    with pytest.raises(MigrationError, match="ACL"):
        _apply(_migration(qdrant), confirm=True)


def test_acl_none_mode_with_grants_is_incomplete() -> None:
    qdrant = _legacy_fixture(sparse=False)
    for point in qdrant.collections["legacy__context"]["points"].values():
        point["payload"].update(
            {
                "acl_mode": "none",
                "acl_direct_grants": ["1:user:alice"],
                "acl_inherited_grants": [],
            }
        )

    plan = _migration(qdrant).preflight()

    assert plan.acl_incomplete_count == 2
    with pytest.raises(MigrationError, match="ACL"):
        _apply(_migration(qdrant), confirm=True)


@pytest.mark.parametrize("enabled", [False, True])
def test_legacy_acl_boolean_requires_explicit_acknowledgement(enabled: bool) -> None:
    qdrant = _legacy_fixture(sparse=False)
    for point in qdrant.collections["legacy__context"]["points"].values():
        point["payload"].update(
            {
                "acl_enabled": enabled,
                "acl_direct_grants": ["1:user:alice"] if enabled else [],
                "acl_inherited_grants": [],
            }
        )
    source_before = copy.deepcopy(qdrant.collections["legacy__context"])
    migration = _migration(qdrant)
    plan = migration.preflight()

    assert plan.acl_incomplete_count == 2
    with pytest.raises(MigrationError, match="ACL"):
        migration.apply(confirm=True, plan=plan, lock_held=True)
    assert "current__context" not in qdrant.collections

    migration.apply(
        confirm=True, plan=plan, lock_held=True, allow_acl_fail_open=True
    )
    assert qdrant.collections["legacy__context"] == source_before
    for point in qdrant.collections["current__context"]["points"].values():
        assert point["payload"]["acl_enabled"] is enabled
        assert "acl_mode" not in point["payload"]


@pytest.mark.parametrize("mode", ["none", "inherit", "restricted"])
def test_valid_current_acl_modes_are_complete(mode: str) -> None:
    qdrant = _legacy_fixture(sparse=False)
    for point in qdrant.collections["legacy__context"]["points"].values():
        point["payload"].update(
            {
                "acl_mode": mode,
                "acl_direct_grants": ["1:user:alice"] if mode != "none" else [],
                "acl_inherited_grants": [],
            }
        )

    result = _apply(_migration(qdrant), confirm=True)

    assert result.target_count == 2
    for point in qdrant.collections["current__context"]["points"].values():
        assert point["payload"]["acl_mode"] == mode


@pytest.mark.parametrize("mode", [None, True, [], "invalid"])
def test_invalid_acl_mode_is_not_masked_by_legacy_boolean(mode) -> None:
    qdrant = _legacy_fixture(sparse=False)
    for point in qdrant.collections["legacy__context"]["points"].values():
        point["payload"].update(
            {
                "acl_enabled": False,
                "acl_mode": mode,
                "acl_direct_grants": [],
                "acl_inherited_grants": [],
            }
        )

    assert _migration(qdrant).preflight().acl_incomplete_count == 2
    with pytest.raises(MigrationError, match="ACL"):
        _apply(_migration(qdrant), confirm=True)
    assert "current__context" not in qdrant.collections


def test_target_metadata_rejects_foreign_points() -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    qdrant.collections["current__context__openviking_meta"]["points"]["foreign"] = {
        "id": "foreign",
        "vector": {"meta": [0.0]},
        "payload": {"term": "foreign", "index": 7},
    }

    with pytest.raises(SparseMigrationError, match="invalid sparse point"):
        _migration(qdrant, sparse_map={111: "hello", 222: "world"}).preflight()


def test_target_sparse_dictionary_requires_stable_index_for_all_terms() -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    term = "foreign"
    qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id(f"openviking:sparse:{term}")
    ] = {
        "id": to_qdrant_point_id(f"openviking:sparse:{term}"),
        "vector": {"meta": [0.0]},
        "payload": {
            "_openviking_sparse_term": True,
            "term": term,
            "index": 999,
        },
    }

    with pytest.raises(SparseMigrationError, match="invalid sparse point"):
        _migration(qdrant, sparse_map={111: "hello", 222: "world"}).preflight()


def test_marker_fingerprint_change_after_preflight_is_rejected(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    _apply(_migration(qdrant), confirm=True, allow_acl_fail_open=True)
    migration = _migration(qdrant)
    _mark_current_target_building(qdrant, migration)
    plan = migration.preflight()
    original_write_indexes = migration._write_indexes

    def write_indexes(schema, indexes, **kwargs):
        original_write_indexes(schema, indexes, **kwargs)
        qdrant.collections[migration.target_metadata_collection]["points"][
            to_qdrant_point_id("openviking:metadata")
        ]["payload"]["source_fingerprint"] = "changed-after-preflight"

    monkeypatch.setattr(migration, "_write_indexes", write_indexes)

    with pytest.raises(MigrationError, match="fingerprint changed"):
        migration.apply(
            confirm=True,
            plan=plan,
            allow_acl_fail_open=True,
            lock_held=True,
        )


def test_index_409_without_physical_index_fails_closed() -> None:
    qdrant = _legacy_fixture(sparse=False)
    original_request = qdrant.request

    def reject_indexes(method, path, body=None, *, params=None):
        if method == "PUT" and path.endswith("/current__context/index"):
            raise _FakeHttpError(409)
        return original_request(method, path, body, params=params)

    qdrant.request = reject_indexes  # type: ignore[method-assign]

    with pytest.raises(MigrationError, match="missing payload index"):
        _apply(_migration(qdrant), confirm=True, allow_acl_fail_open=True)

    marker = qdrant.collections["current__context__openviking_meta"]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["setup_complete"] is False


def test_target_creation_race_preserves_competing_metadata_marker() -> None:
    qdrant = _legacy_fixture(sparse=False)
    original_request = qdrant.request

    def race_on_target(method, path, body=None, *, params=None):
        if method == "PUT" and path.endswith("/current__context"):
            original_request(method, path, body, params=params)
            raise _FakeHttpError(409)
        return original_request(method, path, body, params=params)

    qdrant.request = race_on_target  # type: ignore[method-assign]

    with pytest.raises(MigrationError, match="without an owned migration marker"):
        _apply(_migration(qdrant), confirm=True, allow_acl_fail_open=True)

    assert "current__context" in qdrant.collections
    assert "current__context__openviking_meta" not in qdrant.collections


def test_completion_marker_write_is_verified(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    original_write_points = migration._write_points

    def drop_completion_marker(collection, points, **kwargs):
        if collection == migration.target_metadata_collection and any(
            point.get("payload", {}).get("setup_complete") is True
            for point in points
        ):
            return
        return original_write_points(collection, points, **kwargs)

    monkeypatch.setattr(migration, "_write_points", drop_completion_marker)

    with pytest.raises(MigrationError, match="completion marker"):
        _apply(migration, confirm=True, allow_acl_fail_open=True)

    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["setup_complete"] is False


def test_final_target_identity_readback_rejects_dropped_points() -> None:
    qdrant = _legacy_fixture(sparse=False)
    original_request = qdrant.request

    def drop_one_data_point(method, path, body=None, *, params=None):
        if method == "PUT" and path.endswith("/current__context/points"):
            body = copy.deepcopy(body)
            body["points"] = body["points"][:1]
        return original_request(method, path, body, params=params)

    qdrant.request = drop_one_data_point  # type: ignore[method-assign]

    with pytest.raises(MigrationError, match="target records differ"):
        _apply(_migration(qdrant), confirm=True, allow_acl_fail_open=True)


def test_empty_sparse_only_source_record_fails_closed() -> None:
    qdrant = _legacy_fixture(sparse=True)
    point = qdrant.collections["legacy__context"]["points"]["1"]
    point["vector"].pop("vector")
    point["vector"]["sparse_vector"] = {"indices": [], "values": []}

    with pytest.raises(SparseMigrationError, match="no entries"):
        _migration(qdrant, sparse_map={111: "hello", 222: "world"}).preflight()


def test_dense_vector_override_survives_resume_preflight() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context"]["config"]["params"]["vectors"] = {
        "embedding": {"size": 2, "distance": "Cosine"}
    }
    for point in qdrant.collections["legacy__context"]["points"].values():
        point["vector"]["embedding"] = point["vector"].pop("vector")
    migration = _migration(qdrant, dense_vector_name="embedding")

    _apply(migration, confirm=True, allow_acl_fail_open=True)

    assert migration.preflight().dense_vector_name == "embedding"


def test_unnamed_dense_vector_rejects_a_name_override() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context"]["config"]["params"]["vectors"] = {
        "size": 2,
        "distance": "Cosine",
    }
    for point in qdrant.collections["legacy__context"]["points"].values():
        point["vector"][""] = point["vector"].pop("vector")

    with pytest.raises(MigrationError, match="unnamed source dense vector"):
        _migration(qdrant, dense_vector_name="embedding").preflight()


def test_multiple_named_dense_vectors_require_selection() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context"]["config"]["params"]["vectors"] = {
        "vector": {"size": 2, "distance": "Cosine"},
        "image": {"size": 2, "distance": "Cosine"},
    }
    for point in qdrant.collections["legacy__context"]["points"].values():
        point["vector"]["image"] = [0.0, 1.0]

    with pytest.raises(MigrationError, match="multiple named dense vectors"):
        _migration(qdrant).preflight()


def test_sparse_map_rejects_non_string_terms_and_fractional_indexes() -> None:
    with pytest.raises(SparseMigrationError, match="non-empty string"):
        _migration(_legacy_fixture(sparse=False), sparse_map={"111": None})
    qdrant = _legacy_fixture(sparse=True)
    qdrant.collections["legacy__context"]["points"]["1"]["vector"]["sparse_vector"][
        "indices"
    ] = [111.9]
    with pytest.raises(SparseMigrationError, match="integer"):
        _migration(qdrant, sparse_map={111: "hello", 222: "world"}).preflight()


def test_sparse_map_accepts_numeric_looking_reverse_terms() -> None:
    qdrant = _legacy_fixture(sparse=True)

    plan = _migration(qdrant, sparse_map={"111": 111, "world": 222}).preflight()

    assert plan.sparse_term_count == 2


def test_fractional_level_fails_closed() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context"]["points"]["1"]["payload"]["level"] = 1.9

    with pytest.raises(MigrationError, match="valid level"):
        _migration(qdrant).preflight()


def test_unnamed_dense_vector_with_named_sparse_vector_is_read() -> None:
    qdrant = _legacy_fixture(sparse=True)
    for point in qdrant.collections["legacy__context"]["points"].values():
        vectors = point["vector"]
        point["vector"] = {
            "": vectors.pop("vector"),
            **vectors,
        }
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})

    _apply(migration, confirm=True, allow_acl_fail_open=True)

    target = qdrant.collections["current__context"]["points"]
    assert target[to_qdrant_point_id("1")]["vector"]["vector"] == [1.0, 0.0]


def test_source_metadata_dimension_must_match_physical_layout() -> None:
    qdrant = _legacy_fixture(sparse=False)
    vector_field = next(
        field
        for field in qdrant.collections["legacy__context__openviking_meta"]["points"][
            _legacy_collection_metadata_id("legacy__context")
        ]["payload"]["meta"]["Fields"]
        if field["FieldName"] == "vector"
    )
    vector_field["Dim"] = 3

    with pytest.raises(MigrationError, match="dimension"):
        _migration(qdrant).preflight()


def test_source_metadata_sparse_declaration_must_match_physical_layout() -> None:
    qdrant = _legacy_fixture(sparse=False)
    qdrant.collections["legacy__context__openviking_meta"]["points"][
        _legacy_collection_metadata_id("legacy__context")
    ][
        "payload"
    ]["meta"]["Fields"].append(
        {"FieldName": "sparse_vector", "FieldType": "sparse_vector"}
    )

    with pytest.raises(MigrationError, match="no sparse vectors"):
        _migration(qdrant).preflight()


def test_source_metadata_sparse_name_must_match_physical_layout() -> None:
    qdrant = _legacy_fixture(sparse=True)
    sparse_field = next(
        field
        for field in qdrant.collections["legacy__context__openviking_meta"]["points"][
            _legacy_collection_metadata_id("legacy__context")
        ]["payload"]["meta"]["Fields"]
        if field["FieldName"] == "sparse_vector"
    )
    sparse_field["FieldName"] = "other_sparse"

    with pytest.raises(MigrationError, match="sparse vector metadata name"):
        _migration(qdrant, sparse_map={111: "hello", 222: "world"}).preflight()


def test_all_migration_collection_names_must_be_distinct() -> None:
    qdrant = _legacy_fixture(sparse=False)

    with pytest.raises(ValueError, match="pairwise distinct"):
        QdrantMigration(
            client=qdrant,
            source_collection="legacy__context",
            target_collection="current__context",
            source_metadata_collection="legacy__context__openviking_meta",
            target_metadata_collection="current__context",
            logical_collection="legacy/context",
            migration_id="mig-1",
        )


def _set_marker_state(qdrant: FakeQdrant, migration: QdrantMigration, state: str) -> None:
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["migration_state"] = state
    marker["setup_complete"] = state in {"ready", "cutting_over", "active", "retained"}


def test_verify_detects_dense_vector_value_mismatch_even_when_fingerprint_matches() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    target_id = to_qdrant_point_id("1")
    qdrant.collections[migration.target_collection]["points"][str(target_id)]["vector"][
        "vector"
    ] = [9.0, 9.0]

    with pytest.raises(MigrationError, match="vector"):
        migration.verify(
            plan=plan,
            allow_acl_fail_open=True,
            confirm=True,
            lock_held=True,
        )


def test_verify_detects_sparse_vector_value_and_name_mismatch() -> None:
    qdrant = _legacy_fixture(sparse=True)
    migration = _migration(qdrant, sparse_map={111: "hello", 222: "world"})
    plan = migration.preflight()
    migration.prepare(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    target_id = to_qdrant_point_id("1")
    target_vector = qdrant.collections[migration.target_collection]["points"][str(target_id)][
        "vector"
    ]
    target_vector["sparse_vector"]["values"] = [0.8]

    with pytest.raises(MigrationError, match="sparse vector"):
        migration.verify(
            plan=plan,
            allow_acl_fail_open=True,
            confirm=True,
            lock_held=True,
        )

    target_vector["sparse_vector"] = target_vector.pop("sparse_vector")
    target_vector["other_sparse"] = target_vector.pop("sparse_vector")
    with pytest.raises(MigrationError, match="vector names"):
        migration.verify(
            plan=plan,
            allow_acl_fail_open=True,
            confirm=True,
            lock_held=True,
        )


@pytest.mark.parametrize("field,value", [("acl_enabled", 1), ("acl_mode", "restricted")])
def test_verify_detects_payload_and_acl_mismatch(field: str, value) -> None:
    qdrant = _legacy_fixture(sparse=False)
    for point in qdrant.collections["legacy__context"]["points"].values():
        point["payload"].update(
            {
                "acl_enabled": False,
                "acl_mode": "none",
                "acl_direct_grants": [],
                "acl_inherited_grants": [],
            }
        )
    migration, plan = _prepare_reconcile(qdrant)
    target_id = to_qdrant_point_id("1")
    target_payload = qdrant.collections[migration.target_collection]["points"][str(target_id)][
        "payload"
    ]
    target_payload[field] = value

    with pytest.raises(MigrationError, match="ACL"):
        migration.verify(
            plan=plan,
            allow_acl_fail_open=True,
            confirm=True,
            lock_held=True,
        )


def test_verify_detects_target_extra_and_missing_id() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    points = qdrant.collections[migration.target_collection]["points"]
    points.pop(str(to_qdrant_point_id("1")))
    points["extra"] = copy.deepcopy(next(iter(points.values())))
    points["extra"]["id"] = "extra"

    with pytest.raises(MigrationError, match="target"):
        migration.verify(
            plan=plan,
            allow_acl_fail_open=True,
            confirm=True,
            lock_held=True,
        )


def test_verify_requires_matching_indexes_metadata_and_marker_layout() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["indexes"]["default"]["Description"] = "tampered"

    with pytest.raises(MigrationError, match="index"):
        migration.verify(
            plan=plan,
            allow_acl_fail_open=True,
            confirm=True,
            lock_held=True,
        )


def test_non_cutover_verify_changes_building_to_ready() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)

    result = migration.verify(
        plan=plan,
        allow_acl_fail_open=True,
        confirm=True,
        lock_held=True,
    )

    assert result["migration_state"] == "ready"
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["migration_state"] == "ready"
    assert marker["setup_complete"] is True


def test_final_verify_does_not_reset_cutting_over_to_ready() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    _set_marker_state(qdrant, migration, "cutting_over")

    result = migration.verify(
        plan=plan,
        allow_acl_fail_open=True,
        final=True,
        barrier_held=True,
        confirm=True,
        lock_held=True,
    )

    assert result["migration_state"] == "cutting_over"
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["migration_state"] == "cutting_over"
    assert marker["setup_complete"] is True


def test_final_verify_requires_an_explicit_barrier() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    _set_marker_state(qdrant, migration, "cutting_over")
    marker_before = copy.deepcopy(
        qdrant.collections[migration.target_metadata_collection]["points"][
            to_qdrant_point_id("openviking:metadata")
        ]["payload"]
    )

    with pytest.raises(MigrationError, match="barrier"):
        migration.verify(
            plan=plan,
            allow_acl_fail_open=True,
            final=True,
            confirm=True,
            lock_held=True,
        )

    marker_after = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker_after == marker_before


def test_verify_persists_independent_canonical_content_receipts() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)

    result = migration.verify(
        plan=plan,
        allow_acl_fail_open=True,
        confirm=True,
        lock_held=True,
    )

    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    source_receipt = result["transformed_source_fingerprint"]
    target_receipt = result["target_content_fingerprint"]
    assert source_receipt == target_receipt
    assert source_receipt == marker["transformed_source_fingerprint"]
    assert target_receipt == marker["target_content_fingerprint"]
    assert source_receipt != plan.source_fingerprint
    assert marker["transformed_source_fingerprint"] != marker["source_fingerprint"]


@pytest.mark.parametrize("drift", ["metadata", "sparse_map", "layout", "indexes"])
def test_verify_revalidates_pinned_inputs_after_exact_audit(
    monkeypatch,
    drift: str,
) -> None:
    sparse = drift == "sparse_map"
    qdrant = _legacy_fixture(sparse=sparse)
    migration = _migration(
        qdrant,
        sparse_map={111: "hello", 222: "world"} if sparse else None,
    )
    plan = migration.preflight()
    migration.prepare(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    original_validate = migration._validate_final_target

    def mutate_after_audit(**kwargs):
        target_count = original_validate(**kwargs)
        if drift == "metadata":
            metadata_point = qdrant.collections[
                migration.source_metadata_collection
            ]["points"][_legacy_collection_metadata_id("legacy__context")]
            metadata_point["payload"]["meta"]["CollectionName"] = "changed"
        elif drift == "sparse_map":
            migration._sparse_map[7] = "changed"
        elif drift == "layout":
            qdrant.collections[migration.target_collection]["config"]["vectors"][
                "vector"
            ]["distance"] = "Dot"
        else:
            qdrant.collections[migration.target_collection]["payload_schema"].pop(
                "account_id"
            )
        return target_count

    monkeypatch.setattr(migration, "_validate_final_target", mutate_after_audit)
    marker_before = copy.deepcopy(
        qdrant.collections[migration.target_metadata_collection]["points"][
            to_qdrant_point_id("openviking:metadata")
        ]["payload"]
    )
    writes_before = len(
        [
            request
            for request in qdrant.requests
            if request[0] == "PUT" and request[1].endswith("/points")
        ]
    )

    with pytest.raises(MigrationError, match="metadata|sparse map|layout|index"):
        migration.verify(
            plan=plan,
            allow_acl_fail_open=True,
            confirm=True,
            lock_held=True,
        )

    marker_after = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    writes_after = len(
        [
            request
            for request in qdrant.requests
            if request[0] == "PUT" and request[1].endswith("/points")
        ]
    )
    assert marker_after == marker_before
    assert writes_after == writes_before


def test_verify_revalidates_readiness_after_exact_audit(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _prepare_reconcile(qdrant)
    original_wait = migration._wait_collection_ready
    calls = 0

    def fail_on_post_audit(collection, *, payload_fields=None):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise MigrationError("readiness drift after exact audit")
        return original_wait(collection, payload_fields=payload_fields)

    monkeypatch.setattr(migration, "_wait_collection_ready", fail_on_post_audit)
    marker_before = copy.deepcopy(
        qdrant.collections[migration.target_metadata_collection]["points"][
            to_qdrant_point_id("openviking:metadata")
        ]["payload"]
    )

    with pytest.raises(MigrationError, match="readiness"):
        migration.verify(
            plan=plan,
            allow_acl_fail_open=True,
            confirm=True,
            lock_held=True,
        )

    marker_after = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker_after == marker_before


def test_sparse_index_datatype_is_persisted_and_verified() -> None:
    qdrant = _legacy_fixture(sparse=True, sparse_datatype="float16")
    migration = _migration(
        qdrant,
        sparse_map={111: "hello", 222: "world"},
    )
    plan = migration.preflight()
    assert plan.sparse_datatype == "float16"

    migration.prepare(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    sparse_config = qdrant.collections[migration.target_collection]["config"][
        "sparse_vectors"
    ]["sparse_vector"]
    assert sparse_config["index"]["datatype"] == "float16"
    migration.backfill(
        confirm=True,
        plan=plan,
        allow_acl_fail_open=True,
        lock_held=True,
    )
    sparse_config["index"]["datatype"] = "float32"

    with pytest.raises(MigrationError, match="layout|datatype"):
        migration.verify(
            plan=plan,
            allow_acl_fail_open=True,
            confirm=True,
            lock_held=True,
        )


@pytest.mark.parametrize("state", ["active", "retained", "rolled_back"])
def test_active_retained_and_rolled_back_targets_reject_mutating_reruns(state: str) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, state)
    plan = migration.preflight()
    marker_before = copy.deepcopy(
        qdrant.collections[migration.target_metadata_collection]["points"][
            to_qdrant_point_id("openviking:metadata")
        ]["payload"]
    )

    result = migration.verify(plan=plan, allow_acl_fail_open=True)

    assert result["migration_state"] == state
    marker_after = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker_after == marker_before


@pytest.mark.parametrize("state", ["active", "retained", "rolled_back"])
def test_read_only_state_target_count_mismatch_does_not_write(state: str) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, state)
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    marker["target_count"] = 1
    plan = migration.preflight()
    marker_before = copy.deepcopy(marker)
    writes_before = len(
        [
            request
            for request in qdrant.requests
            if request[0] == "PUT" and request[1].endswith("/points")
        ]
    )

    with pytest.raises(MigrationError, match="target count"):
        migration.verify(plan=plan, allow_acl_fail_open=True)

    assert marker == marker_before
    writes_after = len(
        [
            request
            for request in qdrant.requests
            if request[0] == "PUT" and request[1].endswith("/points")
        ]
    )
    assert writes_after == writes_before


def test_ready_verify_rerun_with_same_fingerprint_does_not_write_marker() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    plan = migration.preflight()
    writes_before = len(
        [
            request
            for request in qdrant.requests
            if request[0] == "PUT" and request[1].endswith("/points")
        ]
    )

    result = migration.verify(plan=plan, allow_acl_fail_open=True)

    assert result["migration_state"] == "ready"
    writes_after = len(
        [
            request
            for request in qdrant.requests
            if request[0] == "PUT" and request[1].endswith("/points")
        ]
    )
    assert writes_after == writes_before


@pytest.mark.parametrize("state", ["active", "retained", "rolled_back"])
def test_read_only_state_mismatch_does_not_write_marker(state: str) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, state)
    plan = migration.preflight()
    target = qdrant.collections[migration.target_collection]["points"][
        to_qdrant_point_id("1")
    ]
    target["payload"]["name"] = "tampered"
    marker_before = copy.deepcopy(
        qdrant.collections[migration.target_metadata_collection]["points"][
            to_qdrant_point_id("openviking:metadata")
        ]["payload"]
    )
    writes_before = len(
        [
            request
            for request in qdrant.requests
            if request[0] == "PUT" and request[1].endswith("/points")
        ]
    )

    with pytest.raises(MigrationError, match="payload"):
        migration.verify(plan=plan, allow_acl_fail_open=True)

    marker_after = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker_after == marker_before
    writes_after = len(
        [
            request
            for request in qdrant.requests
            if request[0] == "PUT" and request[1].endswith("/points")
        ]
    )
    assert writes_after == writes_before


def test_verify_rejects_non_string_physical_target_id() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    plan = migration.preflight()
    target = qdrant.collections[migration.target_collection]["points"][
        to_qdrant_point_id("1")
    ]
    target["id"] = 1

    with pytest.raises(MigrationError, match="canonical UUID"):
        migration.verify(plan=plan, allow_acl_fail_open=True)


class _LifecycleHooks:
    def __init__(
        self,
        *,
        accepted_writes: bool = False,
        fail: str | None = None,
        on_drain=None,
        on_assert_target_not_served=None,
    ) -> None:
        self.events: list[str] = []
        self.accepted_writes = accepted_writes
        self.fail = fail
        self.on_drain = on_drain
        self.on_assert_target_not_served = on_assert_target_not_served
        self.assert_target_not_served_calls = 0

    def _call(self, name: str) -> None:
        self.events.append(name)
        if name == "drain_legacy_writes" and self.on_drain is not None:
            self.on_drain()
        if self.fail == name:
            raise RuntimeError(f"{name} failed")

    def drain_legacy_writes(self, migration) -> None:
        self._call("drain_legacy_writes")

    def remove_legacy_from_serving_path(self, migration) -> None:
        self._call("remove_legacy_from_serving_path")

    def rollout_current(self, migration) -> None:
        self._call("rollout_current")

    def wait_current_ready(self, migration) -> None:
        self._call("wait_current_ready")

    def smoke_current_read_only(self, migration) -> None:
        self._call("smoke_current_read_only")

    def remove_current_from_serving_path(self, migration) -> None:
        self._call("remove_current_from_serving_path")

    def restore_legacy(self, migration) -> None:
        self._call("restore_legacy")

    def verify_legacy_read_path(self, migration) -> None:
        self._call("verify_legacy_read_path")

    def current_target_has_accepted_writes(self, migration) -> bool:
        self._call("current_target_has_accepted_writes")
        return self.accepted_writes

    def assert_target_not_served(self, migration) -> None:
        self._call("assert_target_not_served")
        self.assert_target_not_served_calls += 1
        if self.on_assert_target_not_served is not None:
            self.on_assert_target_not_served()


class _PartialRolloutHooks(_LifecycleHooks):
    def __init__(self, *, remove_serving: bool) -> None:
        super().__init__()
        self.serving = False
        self.wait_calls = 0
        self.remove_serving = remove_serving

    def rollout_current(self, migration) -> None:
        self._call("rollout_current")
        self.serving = True

    def wait_current_ready(self, migration) -> None:
        self._call("wait_current_ready")
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise RuntimeError("wait_current_ready failed")

    def remove_current_from_serving_path(self, migration) -> None:
        self._call("remove_current_from_serving_path")
        if self.remove_serving:
            self.serving = False

    def assert_target_not_served(self, migration) -> None:
        self._call("assert_target_not_served")
        self.assert_target_not_served_calls += 1
        if self.serving:
            raise RuntimeError("target still served")


def _ready_migration(qdrant: FakeQdrant) -> tuple[QdrantMigration, object]:
    migration, plan = _prepare_reconcile(qdrant)
    migration.verify(
        plan=plan,
        allow_acl_fail_open=True,
        confirm=True,
        lock_held=True,
    )
    return migration, migration.preflight()


def test_cutover_requires_ready_target_and_barrier() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _ready_migration(qdrant)
    hooks = _LifecycleHooks()

    with pytest.raises(MigrationError, match="barrier"):
        migration.cutover(
            confirm=True,
            plan=plan,
            barrier_held=False,
            lock_held=True,
            hooks=hooks,
        )

    assert qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]["migration_state"] == "ready"


def test_cutover_drains_legacy_before_final_source_snapshot() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _ready_migration(qdrant)

    def mutate_after_drain() -> None:
        qdrant.collections[migration.source_collection]["points"]["1"]["payload"][
            "name"
        ] = "drained"

    hooks = _LifecycleHooks(on_drain=mutate_after_drain)
    result = migration.cutover(
        confirm=True,
        plan=plan,
        barrier_held=True,
        lock_held=True,
        hooks=hooks,
        allow_acl_fail_open=True,
    )

    assert result["migration_state"] == "active"
    assert qdrant.collections[migration.target_collection]["points"][
        to_qdrant_point_id("1")
    ]["payload"]["name"] == "drained"
    assert hooks.events.index("drain_legacy_writes") < hooks.events.index(
        "remove_legacy_from_serving_path"
    )


def test_cutover_removes_old_serving_path_before_current_rollout() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _ready_migration(qdrant)
    hooks = _LifecycleHooks()

    migration.cutover(
        confirm=True,
        plan=plan,
        barrier_held=True,
        lock_held=True,
        hooks=hooks,
        allow_acl_fail_open=True,
    )

    assert hooks.events.index("remove_legacy_from_serving_path") < hooks.events.index(
        "rollout_current"
    )


def test_cutover_readiness_and_smoke_are_read_only() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _ready_migration(qdrant)
    hooks = _LifecycleHooks()
    writes_before_hooks: list[int] = []

    def observe_readiness(migration) -> None:
        writes_before_hooks.append(
            len([request for request in qdrant.requests if request[0] in {"PUT", "DELETE"}])
        )
        hooks._call("wait_current_ready")

    hooks.wait_current_ready = observe_readiness
    migration.cutover(
        confirm=True,
        plan=plan,
        barrier_held=True,
        lock_held=True,
        hooks=hooks,
        allow_acl_fail_open=True,
    )

    assert writes_before_hooks
    assert "smoke_current_read_only" in hooks.events


def test_cutover_failure_leaves_barrier_held_and_cutting_over_marker() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _ready_migration(qdrant)
    hooks = _LifecycleHooks(fail="wait_current_ready")

    with pytest.raises(RuntimeError, match="wait_current_ready"):
        migration.cutover(
            confirm=True,
            plan=plan,
            barrier_held=True,
            lock_held=True,
            hooks=hooks,
            allow_acl_fail_open=True,
        )

    assert qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]["migration_state"] == "cutting_over"


def test_rollback_requires_barrier_and_no_accepted_target_writes() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "cutting_over")
    hooks = _LifecycleHooks()

    with pytest.raises(MigrationError, match="barrier"):
        migration.rollback(
            confirm=True,
            barrier_held=False,
            lock_held=True,
            no_current_format_writes_accepted=True,
            hooks=hooks,
        )
    with pytest.raises(MigrationError, match="no-current-format"):
        migration.rollback(
            confirm=True,
            barrier_held=True,
            lock_held=True,
            no_current_format_writes_accepted=False,
            hooks=hooks,
        )


def test_rollback_restores_legacy_and_marks_target_rolled_back() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "cutting_over")
    hooks = _LifecycleHooks()

    result = migration.rollback(
        confirm=True,
        barrier_held=True,
        lock_held=True,
        no_current_format_writes_accepted=True,
        hooks=hooks,
    )

    assert result["migration_state"] == "rolled_back"
    assert hooks.events.index("remove_current_from_serving_path") < hooks.events.index(
        "restore_legacy"
    )
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["migration_state"] == "rolled_back"
    assert marker["setup_complete"] is False
    assert migration.target_collection in qdrant.collections
    assert migration.source_collection in qdrant.collections


@pytest.mark.parametrize(
    ("barrier_held", "accepted_writes"),
    [(False, False), (True, True)],
)
def test_rollback_refuses_after_target_write_or_released_barrier(
    barrier_held: bool,
    accepted_writes: bool,
) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "active")
    hooks = _LifecycleHooks(accepted_writes=accepted_writes)

    with pytest.raises(MigrationError):
        migration.rollback(
            confirm=True,
            barrier_held=barrier_held,
            lock_held=True,
            no_current_format_writes_accepted=True,
            hooks=hooks,
        )


def test_retire_refuses_a_serving_target() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "active")
    plan = migration.preflight()
    hooks = _LifecycleHooks(fail="assert_target_not_served")

    with pytest.raises(RuntimeError, match="assert_target_not_served"):
        migration.retire(
            confirm=True,
            plan=plan,
            lock_held=True,
            hooks=hooks,
        )

    assert migration.target_collection in qdrant.collections


def test_retire_marks_retained_before_deleting_non_serving_pair() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "active")
    plan = migration.preflight()
    hooks = _LifecycleHooks()

    result = migration.retire(
        confirm=True,
        plan=plan,
        lock_held=True,
        hooks=hooks,
    )

    assert result["migration_state"] == "retained"
    assert migration.target_collection not in qdrant.collections
    assert migration.target_metadata_collection not in qdrant.collections
    marker_writes = [
        index
        for index, (method, path, _body) in enumerate(qdrant.requests)
        if method == "PUT"
        and path.endswith("/current__context__openviking_meta/points")
    ]
    data_deletes = [
        index
        for index, (method, path, _body) in enumerate(qdrant.requests)
        if method == "DELETE" and path.endswith("/current__context")
    ]
    assert marker_writes and data_deletes
    assert marker_writes[-1] < data_deletes[0]


def test_retire_orphan_cleanup_requires_absent_plan_exact_names_and_confirm() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    plan = migration.preflight()
    qdrant.add_collection(
        migration.target_collection,
        vectors={"vector": {"size": 2, "distance": "Cosine"}},
    )
    qdrant.add_collection(
        migration.target_metadata_collection,
        vectors={"meta": {"size": 1, "distance": "Dot"}},
    )
    hooks = _LifecycleHooks()

    with pytest.raises(MigrationError, match="confirm"):
        migration.retire(
            confirm=False,
            plan=plan,
            lock_held=True,
            hooks=hooks,
        )
    with pytest.raises(MigrationError, match="lock"):
        migration.retire(
            confirm=True,
            plan=plan,
            lock_held=False,
            hooks=hooks,
        )

    result = migration.retire(
        confirm=True,
        plan=plan,
        lock_held=True,
        hooks=hooks,
    )
    assert result["migration_state"] == "orphan_cleaned"
    assert migration.target_collection not in qdrant.collections
    assert migration.target_metadata_collection not in qdrant.collections


_HOOK_NAMES = (
    "drain_legacy_writes",
    "remove_legacy_from_serving_path",
    "rollout_current",
    "wait_current_ready",
    "smoke_current_read_only",
    "remove_current_from_serving_path",
    "restore_legacy",
    "verify_legacy_read_path",
    "current_target_has_accepted_writes",
    "assert_target_not_served",
)


def _hook_document() -> dict[str, list[str]]:
    return {name: ["fake-hook", name] for name in _HOOK_NAMES}


def _hook_migration() -> SimpleNamespace:
    return SimpleNamespace(
        logical_collection="legacy/context",
        migration_id="mig-1",
        source_collection="legacy__context",
        source_metadata_collection="legacy__context__openviking_meta",
        target_collection="current__context",
        target_metadata_collection="current__context__openviking_meta",
        timeout_seconds=7.25,
        migrator_version="qdrant-blue-green-v1",
    )


def test_deployment_hooks_runner_uses_safe_argv_and_bindings(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps(_hook_document()), encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        stdout = "false\n" if argv[1] == "current_target_has_accepted_writes" else "hook output"
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=stdout,
            stderr="secret hook stderr",
        )

    monkeypatch.setenv("QDRANT_API_KEY", "secret-api-key")
    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate.subprocess.run",
        fake_run,
    )
    hooks = DeploymentHooks.from_path(str(path))
    migration = _hook_migration()

    for name in _HOOK_NAMES:
        result = getattr(hooks, name)(migration)
        if name == "current_target_has_accepted_writes":
            assert result is False

    assert len(calls) == len(_HOOK_NAMES)
    for argv, kwargs in calls:
        assert argv == _hook_document()[argv[1]]
        assert kwargs["check"] is True
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 7.25
        env = kwargs["env"]
        assert env["OV_LOGICAL_COLLECTION"] == "legacy/context"
        assert env["OV_MIGRATION_ID"] == "mig-1"
        assert env["OV_SOURCE_COLLECTION"] == "legacy__context"
        assert env["OV_SOURCE_METADATA_COLLECTION"] == "legacy__context__openviking_meta"
        assert env["OV_TARGET_COLLECTION"] == "current__context"
        assert env["OV_TARGET_METADATA_COLLECTION"] == "current__context__openviking_meta"
        assert env["OV_TIMEOUT_SECONDS"] == "7.25"
        assert env["OV_MIGRATOR_VERSION"] == "qdrant-blue-green-v1"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("drain_legacy_writes", []),
        ("rollout_current", [""]),
        ("smoke_current_read_only", [1]),
        ("assert_target_not_served", ["bad\x00argv"]),
    ],
)
def test_deployment_hooks_reject_complete_key_invalid_argv_documents(
    tmp_path,
    name: str,
    value: object,
) -> None:
    document = _hook_document()
    document[name] = value  # type: ignore[assignment]
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(MigrationError, match="non-empty argv list"):
        DeploymentHooks.from_path(str(path))


@pytest.mark.parametrize("stdout", ["", "TRUE", "true\nfalse", '{"value": false}'])
def test_deployment_hooks_reject_malformed_boolean_output(
    monkeypatch,
    stdout: str,
) -> None:
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="secret")

    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate.subprocess.run",
        fake_run,
    )
    hooks = DeploymentHooks(_hook_document())

    with pytest.raises(MigrationError, match="only true or false"):
        hooks.current_target_has_accepted_writes(_hook_migration())


@pytest.mark.parametrize("failure", ["timeout", "nonzero"])
def test_deployment_hooks_wrap_timeout_and_nonzero_without_leaking_output(
    monkeypatch,
    capsys,
    failure: str,
) -> None:
    def fake_run(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(
                argv,
                kwargs["timeout"],
                output="secret timeout output",
                stderr="secret timeout stderr",
            )
        raise subprocess.CalledProcessError(
            17,
            argv,
            output="secret failure output",
            stderr="secret failure stderr",
        )

    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate.subprocess.run",
        fake_run,
    )
    hooks = DeploymentHooks(_hook_document())

    with pytest.raises(MigrationError, match="timed out|failed"):
        hooks.drain_legacy_writes(_hook_migration())

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert "secret" not in str(captured)


def test_cutover_requires_resume_for_interrupted_cutting_over() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "cutting_over")
    plan = migration.preflight()
    hooks = _LifecycleHooks()

    with pytest.raises(MigrationError, match="--resume"):
        migration.cutover(
            confirm=True,
            plan=plan,
            barrier_held=True,
            lock_held=True,
            hooks=hooks,
        )

    assert hooks.events == []
    assert qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]["migration_state"] == "cutting_over"


def test_resumed_cutover_rejects_accepted_writes_before_reconcile_or_target_writes(
    monkeypatch,
) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "cutting_over")
    plan = migration.preflight()
    hooks = _LifecycleHooks(accepted_writes=True)
    writes_before = len(
        [request for request in qdrant.requests if request[0] in {"PUT", "DELETE"}]
    )

    def unexpected_reconcile(**kwargs):
        raise AssertionError("reconcile must not run after accepted writes")

    monkeypatch.setattr(migration, "reconcile", unexpected_reconcile)
    with pytest.raises(MigrationError, match="accepted current-format writes"):
        migration.cutover(
            confirm=True,
            plan=plan,
            barrier_held=True,
            lock_held=True,
            resume=True,
            hooks=hooks,
        )

    assert hooks.events == ["current_target_has_accepted_writes"]
    assert len(
        [request for request in qdrant.requests if request[0] in {"PUT", "DELETE"}]
    ) == writes_before


def test_resumed_cutover_false_check_runs_before_idempotent_sequence() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "cutting_over")
    plan = migration.preflight()
    hooks = _LifecycleHooks()

    result = migration.cutover(
        confirm=True,
        plan=plan,
        barrier_held=True,
        lock_held=True,
        resume=True,
        hooks=hooks,
        allow_acl_fail_open=True,
    )

    assert result["migration_state"] == "active"
    assert hooks.events[0] == "current_target_has_accepted_writes"
    assert hooks.events[-1] == "current_target_has_accepted_writes"
    assert hooks.events.index("drain_legacy_writes") > 0


def test_resumed_cutover_unserves_partial_rollout_before_reconcile(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _ready_migration(qdrant)
    hooks = _PartialRolloutHooks(remove_serving=True)

    with pytest.raises(RuntimeError, match="wait_current_ready"):
        migration.cutover(
            confirm=True,
            plan=plan,
            barrier_held=True,
            lock_held=True,
            hooks=hooks,
            allow_acl_fail_open=True,
        )

    assert hooks.serving is True
    events_before_resume = len(hooks.events)
    reconcile_started: list[bool] = []
    original_reconcile = migration.reconcile

    def observe_reconcile(**kwargs):
        reconcile_started.append(True)
        assert hooks.events[events_before_resume : events_before_resume + 3] == [
            "current_target_has_accepted_writes",
            "remove_current_from_serving_path",
            "assert_target_not_served",
        ]
        return original_reconcile(**kwargs)

    monkeypatch.setattr(migration, "reconcile", observe_reconcile)
    result = migration.cutover(
        confirm=True,
        plan=plan,
        barrier_held=True,
        lock_held=True,
        resume=True,
        hooks=hooks,
        allow_acl_fail_open=True,
    )

    assert result["migration_state"] == "active"
    assert reconcile_started == [True]
    assert hooks.assert_target_not_served_calls == 1


def test_resumed_cutover_refuses_if_partial_rollout_remains_served(monkeypatch) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration, plan = _ready_migration(qdrant)
    hooks = _PartialRolloutHooks(remove_serving=False)

    with pytest.raises(RuntimeError, match="wait_current_ready"):
        migration.cutover(
            confirm=True,
            plan=plan,
            barrier_held=True,
            lock_held=True,
            hooks=hooks,
            allow_acl_fail_open=True,
        )

    writes_before_resume = len(
        [request for request in qdrant.requests if request[0] in {"PUT", "DELETE"}]
    )
    events_before_resume = len(hooks.events)

    def unexpected_reconcile(**kwargs):
        raise AssertionError("reconcile must not run while target is still served")

    monkeypatch.setattr(migration, "reconcile", unexpected_reconcile)
    with pytest.raises(RuntimeError, match="target still served"):
        migration.cutover(
            confirm=True,
            plan=plan,
            barrier_held=True,
            lock_held=True,
            resume=True,
            hooks=hooks,
            allow_acl_fail_open=True,
        )

    assert hooks.events[events_before_resume:] == [
        "current_target_has_accepted_writes",
        "remove_current_from_serving_path",
        "assert_target_not_served",
    ]
    assert len(
        [request for request in qdrant.requests if request[0] in {"PUT", "DELETE"}]
    ) == writes_before_resume


def _add_empty_target_collection(qdrant: FakeQdrant, migration: QdrantMigration) -> None:
    qdrant.add_collection(
        migration.target_collection,
        vectors={"vector": {"size": 2, "distance": "Cosine"}},
    )


def test_retire_rejects_target_data_reappearing_on_metadata_only_retry() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "retained")
    plan = migration.preflight()
    qdrant.collections.pop(migration.target_collection)

    hooks: _LifecycleHooks

    def reappear() -> None:
        if hooks.assert_target_not_served_calls == 1:
            _add_empty_target_collection(qdrant, migration)

    hooks = _LifecycleHooks(on_assert_target_not_served=reappear)
    with pytest.raises(MigrationError, match="reappeared"):
        migration.retire(
            confirm=True,
            plan=plan,
            lock_held=True,
            hooks=hooks,
        )

    assert migration.target_collection in qdrant.collections
    assert migration.target_metadata_collection in qdrant.collections


def test_retire_rejects_target_data_reappearing_before_metadata_delete() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "active")
    plan = migration.preflight()

    hooks: _LifecycleHooks

    def reappear() -> None:
        if hooks.assert_target_not_served_calls == 3:
            _add_empty_target_collection(qdrant, migration)

    hooks = _LifecycleHooks(on_assert_target_not_served=reappear)
    with pytest.raises(MigrationError, match="reappeared"):
        migration.retire(
            confirm=True,
            plan=plan,
            lock_held=True,
            hooks=hooks,
        )

    assert migration.target_collection in qdrant.collections
    assert migration.target_metadata_collection in qdrant.collections


@pytest.mark.parametrize(
    "delete_response",
    [{"result": False}, {}, {"result": "true"}],
)
def test_retire_rejects_false_missing_or_malformed_data_delete_receipts(
    monkeypatch,
    delete_response: dict[str, object],
) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "active")
    plan = migration.preflight()
    original_request = qdrant.request

    def fake_request(method, path, body=None, *, params=None):
        if method == "DELETE" and path == migration._path(migration.target_collection):
            return delete_response
        return original_request(method, path, body, params=params)

    monkeypatch.setattr(qdrant, "request", fake_request)
    with pytest.raises(MigrationError, match="did not complete"):
        migration.retire(
            confirm=True,
            plan=plan,
            lock_held=True,
            hooks=_LifecycleHooks(),
        )

    assert migration.target_collection in qdrant.collections
    assert migration.target_metadata_collection in qdrant.collections


def test_retire_rejects_claimed_data_delete_when_collection_remains(
    monkeypatch,
) -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "active")
    plan = migration.preflight()
    original_request = qdrant.request

    def fake_request(method, path, body=None, *, params=None):
        if method == "DELETE" and path == migration._path(migration.target_collection):
            return {"result": True}
        return original_request(method, path, body, params=params)

    monkeypatch.setattr(qdrant, "request", fake_request)
    with pytest.raises(MigrationError, match="remains"):
        migration.retire(
            confirm=True,
            plan=plan,
            lock_held=True,
            hooks=_LifecycleHooks(),
        )

    assert migration.target_collection in qdrant.collections
    assert migration.target_metadata_collection in qdrant.collections


def test_retire_metadata_delete_failure_leaves_owned_retry_receipt() -> None:
    qdrant = _legacy_fixture(sparse=False)
    migration = _migration(qdrant)
    _apply(migration, confirm=True, allow_acl_fail_open=True)
    _set_marker_state(qdrant, migration, "active")
    plan = migration.preflight()
    original_request = qdrant.request
    failed = False

    def fail_metadata_once(method, path, body=None, *, params=None):
        nonlocal failed
        if method == "DELETE" and path == migration._path(
            migration.target_metadata_collection
        ) and not failed:
            failed = True
            raise _FakeHttpError(500)
        return original_request(method, path, body, params=params)

    qdrant.request = fail_metadata_once
    with pytest.raises(_FakeHttpError):
        migration.retire(
            confirm=True,
            plan=plan,
            lock_held=True,
            hooks=_LifecycleHooks(),
        )

    assert migration.target_collection not in qdrant.collections
    assert migration.target_metadata_collection in qdrant.collections
    marker = qdrant.collections[migration.target_metadata_collection]["points"][
        to_qdrant_point_id("openviking:metadata")
    ]["payload"]
    assert marker["migration_state"] == "retained"

    qdrant.request = original_request
    result = migration.retire(
        confirm=True,
        plan=plan,
        lock_held=True,
        hooks=_LifecycleHooks(),
    )
    assert result["migration_state"] == "retained"
    assert migration.target_metadata_collection not in qdrant.collections


class _CliMigration:
    instances: list["_CliMigration"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.instances.append(self)

    def reconcile(self, **kwargs):
        self.calls.append(("reconcile", kwargs))
        return {"migration_state": "building"}

    def verify(self, **kwargs):
        self.calls.append(("verify", kwargs))
        return {"migration_state": "ready"}

    def cutover(self, **kwargs):
        self.calls.append(("cutover", kwargs))
        return {"migration_state": "active"}

    def rollback(self, **kwargs):
        self.calls.append(("rollback", kwargs))
        return {"migration_state": "rolled_back"}

    def retire(self, **kwargs):
        self.calls.append(("retire", kwargs))
        return {"migration_state": "retained"}


@pytest.mark.parametrize(
    ("command", "phase_args", "expected_call", "expected"),
    [
        (
            "reconcile",
            ["--confirm", "--lock-held", "--barrier-held", "--allow-acl-fail-open"],
            "reconcile",
            {
                "confirm": True,
                "lock_held": True,
                "barrier_held": True,
                "allow_acl_fail_open": True,
            },
        ),
        (
            "verify",
            [
                "--confirm",
                "--lock-held",
                "--barrier-held",
                "--final",
                "--allow-acl-fail-open",
            ],
            "verify",
            {
                "confirm": True,
                "lock_held": True,
                "barrier_held": True,
                "final": True,
                "allow_acl_fail_open": True,
            },
        ),
        (
            "cutover",
            [
                "--confirm",
                "--lock-held",
                "--barrier-held",
                "--resume",
                "--allow-acl-fail-open",
                "--deployment-hooks",
                "hooks.json",
            ],
            "cutover",
            {
                "confirm": True,
                "lock_held": True,
                "barrier_held": True,
                "resume": True,
                "allow_acl_fail_open": True,
            },
        ),
        (
            "rollback",
            [
                "--confirm",
                "--lock-held",
                "--barrier-held",
                "--no-current-format-writes-accepted",
                "--deployment-hooks",
                "hooks.json",
            ],
            "rollback",
            {
                "confirm": True,
                "lock_held": True,
                "barrier_held": True,
                "no_current_format_writes_accepted": True,
            },
        ),
        (
            "retire",
            ["--confirm", "--lock-held", "--deployment-hooks", "hooks.json"],
            "retire",
            {"confirm": True, "lock_held": True},
        ),
    ],
)
def test_cli_dispatches_lifecycle_phase_arguments(
    monkeypatch,
    tmp_path,
    capsys,
    command: str,
    phase_args: list[str],
    expected_call: str,
    expected: dict[str, object],
) -> None:
    plan = _migration(_legacy_fixture(sparse=False)).preflight()
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text("{}", encoding="utf-8")
    _CliMigration.instances.clear()
    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate.QdrantRestClient",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate.QdrantMigration",
        _CliMigration,
    )
    monkeypatch.setattr(
        DeploymentHooks,
        "from_path",
        staticmethod(lambda path: _LifecycleHooks()),
    )

    base = [
        "--url",
        "http://qdrant.invalid",
        "--source-collection",
        "legacy__context",
        "--target-collection",
        "current__context",
        "--logical-collection",
        "legacy/context",
        "--migration-id",
        "mig-1",
        "--timeout-seconds",
        "10",
        command,
    ]
    if command != "rollback":
        base.extend(["--plan", str(plan_path)])
    result = main(base + phase_args)

    assert result == 0
    instance = _CliMigration.instances[-1]
    name, kwargs = instance.calls[-1]
    assert name == expected_call
    for key, value in expected.items():
        assert kwargs[key] is value
    assert len(capsys.readouterr().out.splitlines()) == 1


def test_cli_rejects_invalid_hook_arrays_before_controller_dispatch(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "drain_legacy_writes": ["bad\x00command"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate.QdrantRestClient",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "scripts.maintenance.qdrant_migrate.QdrantMigration",
        _CliMigration,
    )
    result = main(
        [
            "--url",
            "http://qdrant.invalid",
            "--source-collection",
            "legacy__context",
            "--target-collection",
            "current__context",
            "--logical-collection",
            "legacy/context",
            "--migration-id",
            "mig-1",
            "--timeout-seconds",
            "10",
            "rollback",
            "--confirm",
            "--lock-held",
            "--barrier-held",
            "--no-current-format-writes-accepted",
            "--deployment-hooks",
            str(hooks_path),
        ]
    )

    assert result == 2
    assert "deployment hook keys differ" in capsys.readouterr().err
    assert not _CliMigration.instances[-1].calls


def test_qdrant_ci_workflow_lists_required_suites(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/pr.yml").read_text())
    lite_workflow = yaml.safe_load(
        (root / ".github/workflows/_test_lite.yml").read_text()
    )

    check_deps = workflow["jobs"]["check-deps"]
    assert (
        check_deps["outputs"]["qdrant_changed"]
        == "${{ steps.check.outputs.qdrant_changed }}"
    )
    check_step = next(step for step in check_deps["steps"] if step.get("id") == "check")
    check_script = check_step["run"]
    qdrant_pattern_line = next(
        line.strip()
        for line in check_script.splitlines()
        if line.strip().startswith("QDRANT_PATTERN=")
    )
    qdrant_pattern = qdrant_pattern_line.split("=", 1)[1].strip().strip('"')
    for path in (
        *REQUIRED_QDRANT_TESTS,
        *REQUIRED_QDRANT_SHARED_DEPENDENCIES,
        "openviking/storage/vectordb/collection/qdrant_rest.py",
        "openviking/storage/vectordb/collection/qdrant_collection.py",
        "openviking/storage/vectordb/qdrant_sparse.py",
        "openviking/storage/vectordb/qdrant_utils.py",
        "openviking/storage/vectordb_adapters/qdrant_adapter.py",
        "openviking_cli/utils/config/vectordb_config.py",
        "scripts/maintenance/qdrant_migrate.py",
        "pyproject.toml",
        "uv.lock",
        ".github/workflows/pr.yml",
        ".github/workflows/_test_lite.yml",
    ):
        assert re.search(qdrant_pattern, path), path
    assert not re.search(qdrant_pattern, "docs/en/guides/01-configuration.md")
    assert 'echo "qdrant_changed=true" >> "$GITHUB_OUTPUT"' in check_script
    assert 'echo "qdrant_changed=false" >> "$GITHUB_OUTPUT"' in check_script

    qdrant_job = workflow["jobs"]["qdrant-tests"]
    assert qdrant_job["needs"] == "check-deps"
    assert (
        qdrant_job["if"]
        == "${{ needs.check-deps.outputs.qdrant_changed == 'true' }}"
    )
    assert qdrant_job["uses"] == "./.github/workflows/_test_lite.yml"
    assert qdrant_job["with"]["os_json"] == '["ubuntu-24.04"]'
    assert qdrant_job["with"]["python_json"] == '["3.10"]'
    assert json.loads(qdrant_job["with"]["test_paths_json"]) == list(
        REQUIRED_QDRANT_TESTS
    )
    assert "secrets" not in qdrant_job

    lite_on = lite_workflow.get("on", lite_workflow.get(True))
    lite_inputs = lite_on["workflow_call"]["inputs"]
    assert "test_paths_json" in lite_inputs
    for trigger_name in ("workflow_call", "workflow_dispatch"):
        assert json.loads(
            lite_on[trigger_name]["inputs"]["test_paths_json"]["default"]
        ) == list(DEFAULT_CUVS_TESTS)
    lite_steps = lite_workflow["jobs"]["test-lite"]["steps"]
    test_step = next(step for step in lite_steps if "pytest" in step.get("run", ""))
    assert test_step["env"] == {
        "QDRANT_URL": "",
        "QDRANT_API_KEY": "",
        "TEST_PATHS_JSON": "${{ inputs.test_paths_json }}",
    }
    assert test_step["shell"] == "bash"

    runner = test_step["run"].rstrip("\n")
    prefix = "uv run python - <<'PY'\n"
    suffix = "\nPY"
    assert runner.startswith(prefix)
    assert runner.endswith(suffix)
    runner_source = runner[len(prefix) : -len(suffix)]
    assert "${{ join(fromJson(inputs.test_paths_json), ' ') }}" not in runner

    captured: list[tuple[list[str], dict[str, object]]] = []

    def capture(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        captured.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", capture)
    def run_runner(paths: list[str]) -> tuple[list[str], dict[str, object]]:
        captured.clear()
        monkeypatch.setenv("TEST_PATHS_JSON", json.dumps(paths))
        exec(compile(runner_source, "<_test_lite.yml>", "exec"), {})
        assert len(captured) == 1
        return captured[0]

    command_kwargs = {"check": True, "shell": False}
    command_prefix = ["uv", "run", "pytest", "-q", "-o", "addopts=", "--"]
    assert run_runner(list(DEFAULT_CUVS_TESTS)) == (
        [*command_prefix, *DEFAULT_CUVS_TESTS],
        command_kwargs,
    )
    assert run_runner(list(REQUIRED_QDRANT_TESTS)) == (
        [*command_prefix, *REQUIRED_QDRANT_TESTS],
        command_kwargs,
    )

    malicious_paths = ["tests/fixture; printf SHOULD_NOT_RUN", "--literal-option"]
    assert run_runner(malicious_paths) == (
        [*command_prefix, *malicious_paths],
        command_kwargs,
    )

    captured.clear()
    monkeypatch.setenv("TEST_PATHS_JSON", json.dumps(["tests/valid", 1]))
    with pytest.raises(SystemExit, match="non-empty JSON array of non-empty strings"):
        exec(compile(runner_source, "<_test_lite.yml>", "exec"), {})
    assert captured == []
