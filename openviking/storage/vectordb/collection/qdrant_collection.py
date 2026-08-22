# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Qdrant collection implementation for the OpenViking Collection contract."""

from __future__ import annotations

import math
import random
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
from openviking.storage.vectordb.qdrant_sparse import SparseTermDictionary
from openviking.storage.vectordb.qdrant_utils import (
    build_qdrant_payload,
    to_qdrant_point_id,
)

_META_VERSION = 1
_META_MARKER_ID = to_qdrant_point_id("openviking:metadata")
_META_VECTOR_NAME = "meta"
_INTERNAL_PAYLOAD_FIELDS = {
    "uri_depth",
    "scope_roots",
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

    @staticmethod
    def _normalize_distance(distance: str) -> str:
        value = str(distance or "cosine").strip().lower()
        mapping = {"cosine": "Cosine", "ip": "Dot", "dot": "Dot", "l2": "Euclid", "euclid": "Euclid"}
        if value not in mapping:
            raise ValueError(f"Unsupported Qdrant distance metric: {distance!r}")
        return mapping[value]

    @staticmethod
    def _result(response: dict[str, Any]) -> Any:
        return response.get("result", response)

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

    def _create_collection(self, name: str, *, metadata: bool = False) -> None:
        if self._exists(name):
            return
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
            self._client.request("PUT", self._path(name), body, params={"wait": "true"})
        except QdrantError as exc:
            if exc.status != 409:
                raise

    def create_remote_collection(self, metadata: dict[str, Any]) -> None:
        self._schema = dict(metadata)
        if self._vector_dim <= 0:
            for field in self._schema.get("Fields", []):
                if str(field.get("FieldType", "")).lower() == "vector" and field.get("Dim"):
                    self._vector_dim = int(field["Dim"])
                    break
        if self._vector_dim <= 0:
            raise ValueError("Qdrant backend requires a positive dense vector dimension")
        self._create_collection(self._collection_name)
        self._create_collection(self._metadata_collection_name, metadata=True)
        self._write_metadata_marker()

    def has_openviking_metadata(self) -> bool:
        payload = self._load_metadata_marker()
        return bool(
            payload
            and payload.get("_openviking_meta_version") == _META_VERSION
            and payload.get("collection_name") == self._collection_name
            and isinstance(payload.get("schema"), dict)
        )

    def _metadata_payload(self) -> dict[str, Any]:
        return {
            "_openviking_meta_version": _META_VERSION,
            "collection_name": self._collection_name,
            "schema": self._schema,
            "dense_vector_name": self._dense_vector_name,
            "sparse_vector_name": self._sparse_vector_name,
            "vector_dim": self._vector_dim,
            "distance": self._distance,
            "sparse_enabled": self._sparse_enabled,
            "sparse_weight": self._sparse_weight,
            "indexes": self._indexes,
        }

    def _write_metadata_marker(self) -> None:
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

    def _load_metadata_marker(self) -> dict[str, Any] | None:
        if not self._exists(self._metadata_collection_name):
            return None
        points = self._retrieve_points(
            self._metadata_collection_name,
            [_META_MARKER_ID],
            with_vectors=False,
        )
        if not points:
            return None
        payload = points[0].get("payload")
        return payload if isinstance(payload, dict) else None

    def _ensure_loaded(self) -> None:
        if self._schema:
            return
        marker = self._load_metadata_marker()
        if not marker:
            raise RuntimeError(
                f"Qdrant collection {self._collection_name!r} is missing OpenViking metadata"
            )
        if marker.get("collection_name") != self._collection_name:
            raise RuntimeError(
                f"Qdrant metadata collection does not belong to {self._collection_name!r}"
            )
        self._schema = dict(marker["schema"])
        self._vector_dim = int(marker.get("vector_dim") or self._vector_dim)
        self._dense_vector_name = str(
            marker.get("dense_vector_name") or self._dense_vector_name
        )
        self._sparse_vector_name = str(
            marker.get("sparse_vector_name") or self._sparse_vector_name
        )
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

    def update(self, fields: dict[str, Any] | None = None, description: str | None = None):
        self._ensure_loaded()
        if fields:
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
                self._client.request("DELETE", self._path(name), params={"timeout": 30})
        self._schema.clear()
        return True

    def _field_schema(self, field: str) -> str:
        fields = self._schema.get("Fields", [])
        for item in fields:
            if item.get("FieldName") != field:
                continue
            field_type = str(item.get("FieldType") or "").lower()
            if field_type.startswith("list<") and field_type.endswith(">"):
                field_type = field_type[5:-1]
            if field_type in {
                "int",
                "int8",
                "int16",
                "int32",
                "int64",
                "uint",
                "uint8",
                "uint16",
                "uint32",
                "uint64",
            }:
                return "integer"
            if field_type in {"float", "float16", "float32", "float64", "double"}:
                return "float"
            if field_type in {"bool", "boolean"}:
                return "bool"
            if field_type in {"date_time", "datetime"}:
                return "datetime"
            return "keyword"
        return "keyword"

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
                "field_schema": (
                    "integer" if field == "uri_depth" else self._field_schema(field)
                ),
            }
            try:
                self._client.request(
                    "PUT",
                    self._path(self._collection_name, "/index"),
                    body,
                    params={"wait": "true"},
                )
            except QdrantError as exc:
                if exc.status != 409:
                    raise

    def _delete_remote_indexes(self, fields: list[str]) -> None:
        for field in dict.fromkeys(fields):
            try:
                self._client.request(
                    "DELETE",
                    self._path(
                        self._collection_name,
                        f"/index/{quote(field, safe='')}",
                    ),
                    params={"wait": "true"},
                )
            except QdrantError as exc:
                if exc.status != 404:
                    raise

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
        self._client.request(
            "PUT",
            self._path(collection_name, "/points"),
            {"points": points},
            params={"wait": "true"},
        )

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
        )
        result = self._result(response)
        return result if isinstance(result, list) else []

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
            )
            result = self._result(response)
            if not isinstance(result, dict):
                break
            page = result.get("points")
            if not isinstance(page, list) or not page:
                break
            points.extend(point for point in page if isinstance(point, dict))
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
        self._upsert_points(self._collection_name, [self._point_from_record(item) for item in data_list])
        return {"status": "ok"}

    def _payload_to_record(self, point: dict[str, Any]) -> dict[str, Any]:
        payload = dict(point.get("payload") or {})
        original_id = payload.pop("_openviking_original_id", None)
        if original_id is None:
            original_id = point.get("id")
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
        items = [
            DataItem(
                id=self._payload_to_record(point).get("id"),
                fields=self._payload_to_record(point),
            )
            for point in points
        ]
        found = {item.id for item in items}
        missing = [key for key in primary_keys if str(key) not in {str(item) for item in found}]
        return FetchDataInCollectionResult(items=items, ids_not_exist=missing)

    def delete_data(self, primary_keys: list[Any]):
        if not primary_keys:
            return {"status": "ok"}
        self._client.request(
            "POST",
            self._path(self._collection_name, "/points/delete"),
            {"points": [to_qdrant_point_id(value) for value in primary_keys]},
            params={"wait": "true"},
        )
        return {"status": "ok"}

    def delete_all_data(self):
        self._client.request(
            "POST",
            self._path(self._collection_name, "/points/delete"),
            {"filter": {}},
            params={"wait": "true"},
        )
        return True

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: str | None = None,
        filters: dict[str, Any] | None = None,
        cond: dict[str, Any] | None = None,
    ) -> AggregateResult:
        del index_name, field, cond
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
        points = self._retrieve_points(self._collection_name, [to_qdrant_point_id(id)], with_vectors=True)
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
        points = self._scroll(
            self._collection_name,
            filter=filters,
            limit=limit + offset,
            with_vectors=False,
            order_by={"key": field, "direction": "desc" if order == "desc" else "asc"},
            output_fields=output_fields,
        )
        items = []
        for point in points[offset : offset + limit]:
            record = self._payload_to_record(point)
            items.append(
                SearchItemResult(
                    id=record.get("id"),
                    fields=record,
                    score=float(record.get(field) or 0.0)
                    if isinstance(record.get(field), (int, float))
                    else 0.0,
                )
            )
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

    def _resolve_sparse_term(self, term: str) -> int | None:
        points = self._scroll(
            self._metadata_collection_name,
            filter={"must": [{"key": "term", "match": {"value": term}}]},
            limit=1,
        )
        if not points:
            return None
        value = points[0].get("payload", {}).get("index")
        if value is None:
            return None
        index = int(value)
        owner = self._resolve_sparse_index(index)
        if owner is not None and owner != term:
            raise ValueError(
                "sparse term index collision: "
                f"index={index} existing_term={owner!r} new_term={term!r}"
            )
        return index

    def _resolve_sparse_index(self, index: int) -> str | None:
        points = self._scroll(
            self._metadata_collection_name,
            filter={"must": [{"key": "index", "match": {"value": int(index)}}]},
            limit=2,
        )
        if not points:
            return None
        terms = {
            str(value)
            for point in points
            if (value := point.get("payload", {}).get("term")) is not None
        }
        if len(terms) > 1:
            raise ValueError(
                "sparse term index collision: "
                f"index={index} existing_terms={sorted(terms)!r}"
            )
        return next(iter(terms), None)

    def _persist_sparse_term(self, term: str, index: int) -> None:
        self._upsert_points(
            self._metadata_collection_name,
            [
                {
                    "id": to_qdrant_point_id(f"openviking:sparse:{term}"),
                    "vector": {_META_VECTOR_NAME: [0.0]},
                    "payload": {
                        "_openviking_sparse_term": True,
                        "term": term,
                        "index": int(index),
                    },
                }
            ],
        )
