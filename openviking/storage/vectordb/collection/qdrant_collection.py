# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Qdrant collection implementation for the OpenViking Collection contract."""

from __future__ import annotations

import math
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from openviking.storage.vectordb.collection.collection import ICollection
from openviking.storage.vectordb.collection.qdrant_rest import QdrantError, QdrantRestClient
from openviking.storage.vectordb.collection.result import (
    AggregateResult,
    DataItem,
    FetchDataInCollectionResult,
    SearchItemResult,
    SearchResult,
)
from openviking.storage.vectordb.qdrant_sparse import (
    SparseTermDictionary,
    parse_sparse_point,
    sparse_owner_point_id,
)
from openviking.storage.vectordb.qdrant_utils import (
    build_qdrant_payload,
    is_qdrant_migration_marker,
    pending_work,
    qdrant_payload_field_schema,
    to_qdrant_point_id,
)

_META_VERSION = 1
_META_MARKER_ID = to_qdrant_point_id("openviking:metadata")
_META_VECTOR_NAME = "meta"
_INTERNAL_PAYLOAD_FIELDS = {
    "uri_depth",
    "scope_roots",
}
_ADAPTER_MARKER_FIELDS = {
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
_MIGRATION_STATE_SETUP = {
    "building": False,
    "failed": False,
    "rolled_back": False,
    "ready": True,
    "cutting_over": True,
    "active": True,
    "retained": True,
}


@dataclass
class _Hit:
    point_id: str
    item: SearchItemResult


class QdrantCollection(ICollection):
    """REST-backed implementation of :class:`ICollection`."""

    def __init__(
        self,
        *,
        client: QdrantRestClient,
        collection_name: str,
        metadata_collection_name: str,
        dense_vector_name: str,
        sparse_vector_name: str,
        vector_dim: int,
        distance: str,
        sparse_enabled: bool,
        sparse_weight: float,
        logical_collection: str | None = None,
        require_logical_collection: bool = False,
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._metadata_collection_name = metadata_collection_name
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._vector_dim = int(vector_dim)
        self._distance = self._normalize_distance(distance)
        self._sparse_enabled = bool(sparse_enabled)
        self._sparse_weight = float(sparse_weight)
        self._schema: dict[str, Any] = {}
        self._indexes: dict[str, dict[str, Any]] = {}
        self._sparse_dictionary: SparseTermDictionary | None = None
        self._migration_marker_fields: dict[str, Any] | None = None
        self._logical_collection = logical_collection
        self._require_logical_collection = bool(require_logical_collection)
        self._marker_loaded_from_remote = False

    @staticmethod
    def _normalize_distance(distance: str) -> str:
        value = str(distance or "cosine").strip().lower()
        mapping = {
            "cosine": "Cosine",
            "ip": "Dot",
            "dot": "Dot",
            "l2": "Euclid",
            "euclid": "Euclid",
        }
        if value not in mapping:
            raise ValueError(f"Unsupported Qdrant distance metric: {distance!r}")
        return mapping[value]

    @staticmethod
    def _result(response: dict[str, Any]) -> Any:
        return response.get("result", response)

    @staticmethod
    def _require_completed(response: dict[str, Any], operation: str) -> None:
        result = response.get("result")
        if not isinstance(result, dict) or result.get("status") != "completed":
            raise QdrantError(f"Qdrant {operation} did not complete")

    def _timeout_param(self) -> int:
        return max(1, math.ceil(self._client.timeout_seconds))

    @staticmethod
    def _strong_point_params() -> dict[str, str]:
        return {"wait": "true", "ordering": "strong"}

    def _wait_payload_index(self, field: str, *, present: bool) -> None:
        self._wait_collection_ready(
            self._collection_name,
            payload_field=field,
            payload_present=present,
        )

    def _wait_collection_ready(
        self,
        name: str,
        *,
        payload_field: str | None = None,
        payload_present: bool = True,
    ) -> None:
        deadline = time.monotonic() + self._client.timeout_seconds
        while True:
            response = self._client.request("GET", self._path(name))
            result = self._result(response)
            if not isinstance(result, dict):
                raise QdrantError("Qdrant collection readiness response is malformed")
            status = result.get("status")
            optimizer_status = result.get("optimizer_status")
            if not isinstance(status, str) or not isinstance(optimizer_status, (str, dict)):
                raise QdrantError("Qdrant collection readiness response is malformed")
            normalized_status = status.strip().lower()
            if normalized_status in {"red", "error", "failed"} or isinstance(
                optimizer_status, dict
            ):
                raise QdrantError(f"Qdrant collection {name!r} is not ready")
            normalized_optimizer = optimizer_status.strip().lower()
            if normalized_optimizer in {"red", "error", "failed"}:
                raise QdrantError(f"Qdrant collection {name!r} is not ready")
            payload_ready = True
            if payload_field is not None:
                payload_schema = result.get("payload_schema")
                if not isinstance(payload_schema, dict):
                    raise QdrantError("Qdrant collection response has no payload_schema")
                payload_ready = (payload_field in payload_schema) is payload_present
            status_ready = normalized_status == "green"
            if payload_field is not None:
                status_ready = normalized_status in {"green", "yellow", "grey"}
            if (
                status_ready
                and normalized_optimizer == "ok"
                and not any(
                    pending_work(result[field])
                    for field in ("update_queue", "deferred")
                    if field in result
                )
                and payload_ready
            ):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if payload_field is not None:
                    state = "visible" if payload_present else "absent"
                    raise QdrantError(
                        f"Qdrant payload index {payload_field!r} did not become {state}"
                    )
                raise QdrantError(f"Qdrant collection {name!r} did not become ready")
            time.sleep(min(0.1, remaining))

    def _path(self, name: str, suffix: str = "") -> str:
        return f"/collections/{quote(name, safe='')}{suffix}"

    def _exists(self, name: str) -> bool:
        try:
            self._client.request("GET", self._path(name))
        except QdrantError as exc:
            if exc.status == 404:
                return False
            raise
        return True

    def collection_exists(self) -> bool:
        return self._exists(self._collection_name)

    def _create_collection(
        self,
        name: str,
        *,
        metadata: bool = False,
    ) -> None:
        if metadata:
            vectors = {_META_VECTOR_NAME: {"size": 1, "distance": "Dot"}}
            body: dict[str, Any] = {"vectors": vectors}
        else:
            body = {
                "vectors": {
                    self._dense_vector_name: {
                        "size": self._vector_dim,
                        "distance": self._distance,
                    }
                }
            }
            if self._sparse_enabled:
                body["sparse_vectors"] = {self._sparse_vector_name: {}}
        try:
            self._client.request(
                "PUT",
                self._path(name),
                body,
                params={"timeout": self._timeout_param()},
            )
            self._wait_collection_ready(name)
        except QdrantError as exc:
            if exc.status != 409:
                raise
            raise RuntimeError(f"Qdrant collection {name!r} appeared during creation") from exc

    def create_remote_collection(self, metadata: dict[str, Any]) -> None:
        self._schema = dict(metadata)
        if self._vector_dim <= 0:
            for field in self._schema.get("Fields", []):
                if str(field.get("FieldType", "")).lower() == "vector" and field.get("Dim"):
                    self._vector_dim = int(field["Dim"])
                    break
        if self._vector_dim <= 0:
            raise ValueError("Qdrant backend requires a positive dense vector dimension")
        collection_exists = self._exists(self._collection_name)
        metadata_exists = self._exists(self._metadata_collection_name)
        if collection_exists:
            raise RuntimeError(
                f"Qdrant collection {self._collection_name!r} appeared during creation; "
                "refusing to adopt existing data"
            )
        if metadata_exists:
            raise RuntimeError(
                f"Qdrant metadata collection {self._metadata_collection_name!r} "
                f"exists without data collection {self._collection_name!r}"
            )
        self._create_collection(self._collection_name)
        self._create_collection(self._metadata_collection_name, metadata=True)
        self._migration_marker_fields = {}
        self._marker_loaded_from_remote = False
        self._write_metadata_marker()

    def has_openviking_metadata(self) -> bool:
        payload = self._load_metadata_marker()
        if not payload:
            return False
        try:
            self._validate_marker_binding(payload)
        except RuntimeError:
            return False
        return True

    def _metadata_payload(self) -> dict[str, Any]:
        if self._migration_marker_fields is None:
            self._load_metadata_marker()
        payload = {
            **(self._migration_marker_fields or {}),
            "_openviking_meta_version": _META_VERSION,
            "collection_name": self._collection_name,
            "metadata_collection_name": self._metadata_collection_name,
            "schema": self._schema,
            "dense_vector_name": self._dense_vector_name,
            "sparse_vector_name": self._sparse_vector_name,
            "vector_dim": self._vector_dim,
            "distance": self._distance,
            "sparse_enabled": self._sparse_enabled,
            "sparse_weight": self._sparse_weight,
            "indexes": self._indexes,
        }
        if self._logical_collection is not None:
            payload["logical_collection"] = self._logical_collection
        if is_qdrant_migration_marker(payload):
            payload["vector_dimension"] = self._vector_dim
        return payload

    def _write_metadata_marker(self) -> None:
        if self._marker_loaded_from_remote:
            marker = self._load_metadata_marker()
            if marker is None:
                raise RuntimeError(
                    f"Qdrant metadata marker disappeared for {self._collection_name!r}"
                )
            self._validate_marker_binding(marker)
            self._migration_marker_fields = {
                name: value for name, value in marker.items() if name not in _ADAPTER_MARKER_FIELDS
            }
        self._upsert_points(
            self._metadata_collection_name,
            [
                {
                    "id": _META_MARKER_ID,
                    "vector": {_META_VECTOR_NAME: [0.0]},
                    "payload": self._metadata_payload(),
                }
            ],
        )
        self._marker_loaded_from_remote = True

    def _load_metadata_marker(self) -> dict[str, Any] | None:
        self._marker_loaded_from_remote = True
        if not self._exists(self._metadata_collection_name):
            self._migration_marker_fields = {}
            return None
        points = self._retrieve_points(
            self._metadata_collection_name,
            [_META_MARKER_ID],
            with_vectors=False,
        )
        if not points:
            self._migration_marker_fields = {}
            return None
        if len(points) != 1:
            self._migration_marker_fields = {}
            raise RuntimeError(
                "Qdrant metadata marker lookup returned multiple marker points"
            )
        payload = points[0].get("payload")
        if str(points[0].get("id")) != _META_MARKER_ID:
            self._migration_marker_fields = {}
            raise RuntimeError("Qdrant metadata marker point ID does not match the marker ID")
        if not isinstance(payload, dict):
            self._migration_marker_fields = {}
            return None
        self._migration_marker_fields = {
            name: value for name, value in payload.items() if name not in _ADAPTER_MARKER_FIELDS
        }
        return payload

    def _validate_marker_binding(self, marker: dict[str, Any]) -> None:
        if type(marker.get("_openviking_meta_version")) is not int or (
            marker["_openviking_meta_version"] != _META_VERSION
        ):
            raise RuntimeError(
                f"Qdrant collection {self._collection_name!r} has no valid current marker"
            )
        migration_marker = is_qdrant_migration_marker(marker)
        if migration_marker:
            for field_name in ("migration_id", "migration_state"):
                value = marker.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    raise RuntimeError(
                        f"Qdrant migration marker has an invalid {field_name}"
                    )
        setup_complete = marker.get("setup_complete", True)
        if migration_marker and "setup_complete" not in marker:
            raise RuntimeError(
                f"Qdrant collection {self._collection_name!r} has no setup_complete flag"
            )
        if not isinstance(setup_complete, bool):
            raise RuntimeError(
                f"Qdrant collection {self._collection_name!r} has an invalid setup_complete flag"
            )
        migration_state = marker.get("migration_state")
        if migration_marker and migration_state is not None:
            expected_setup = (
                _MIGRATION_STATE_SETUP.get(migration_state)
                if isinstance(migration_state, str)
                else None
            )
            if expected_setup is None:
                raise RuntimeError(
                    f"Qdrant migration marker has an invalid migration state: {migration_state!r}"
                )
            if setup_complete is not expected_setup:
                raise RuntimeError(
                    "Qdrant migration marker migration_state and setup_complete disagree"
                )
        if not setup_complete:
            raise RuntimeError(
                f"Qdrant collection {self._collection_name!r} has an incomplete migration"
            )
        if marker.get("migration_state") == "rolled_back":
            raise RuntimeError(f"Qdrant collection {self._collection_name!r} has been rolled back")
        if marker.get("collection_name") != self._collection_name:
            raise RuntimeError(
                f"Qdrant metadata collection does not belong to {self._collection_name!r}"
            )
        if (
            "metadata_collection_name" in marker
            and marker["metadata_collection_name"] != self._metadata_collection_name
        ):
            raise RuntimeError("Qdrant metadata marker is bound to a different metadata collection")
        logical_collection = marker.get("logical_collection")
        if self._require_logical_collection and (
            not isinstance(self._logical_collection, str)
            or not self._logical_collection.strip()
            or not isinstance(logical_collection, str)
            or not logical_collection.strip()
            or logical_collection != self._logical_collection
        ):
            raise RuntimeError(
                "Qdrant metadata marker is missing or bound to a different logical collection"
            )
        if migration_marker and (
            self._logical_collection is None or logical_collection != self._logical_collection
        ):
            raise RuntimeError(
                "Qdrant migration marker is missing or bound to a different logical collection"
            )
        if (
            logical_collection is not None
            and self._logical_collection is not None
            and logical_collection != self._logical_collection
        ):
            raise RuntimeError("Qdrant metadata marker is bound to a different logical collection")
        target_collection_present = "target_collection" in marker
        target_metadata_present = "target_metadata_collection" in marker
        if target_collection_present or target_metadata_present:
            if (
                target_collection_present != target_metadata_present
                or marker.get("target_collection") != self._collection_name
                or marker.get("target_metadata_collection") != self._metadata_collection_name
            ):
                raise RuntimeError(
                    "Qdrant migration marker target collection pair does not match "
                    "the canonical physical collections"
                )
        schema = marker.get("schema")
        if not isinstance(schema, dict):
            raise RuntimeError(
                f"Qdrant collection {self._collection_name!r} has no metadata schema"
            )

        marker_vector_dim = marker.get("vector_dim")
        if marker_vector_dim is not None and (
            isinstance(marker_vector_dim, bool)
            or not isinstance(marker_vector_dim, int)
            or marker_vector_dim <= 0
        ):
            raise RuntimeError("Qdrant metadata marker has an invalid vector dimension")
        marker_vector_dimension = marker.get("vector_dimension")
        if marker_vector_dimension is not None and (
            isinstance(marker_vector_dimension, bool)
            or not isinstance(marker_vector_dimension, int)
            or marker_vector_dimension <= 0
        ):
            raise RuntimeError("Qdrant metadata marker has an invalid vector_dimension")
        if marker_vector_dimension is not None and marker_vector_dimension != marker_vector_dim:
            raise RuntimeError("Qdrant metadata marker vector_dimension does not match vector_dim")
        if not migration_marker:
            return
        if marker_vector_dim is not None and self._vector_dim > 0:
            if marker_vector_dim != self._vector_dim:
                raise RuntimeError(
                    "Qdrant migration marker vector dimension differs from configured dimension"
                )
        if "dense_vector_name" in marker and marker["dense_vector_name"] != self._dense_vector_name:
            raise RuntimeError("Qdrant migration marker dense vector name differs")
        if (
            "sparse_vector_name" in marker
            and marker["sparse_vector_name"] != self._sparse_vector_name
        ):
            raise RuntimeError("Qdrant migration marker sparse vector name differs")
        if "distance" in marker:
            try:
                marker_distance = self._normalize_distance(marker["distance"])
            except ValueError as exc:
                raise RuntimeError("Qdrant migration marker has an invalid distance") from exc
            if marker_distance != self._distance:
                raise RuntimeError("Qdrant migration marker distance differs")
        if "sparse_enabled" in marker:
            if not isinstance(marker["sparse_enabled"], bool):
                raise RuntimeError("Qdrant migration marker sparse policy is invalid")
            if marker["sparse_enabled"] != self._sparse_enabled:
                raise RuntimeError("Qdrant migration marker sparse policy differs")
        if "sparse_weight" in marker:
            try:
                marker_weight = float(marker["sparse_weight"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Qdrant migration marker sparse weight is invalid") from exc
            if not math.isclose(marker_weight, self._sparse_weight):
                raise RuntimeError("Qdrant migration marker sparse weight differs")

    def _ensure_loaded(self) -> None:
        if self._schema:
            return
        marker = self._load_metadata_marker()
        if not marker:
            raise RuntimeError(
                f"Qdrant collection {self._collection_name!r} is missing OpenViking metadata"
            )
        self._validate_marker_binding(marker)
        self._schema = dict(marker["schema"])
        self._vector_dim = int(marker.get("vector_dim") or self._vector_dim)
        self._dense_vector_name = str(marker.get("dense_vector_name") or self._dense_vector_name)
        self._sparse_vector_name = str(marker.get("sparse_vector_name") or self._sparse_vector_name)
        self._distance = str(marker.get("distance") or self._distance)
        self._sparse_enabled = bool(marker.get("sparse_enabled", self._sparse_enabled))
        if "sparse_weight" in marker:
            self._sparse_weight = float(marker["sparse_weight"])
        indexes = marker.get("indexes")
        self._indexes = (
            {str(name): dict(meta) for name, meta in indexes.items()}
            if isinstance(indexes, dict)
            else {}
        )

    def get_meta_data(self) -> dict[str, Any]:
        self._ensure_loaded()
        return dict(self._schema)

    def update(
        self,
        fields: dict[str, Any] | list[dict[str, Any]] | None = None,
        description: str | None = None,
    ):
        self._ensure_loaded()
        if fields:
            if isinstance(fields, list):
                field_updates = fields
                schema_updates = {}
            else:
                schema_updates = dict(fields)
                field_updates = schema_updates.pop("Fields", None)

            if isinstance(field_updates, list):
                existing_fields = {
                    field.get("FieldName"): field
                    for field in self._schema.get("Fields", [])
                    if isinstance(field, dict) and field.get("FieldName")
                }
                for field in field_updates:
                    if isinstance(field, dict) and field.get("FieldName"):
                        existing_fields.setdefault(field["FieldName"], field)
                self._schema["Fields"] = list(existing_fields.values())
                self._schema.update(schema_updates)
            else:
                self._schema.update(fields)
        if description is not None:
            self._schema["Description"] = description
        self._write_metadata_marker()
        return self._schema

    def close(self) -> None:
        return None

    def drop(self):
        for name in (self._collection_name, self._metadata_collection_name):
            if self._exists(name):
                self._client.request(
                    "DELETE",
                    self._path(name),
                    params={"timeout": self._timeout_param()},
                )
        self._schema.clear()
        return True

    def _field_schema(self, field: str) -> str:
        return qdrant_payload_field_schema(field, self._schema.get("Fields", []))

    @staticmethod
    def _index_fields(meta: dict[str, Any]) -> list[str]:
        scalar_index = meta.get("ScalarIndex")
        if isinstance(scalar_index, dict):
            return [str(field) for field in scalar_index]
        if isinstance(scalar_index, (list, tuple, set)):
            return [str(field) for field in scalar_index]
        return []

    def _ensure_remote_indexes(self, meta_data: dict[str, Any]) -> None:
        scalar_fields = list(meta_data.get("ScalarIndex") or [])
        scalar_fields.extend(["uri_depth", "scope_roots"])
        for field in dict.fromkeys(scalar_fields):
            body = {
                "field_name": field,
                "field_schema": ("integer" if field == "uri_depth" else self._field_schema(field)),
            }
            try:
                response = self._client.request(
                    "PUT",
                    self._path(self._collection_name, "/index"),
                    body,
                    params={"wait": "true"},
                )
                self._require_completed(response, f"index creation for {field!r}")
            except QdrantError as exc:
                if exc.status != 409:
                    raise
            self._wait_payload_index(field, present=True)

    def _delete_remote_indexes(self, fields: list[str]) -> None:
        for field in dict.fromkeys(fields):
            try:
                response = self._client.request(
                    "DELETE",
                    self._path(
                        self._collection_name,
                        f"/index/{quote(field, safe='')}",
                    ),
                    params={"wait": "true"},
                )
                self._require_completed(response, f"index deletion for {field!r}")
            except QdrantError as exc:
                if exc.status != 404:
                    raise
            self._wait_payload_index(field, present=False)

    def create_index(self, index_name: str, meta_data: dict[str, Any]):
        self._ensure_remote_indexes(meta_data)
        previous_indexes = self._indexes
        self._indexes = dict(previous_indexes)
        self._indexes[index_name] = dict(meta_data)
        try:
            self._write_metadata_marker()
        except Exception:
            self._indexes = previous_indexes
            raise
        return meta_data

    def has_index(self, index_name: str) -> bool:
        return index_name in self._indexes

    def get_index(self, index_name: str):
        return self._indexes.get(index_name)

    def get_index_meta_data(self, index_name: str) -> dict[str, Any]:
        return dict(self._indexes.get(index_name, {}))

    def list_indexes(self) -> list[str]:
        return list(self._indexes)

    def update_index(
        self,
        index_name: str,
        scalar_index: dict[str, Any] | list[str] | None = None,
        description: str | None = None,
    ):
        if index_name not in self._indexes:
            return None
        meta = dict(self._indexes.get(index_name, {}))
        if scalar_index is not None:
            meta["ScalarIndex"] = scalar_index
        if description is not None:
            meta["Description"] = description

        old_fields = set(self._index_fields(self._indexes.get(index_name, {})))
        other_fields = {
            field
            for name, item in self._indexes.items()
            if name != index_name
            for field in self._index_fields(item)
        }
        self._ensure_remote_indexes(meta)
        self._delete_remote_indexes(
            [
                field
                for field in old_fields - set(self._index_fields(meta))
                if field not in other_fields
            ]
        )
        previous_indexes = self._indexes
        self._indexes = dict(previous_indexes)
        self._indexes[index_name] = meta
        try:
            self._write_metadata_marker()
        except Exception:
            self._indexes = previous_indexes
            raise
        return meta

    def drop_index(self, index_name: str):
        removed = self._indexes.get(index_name)
        if removed is None:
            return True

        remaining_fields = {
            field
            for name, meta in self._indexes.items()
            if name != index_name
            for field in self._index_fields(meta)
        }
        if any(name != index_name for name in self._indexes):
            remaining_fields.update({"uri_depth", "scope_roots"})
        fields_to_remove = list(
            dict.fromkeys([*self._index_fields(removed), "uri_depth", "scope_roots"])
        )
        self._delete_remote_indexes(
            [field for field in fields_to_remove if field not in remaining_fields]
        )
        previous_indexes = self._indexes
        self._indexes = dict(previous_indexes)
        self._indexes.pop(index_name, None)
        try:
            self._write_metadata_marker()
        except Exception:
            self._indexes = previous_indexes
            raise
        return True

    def _upsert_points(self, collection_name: str, points: list[dict[str, Any]]) -> None:
        response = self._client.request(
            "PUT",
            self._path(collection_name, "/points"),
            {"points": points},
            params=self._strong_point_params(),
        )
        self._require_completed(response, "point upsert")

    def _retrieve_points(
        self,
        collection_name: str,
        ids: list[str],
        *,
        with_vectors: bool,
    ) -> list[dict[str, Any]]:
        response = self._client.request(
            "POST",
            self._path(collection_name, "/points"),
            {
                "ids": ids,
                "with_payload": True,
                "with_vector": with_vectors,
            },
            params={"consistency": "all"}
            if collection_name == self._metadata_collection_name
            else None,
        )
        result = self._result(response)
        if not isinstance(result, list) or any(not isinstance(point, dict) for point in result):
            raise QdrantError("Qdrant point retrieval response is malformed")
        return result

    def _scroll(
        self,
        collection_name: str,
        *,
        filter: dict[str, Any] | None,
        limit: int = 1,
        with_vectors: bool = False,
        order_by: dict[str, Any] | None = None,
        output_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        points: list[dict[str, Any]] = []
        offset: Any = None
        while len(points) < limit:
            body: dict[str, Any] = {
                "limit": limit - len(points),
                "with_payload": self._payload_selector(output_fields),
                "with_vector": with_vectors,
            }
            if filter:
                body["filter"] = filter
            if order_by:
                body["order_by"] = order_by
            if offset is not None:
                body["offset"] = offset
            response = self._client.request(
                "POST",
                self._path(collection_name, "/points/scroll"),
                body,
                params={"consistency": "all"}
                if collection_name == self._metadata_collection_name
                else None,
            )
            result = self._result(response)
            if not isinstance(result, dict):
                raise QdrantError("Qdrant scroll response is malformed")
            page = result.get("points")
            if not isinstance(page, list):
                raise QdrantError("Qdrant scroll response is malformed")
            if not page:
                if result.get("next_page_offset") is not None:
                    raise QdrantError("Qdrant scroll response is malformed")
                break
            if any(not isinstance(point, dict) for point in page):
                raise QdrantError("Qdrant scroll response is malformed")
            points.extend(page)
            if len(points) >= limit:
                break
            offset = result.get("next_page_offset")
            if offset is None:
                break
        return points[:limit]

    def _point_from_record(self, record: dict[str, Any]) -> dict[str, Any]:
        original_id = record.get("id")
        if original_id is None:
            raise ValueError("Qdrant upsert requires an OpenViking record id")
        dense = record.get("vector")
        if dense is not None:
            if not isinstance(dense, list) or len(dense) != self._vector_dim:
                raise ValueError(
                    f"Qdrant dense vector dimension must be {self._vector_dim}, got {len(dense) if isinstance(dense, list) else type(dense).__name__}"
                )
        sparse = record.get("sparse_vector")
        vectors: dict[str, Any] = {}
        if dense is not None:
            dense_values = [float(value) for value in dense]
            if not all(math.isfinite(value) for value in dense_values):
                raise ValueError("Qdrant dense vector values must be finite")
            vectors[self._dense_vector_name] = dense_values
        if sparse:
            vectors[self._sparse_vector_name] = self.encode_sparse_vector(sparse)
        if not vectors:
            raise ValueError("Qdrant record requires a dense or sparse vector")
        payload = build_qdrant_payload(record)
        return {
            "id": to_qdrant_point_id(original_id),
            "vector": vectors,
            "payload": payload,
        }

    def upsert_data(self, data_list: list[dict[str, Any]], ttl: int = 0):
        del ttl
        if not data_list:
            return {"status": "ok"}
        self._upsert_points(
            self._collection_name, [self._point_from_record(item) for item in data_list]
        )
        return {"status": "ok"}

    def _payload_to_record(self, point: dict[str, Any]) -> dict[str, Any]:
        payload = dict(point.get("payload") or {})
        original_id = payload.pop("_openviking_original_id", None)
        if original_id is None:
            raise ValueError("Qdrant payload is missing _openviking_original_id")
        for field in _INTERNAL_PAYLOAD_FIELDS:
            payload.pop(field, None)
        payload["id"] = original_id
        return payload

    def _vectors_to_record(self, point: dict[str, Any]) -> dict[str, Any]:
        record = self._payload_to_record(point)
        vectors = point.get("vector") or point.get("vectors") or {}
        if isinstance(vectors, dict):
            dense = vectors.get(self._dense_vector_name)
            sparse = vectors.get(self._sparse_vector_name)
            if dense is not None:
                record["vector"] = dense
            if sparse is not None:
                record["sparse_vector"] = self._decode_sparse_vector(sparse)
        return record

    def _decode_sparse_vector(self, value: Any) -> dict[str, float]:
        if not isinstance(value, dict):
            raise ValueError("Qdrant sparse vector must be a mapping")
        indices = value.get("indices")
        values = value.get("values")
        if (
            not isinstance(indices, list)
            or not isinstance(values, list)
            or len(indices) != len(values)
        ):
            raise ValueError(
                "Qdrant sparse vector indices and values must be lists of equal length"
            )
        self._get_sparse_dictionary()
        result: dict[str, float] = {}
        for index, weight in zip(indices, values, strict=True):
            term = self._resolve_sparse_index(int(index))
            if term is None:
                raise ValueError(f"unknown sparse term index: {index}")
            result[term] = float(weight)
        return result

    def update_data(self, data_list: list[dict[str, Any]]):
        pending: list[tuple[str, dict[str, Any]]] = []
        missing: list[Any] = []
        for item in data_list:
            if "id" not in item:
                raise ValueError("primary key 'id' is required for update")
            record_id = item["id"]
            points = self._retrieve_points(
                self._collection_name,
                [to_qdrant_point_id(record_id)],
                with_vectors=True,
            )
            if not points:
                missing.append(record_id)
                continue
            merged = self._vectors_to_record(points[0])
            merged.update(item)
            merged["id"] = record_id
            pending.append((str(record_id), merged))

        if missing:
            raise ValueError(f"record not found for primary key(s): {missing}")

        updated: list[str] = []
        for record_id, merged in pending:
            self.upsert_data([merged])
            updated.append(record_id)
        return updated

    def fetch_data(self, primary_keys: list[Any]) -> FetchDataInCollectionResult:
        if not primary_keys:
            return FetchDataInCollectionResult()
        points = self._retrieve_points(
            self._collection_name,
            [to_qdrant_point_id(value) for value in primary_keys],
            with_vectors=False,
        )
        items = []
        for point in points:
            record = self._payload_to_record(point)
            items.append(DataItem(id=record.get("id"), fields=record))
        found = {item.id for item in items}
        missing = [key for key in primary_keys if str(key) not in {str(item) for item in found}]
        return FetchDataInCollectionResult(items=items, ids_not_exist=missing)

    def delete_data(self, primary_keys: list[Any]):
        if not primary_keys:
            return {"status": "ok"}
        response = self._client.request(
            "POST",
            self._path(self._collection_name, "/points/delete"),
            {"points": [to_qdrant_point_id(value) for value in primary_keys]},
            params=self._strong_point_params(),
        )
        self._require_completed(response, "point deletion")
        return {"status": "ok"}

    def delete_all_data(self):
        response = self._client.request(
            "POST",
            self._path(self._collection_name, "/points/delete"),
            {"filter": {}},
            params=self._strong_point_params(),
        )
        self._require_completed(response, "point deletion")
        return True

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: str | None = None,
        filters: dict[str, Any] | None = None,
        cond: dict[str, Any] | None = None,
    ) -> AggregateResult:
        if field is not None or cond is not None:
            raise NotImplementedError(
                "Qdrant grouped or conditional aggregation is unsupported"
            )
        del index_name
        if op != "count":
            raise NotImplementedError(f"Qdrant aggregate operation is unsupported: {op}")
        response = self._client.request(
            "POST",
            self._path(self._collection_name, "/points/count"),
            {"filter": filters or {}, "exact": True},
        )
        result = self._result(response)
        count = result.get("count", 0) if isinstance(result, dict) else 0
        return AggregateResult(agg={"_total": int(count)}, op="count")

    def _payload_selector(self, output_fields: list[str] | None) -> bool | dict[str, list[str]]:
        if output_fields is None:
            return True
        fields = list(dict.fromkeys([*output_fields, "_openviking_original_id"]))
        return {"include": fields}

    def _search_one(
        self,
        *,
        vector: Any,
        using: str,
        filter: dict[str, Any],
        limit: int,
        offset: int,
        output_fields: list[str] | None,
    ) -> list[_Hit]:
        query = vector.get("vector") if isinstance(vector, dict) and "vector" in vector else vector
        body = {
            "query": query,
            "using": using,
            "filter": filter,
            "limit": limit,
            "offset": offset,
            "with_payload": self._payload_selector(output_fields),
            "with_vector": False,
        }
        response = self._client.request(
            "POST",
            self._path(self._collection_name, "/points/query"),
            body,
        )
        result = self._result(response)
        if isinstance(result, dict):
            result = result.get("points")
        if not isinstance(result, list):
            raise QdrantError("Qdrant search response did not contain a result list")
        hits: list[_Hit] = []
        for point in result:
            if not isinstance(point, dict):
                continue
            record = self._payload_to_record(point)
            hits.append(
                _Hit(
                    point_id=str(point.get("id")),
                    item=SearchItemResult(
                        id=record.get("id"),
                        fields=record,
                        score=float(point.get("score") or 0.0),
                    ),
                )
            )
        return hits

    def search_by_vector(
        self,
        index_name: str,
        dense_vector: list[float] | None = None,
        limit: int = 10,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
        sparse_vector: dict[str, float] | None = None,
        output_fields: list[str] | None = None,
    ) -> SearchResult:
        del index_name
        if sparse_vector and not self._sparse_enabled:
            raise ValueError("Qdrant collection was created without sparse-vector support")
        if dense_vector is not None and len(dense_vector) != self._vector_dim:
            raise ValueError(
                "Qdrant dense query vector dimension must be "
                f"{self._vector_dim}, got {len(dense_vector)}"
            )
        if dense_vector is not None and not all(
            math.isfinite(float(value)) for value in dense_vector
        ):
            raise ValueError("Qdrant dense query vector values must be finite")
        if sparse_vector is not None and not sparse_vector:
            raise ValueError("Qdrant sparse query vector must not be empty")
        qdrant_filter = filters or {}
        if dense_vector is None and sparse_vector is None:
            dense_vector = [random.uniform(-1, 1) for _ in range(self._vector_dim)]
        if dense_vector is not None and sparse_vector is not None:
            candidate_limit = max(limit + offset, limit * 2)
            dense_hits = self._search_one(
                vector={"vector": [float(value) for value in dense_vector]},
                using=self._dense_vector_name,
                filter=qdrant_filter,
                limit=candidate_limit,
                offset=0,
                output_fields=output_fields,
            )
            sparse = self.encode_sparse_vector(sparse_vector)
            sparse_hits = self._search_one(
                vector={"indices": sparse["indices"], "values": sparse["values"]},
                using=self._sparse_vector_name,
                filter=qdrant_filter,
                limit=candidate_limit,
                offset=0,
                output_fields=output_fields,
            )
            merged = self._weighted_rank_fusion(dense_hits, sparse_hits)
            return SearchResult(data=[hit.item for hit in merged[offset : offset + limit]])
        if dense_vector is not None:
            hits = self._search_one(
                vector={"vector": [float(value) for value in dense_vector]},
                using=self._dense_vector_name,
                filter=qdrant_filter,
                limit=limit,
                offset=offset,
                output_fields=output_fields,
            )
        else:
            sparse = self.encode_sparse_vector(sparse_vector)
            hits = self._search_one(
                vector={"indices": sparse["indices"], "values": sparse["values"]},
                using=self._sparse_vector_name,
                filter=qdrant_filter,
                limit=limit,
                offset=offset,
                output_fields=output_fields,
            )
        return SearchResult(data=[hit.item for hit in hits])

    def _weighted_rank_fusion(self, dense: list[_Hit], sparse: list[_Hit]) -> list[_Hit]:
        alpha = min(max(self._sparse_weight, 0.0), 1.0)
        scores: dict[str, float] = {}
        hits: dict[str, _Hit] = {}
        for rank, hit in enumerate(dense, start=1):
            scores[hit.point_id] = scores.get(hit.point_id, 0.0) + (1.0 - alpha) / (60 + rank)
            hits[hit.point_id] = hit
        for rank, hit in enumerate(sparse, start=1):
            scores[hit.point_id] = scores.get(hit.point_id, 0.0) + alpha / (60 + rank)
            hits.setdefault(hit.point_id, hit)
        ordered = sorted(hits, key=lambda point_id: scores[point_id], reverse=True)
        return [
            _Hit(
                point_id=point_id,
                item=SearchItemResult(
                    id=hits[point_id].item.id,
                    fields=hits[point_id].item.fields,
                    score=scores[point_id],
                ),
            )
            for point_id in ordered
        ]

    def search_by_keywords(
        self,
        index_name: str,
        keywords: list[str] | None = None,
        query: str | None = None,
        limit: int = 10,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
        output_fields: list[str] | None = None,
    ) -> SearchResult:
        del index_name, keywords, query, limit, offset, filters, output_fields
        raise NotImplementedError(
            "Qdrant does not provide OpenViking content grep; use the filesystem fallback"
        )

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
        output_fields: list[str] | None = None,
    ) -> SearchResult:
        points = self._retrieve_points(
            self._collection_name, [to_qdrant_point_id(id)], with_vectors=True
        )
        if not points:
            return SearchResult()
        record = self._vectors_to_record(points[0])
        return self.search_by_vector(
            index_name,
            dense_vector=record.get("vector"),
            sparse_vector=record.get("sparse_vector"),
            limit=limit,
            offset=offset,
            filters=filters,
            output_fields=output_fields,
        )

    def search_by_multimodal(self, *args: Any, **kwargs: Any) -> SearchResult:
        del args, kwargs
        raise NotImplementedError("Qdrant multimodal search is not supported")

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
        output_fields: list[str] | None = None,
    ) -> SearchResult:
        return self.search_by_vector(
            index_name,
            dense_vector=None,
            sparse_vector=None,
            limit=limit,
            offset=offset,
            filters=filters,
            output_fields=output_fields,
        )

    def search_by_scalar(
        self,
        index_name: str,
        field: str,
        order: str | None = "desc",
        limit: int = 10,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
        output_fields: list[str] | None = None,
    ) -> SearchResult:
        del index_name
        scalar_output_fields = None if output_fields is None else list(output_fields)
        remove_sort_field = False
        if scalar_output_fields is not None and field not in scalar_output_fields:
            scalar_output_fields.append(field)
            remove_sort_field = True
        points = self._scroll(
            self._collection_name,
            filter=filters,
            limit=limit + offset,
            with_vectors=False,
            order_by={"key": field, "direction": "desc" if order == "desc" else "asc"},
            output_fields=scalar_output_fields,
        )
        items = []
        for point in points[offset : offset + limit]:
            record = self._payload_to_record(point)
            value = record.get(field)
            items.append(
                SearchItemResult(
                    id=record.get("id"),
                    fields=record,
                    score=float(value) if isinstance(value, (int, float)) else 0.0,
                )
            )
            if remove_sort_field:
                record.pop(field, None)
        return SearchResult(data=items)

    def _get_sparse_dictionary(self) -> SparseTermDictionary:
        if not self._sparse_enabled:
            raise ValueError("Qdrant collection was created without sparse-vector support")
        if self._sparse_dictionary is None:
            self._sparse_dictionary = SparseTermDictionary(
                resolve_term=self._resolve_sparse_term,
                resolve_index=self._resolve_sparse_index,
                persist=self._persist_sparse_term,
            )
        return self._sparse_dictionary

    def encode_sparse_vector(self, vector: dict[str, float]) -> dict[str, list[Any]]:
        return self._get_sparse_dictionary().encode(vector) or {"indices": [], "values": []}

    def _sparse_provenance(self) -> tuple[str, str] | None:
        if self._migration_marker_fields is None and self._logical_collection is not None:
            self._load_metadata_marker()
        migration_id = (
            self._migration_marker_fields.get("migration_id")
            if isinstance(self._migration_marker_fields, dict)
            else None
        )
        if (
            isinstance(self._logical_collection, str)
            and self._logical_collection.strip()
            and isinstance(migration_id, str)
            and migration_id.strip()
        ):
            return self._logical_collection, migration_id
        return None

    def _parse_sparse_binding(self, point: Mapping[str, Any]) -> tuple[str, int]:
        term, index = parse_sparse_point(point)
        payload = point["payload"]
        logical_present = "logical_collection" in payload
        migration_present = "migration_id" in payload
        if logical_present != migration_present:
            raise ValueError("sparse dictionary point has incomplete migration provenance")
        if not logical_present:
            return term, index

        logical_collection = payload.get("logical_collection")
        migration_id = payload.get("migration_id")
        if (
            not isinstance(logical_collection, str)
            or not logical_collection.strip()
            or not isinstance(migration_id, str)
            or not migration_id.strip()
        ):
            raise ValueError("sparse dictionary point has invalid migration provenance")
        if self._sparse_provenance() != (logical_collection, migration_id):
            raise ValueError("sparse dictionary point has foreign migration provenance")
        return term, index

    def _resolve_sparse_term(self, term: str) -> int | None:
        points = self._scroll(
            self._metadata_collection_name,
            filter={"must": [{"key": "term", "match": {"value": term}}]},
            limit=3,
        )
        if not points:
            return None
        bindings = [self._parse_sparse_binding(point) for point in points]
        point_ids = [str(point.get("id")) for point in points]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError(f"sparse term lookup has duplicate point ids for term={term!r}")
        if any(found_term != term for found_term, _ in bindings):
            raise ValueError(f"sparse term lookup returned an unrelated binding for {term!r}")
        indexes = {index for _, index in bindings}
        if len(indexes) > 1:
            raise ValueError(
                "sparse term index collision: "
                f"term={term!r} existing_indices={sorted(indexes)!r}"
            )
        if len(bindings) > 2:
            raise ValueError(f"sparse term lookup has duplicate bindings for term={term!r}")
        index = next(iter(indexes))
        owner = self._resolve_sparse_index(index)
        if owner != term:
            raise ValueError(
                "sparse term index collision: "
                f"index={index} existing_term={owner!r} new_term={term!r}"
            )
        return index

    def _resolve_sparse_index(self, index: int) -> str | None:
        points = self._scroll(
            self._metadata_collection_name,
            filter={"must": [{"key": "index", "match": {"value": int(index)}}]},
            limit=3,
        )
        if not points:
            return None
        bindings = [self._parse_sparse_binding(point) for point in points]
        point_ids = [str(point.get("id")) for point in points]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError(f"sparse index lookup has duplicate point ids for index={index}")
        if any(found_index != int(index) for _, found_index in bindings):
            raise ValueError(f"sparse index lookup returned an unrelated binding for index={index}")
        if len(bindings) > 2:
            raise ValueError(f"sparse index lookup has duplicate bindings for index={index}")
        terms = {term for term, _ in bindings}
        if len(terms) > 1:
            raise ValueError(
                f"sparse term index collision: index={index} existing_terms={sorted(terms)!r}"
            )
        return next(iter(terms), None)

    def _persist_sparse_term(self, term: str, index: int) -> None:
        owner_id = sparse_owner_point_id(int(index))
        payload = {
            "_openviking_sparse_term": True,
            "term": term,
            "index": int(index),
        }
        provenance = self._sparse_provenance()
        if provenance is not None:
            payload.update(
                {
                    "logical_collection": provenance[0],
                    "migration_id": provenance[1],
                }
            )
        point = {
            "id": owner_id,
            "vector": {_META_VECTOR_NAME: [0.0]},
            "payload": payload,
        }
        self._client.ensure_supported_version()
        response = self._client.request(
            "PUT",
            self._path(self._metadata_collection_name, "/points"),
            {
                "points": [point],
                "update_filter": {
                    "must_not": [{"has_id": [owner_id]}],
                },
            },
            params=self._strong_point_params(),
        )
        self._require_completed(response, "sparse term owner registration")
        points = self._retrieve_points(
            self._metadata_collection_name,
            [owner_id],
            with_vectors=False,
        )
        if len(points) != 1:
            raise ValueError(
                f"sparse term owner is not visible after persistence: index={index}"
            )
        if str(points[0].get("id")) != owner_id:
            raise ValueError(
                f"sparse term owner readback returned the wrong point: expected={owner_id!r}"
            )
        persisted_term, persisted_index = self._parse_sparse_binding(points[0])
        if (persisted_term, persisted_index) != (term, int(index)):
            raise ValueError(
                "sparse term owner changed during persistence: "
                f"expected={(term, int(index))!r} actual={(persisted_term, persisted_index)!r}"
            )
