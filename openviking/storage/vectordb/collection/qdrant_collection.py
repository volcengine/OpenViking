# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Qdrant-backed ICollection implementation for OpenViking."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

import xxhash

from openviking.storage.vectordb.collection.collection import ICollection
from openviking.storage.vectordb.collection.qdrant_meta_store import QdrantMetaStore
from openviking.storage.vectordb.collection.qdrant_rest import QdrantRestClient, QdrantRestError
from openviking.storage.vectordb.collection.result import (
    AggregateResult,
    DataItem,
    FetchDataInCollectionResult,
    SearchItemResult,
    SearchResult,
)
from openviking.storage.vectordb.index.index import IIndex
from openviking.storage.vectordb.store.data import DeltaRecord
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

QDRANT_SPARSE_INDEX_MAX = 2**31 - 1
QDRANT_UINT64_MAX = 2**64 - 1
OPENVIKING_QDRANT_ID_NAMESPACE = uuid.UUID("4b6bb5a8-7f1f-5b1a-9d4c-b93f29b1d67c")
ORIGINAL_ID_FIELD = "_openviking_original_id"


def _coerce_iso_datetime(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.isoformat()
    return value


def _normalize_distance(distance: str) -> str:
    distance_value = (distance or "cosine").strip().lower()
    mapping = {
        "cosine": "Cosine",
        "ip": "Dot",
        "dot": "Dot",
        "l2": "Euclid",
        "euclid": "Euclid",
        "manhattan": "Manhattan",
    }
    return mapping.get(distance_value, "Cosine")


def _payload_selector(output_fields: Optional[List[str]]) -> bool | Dict[str, List[str]]:
    if not output_fields:
        return True
    include_fields = [field for field in output_fields if field != "id"]
    if ORIGINAL_ID_FIELD not in include_fields:
        include_fields.append(ORIGINAL_ID_FIELD)
    return {"include": include_fields} if include_fields else True


def _parse_point_id(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return str(value)


def _to_qdrant_point_id(value: Any) -> int | str:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= QDRANT_UINT64_MAX:
        return value
    value_str = str(value)
    try:
        return str(uuid.UUID(value_str))
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid5(OPENVIKING_QDRANT_ID_NAMESPACE, value_str))


def _extract_points(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = response.get("result", response)
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        if isinstance(result.get("points"), list):
            return result.get("points", [])
        if isinstance(result.get("result"), list):
            return result.get("result", [])
    return []


def _hash_sparse_term(term: str) -> int:
    # Keep sparse indices in the positive signed 32-bit integer range expected
    # by Qdrant sparse vectors.
    return xxhash.xxh64_intdigest(str(term)) % QDRANT_SPARSE_INDEX_MAX


def _sparse_to_qdrant(sparse_vector: Optional[Dict[str, float]]) -> Optional[Dict[str, List[Any]]]:
    if not sparse_vector:
        return None
    merged: Dict[int, float] = {}
    hashed_terms: Dict[int, str] = {}
    collisions: List[tuple[str, str, int]] = []
    for raw_term, raw_weight in sparse_vector.items():
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            continue
        idx = _hash_sparse_term(str(raw_term))
        previous_term = hashed_terms.get(idx)
        if previous_term is not None and previous_term != str(raw_term):
            collisions.append((previous_term, str(raw_term), idx))
        else:
            hashed_terms[idx] = str(raw_term)
        merged[idx] = merged.get(idx, 0.0) + weight
    if collisions:
        sample = ", ".join(
            f"{left!r}<->{right!r}@{idx}" for left, right, idx in collisions[:3]
        )
        logger.warning(
            "Qdrant sparse term hash collision detected; colliding terms are merged into the same sparse index bucket. count=%s sample=%s",
            len(collisions),
            sample,
        )
    if not merged:
        return None
    indices = sorted(merged.keys())
    values = [merged[idx] for idx in indices]
    return {"indices": indices, "values": values}


class QdrantIndex(IIndex):
    """Lightweight logical index facade backed by Qdrant payload/vector config."""

    def __init__(self, collection: "QdrantCollection", index_name: str, meta: Dict[str, Any]) -> None:
        super().__init__(meta=meta)
        self._collection = collection
        self._index_name = index_name
        self._meta = dict(meta)

    def upsert_data(self, delta_list: List[DeltaRecord]):
        raise NotImplementedError("QdrantIndex.upsert_data is managed at collection level")

    def delete_data(self, delta_list: List[DeltaRecord]):
        raise NotImplementedError("QdrantIndex.delete_data is managed at collection level")

    def search(
        self,
        query_vector: Optional[List[float]],
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        sparse_raw_terms: Optional[List[str]] = None,
        sparse_values: Optional[List[float]] = None,
    ) -> Tuple[List[int], List[float]]:
        raise NotImplementedError("QdrantIndex.search is not exposed via raw index interface")

    def aggregate(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raise NotImplementedError("QdrantIndex.aggregate is not exposed via raw index interface")

    def update(self, scalar_index: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        self._collection.update_index(
            index_name=self._index_name,
            scalar_index=scalar_index,
            description=description,
        )
        self._meta = self._collection.get_index_meta_data(self._index_name) or self._meta

    def get_meta_data(self):
        return dict(self._meta)

    def close(self):
        return None

    def drop(self):
        self._collection.drop_index(self._index_name)


class QdrantCollection(ICollection):
    """Qdrant-backed collection implementation."""

    DEFAULT_SCROLL_PAGE_SIZE = 256
    DEFAULT_DELETE_BATCH_SIZE = 1_000
    DEFAULT_HYBRID_PREFETCH_LIMIT = 32
    MAX_HYBRID_PREFETCH_LIMIT = 256
    MAX_SCROLL_ITERATIONS = 10_000
    CLIENT_SIDE_SCAN_WARNING_THRESHOLD = 10_000
    TEXT_INDEX_FIELDS = {"name", "description", "abstract", "tags"}

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None,
        timeout_seconds: int,
        project_name: str,
        logical_collection_name: str,
        physical_collection_name: str,
        dense_vector_name: str,
        sparse_vector_name: str,
        meta_collection_name: str,
        distance_metric: str,
        enable_text_index: bool = False,
    ) -> None:
        super().__init__()
        self._client = QdrantRestClient(
            url=url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
        self._project_name = project_name
        self._logical_collection_name = logical_collection_name
        self._physical_collection_name = physical_collection_name
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._distance_metric = distance_metric
        self._enable_text_index = enable_text_index
        self._meta_store = QdrantMetaStore(
            client=self._client,
            meta_collection_name=meta_collection_name,
        )
        self._vector_dim = 0
        self._path_payload_fields: set[str] = {"parent_uri", "scope_roots"}
        self._refresh_vector_dim_from_meta()

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------
    @property
    def collection_key(self) -> str:
        return self._physical_collection_name

    def collection_exists(self) -> bool:
        return self._client.collection_exists(self._physical_collection_name)

    def _apply_meta_schema(self, meta: Dict[str, Any]) -> None:
        path_payload_fields = {"parent_uri", "scope_roots"}
        for field in meta.get("Fields", []):
            if field.get("FieldType") == "path":
                field_name = field.get("FieldName")
                if field_name:
                    path_payload_fields.add(str(field_name))
            if field.get("FieldType") == "vector":
                try:
                    self._vector_dim = int(field.get("Dim") or 0)
                except (TypeError, ValueError):
                    self._vector_dim = 0
        self._path_payload_fields = path_payload_fields

    def _refresh_vector_dim_from_meta(self) -> None:
        meta = self._meta_store.get_collection_meta(collection_key=self.collection_key)
        if not meta:
            return
        self._apply_meta_schema(meta)

    @staticmethod
    def _decode_path_value(value: Any) -> Any:
        if isinstance(value, list):
            return [QdrantCollection._decode_path_value(item) for item in value]
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped.startswith("viking://") or not stripped.startswith("/"):
            return value
        suffix = stripped.strip("/")
        return f"viking://{suffix}" if suffix else "viking://"

    def _normalize_payload_for_read(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not payload:
            return {}
        normalized = dict(payload)
        for field_name in self._path_payload_fields:
            if field_name in normalized:
                normalized[field_name] = self._decode_path_value(normalized[field_name])
        return normalized

    def _build_vector_config(self, meta_data: Dict[str, Any]) -> Dict[str, Any]:
        dense_dim = 0
        has_sparse = False
        for field in meta_data.get("Fields", []):
            field_type = field.get("FieldType")
            if field_type == "vector":
                dense_dim = int(field.get("Dim") or 0)
            elif field_type == "sparse_vector":
                has_sparse = True

        if dense_dim <= 0:
            raise ValueError("Qdrant collection requires a positive dense vector dimension")

        self._vector_dim = dense_dim
        body: Dict[str, Any] = {
            "vectors": {
                self._dense_vector_name: {
                    "size": dense_dim,
                    "distance": _normalize_distance(self._distance_metric),
                }
            }
        }
        if has_sparse:
            body["sparse_vectors"] = {self._sparse_vector_name: {}}
        return body

    def create_remote_collection(self, meta_data: Dict[str, Any]) -> None:
        body = self._build_vector_config(meta_data)
        self._client.request(
            "PUT",
            f"/collections/{self._physical_collection_name}",
            json_body=body,
            expected_statuses=(200, 201),
        )
        try:
            self._meta_store.save_collection_meta(
                collection_key=self.collection_key,
                logical_collection_name=self._logical_collection_name,
                project_name=self._project_name,
                meta=meta_data,
            )
        except Exception:
            try:
                self._client.request(
                    "DELETE",
                    f"/collections/{self._physical_collection_name}",
                    expected_statuses=(200, 202, 404),
                )
            except Exception as cleanup_error:
                logger.warning(
                    "Failed to rollback Qdrant collection after metadata write failure: collection=%s error=%s",
                    self._physical_collection_name,
                    cleanup_error,
                )
            raise
        self._apply_meta_schema(meta_data)

    def _make_point(self, record: Dict[str, Any]) -> Dict[str, Any]:
        original_id = record.get("id")
        if original_id is None or original_id == "":
            raise ValueError("Qdrant point requires id")

        dense_vector = record.get(self._dense_vector_name)
        sparse_vector = record.get(self._sparse_vector_name)
        payload = {
            key: _coerce_iso_datetime(value)
            for key, value in record.items()
            if key not in {"id", self._dense_vector_name, self._sparse_vector_name}
        }
        payload[ORIGINAL_ID_FIELD] = _parse_point_id(original_id)

        vector_payload: Dict[str, Any] = {}
        if isinstance(dense_vector, list) and dense_vector:
            vector_payload[self._dense_vector_name] = dense_vector

        sparse_payload = _sparse_to_qdrant(sparse_vector)
        if sparse_payload:
            vector_payload[self._sparse_vector_name] = sparse_payload

        if not vector_payload:
            raise ValueError("Qdrant point requires at least one dense or sparse vector")

        return {
            "id": _to_qdrant_point_id(original_id),
            "vector": vector_payload,
            "payload": payload,
        }

    def _point_payload_for_read(self, point: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        raw_payload = dict(point.get("payload", {}) or {})
        original_id = raw_payload.pop(ORIGINAL_ID_FIELD, None)
        if original_id is None:
            original_id = _parse_point_id(point.get("id"))
        return original_id, self._normalize_payload_for_read(raw_payload)

    def _parse_search_result(
        self,
        response: Dict[str, Any],
        *,
        offset: int,
        limit: int,
    ) -> SearchResult:
        items: List[SearchItemResult] = []
        for point in _extract_points(response):
            if not isinstance(point, dict):
                continue
            original_id, payload = self._point_payload_for_read(point)
            score = point.get("score") if isinstance(point, dict) else None
            items.append(
                SearchItemResult(
                    id=original_id,
                    fields=payload,
                    score=score,
                )
            )
        if offset > 0:
            items = items[offset:]
        if len(items) > limit:
            items = items[:limit]
        return SearchResult(data=items)

    def _parse_points_as_search_result(
        self,
        points: Sequence[Dict[str, Any]],
        *,
        score: Optional[float] = 1.0,
    ) -> SearchResult:
        data: List[SearchItemResult] = []
        for point in points:
            original_id, payload = self._point_payload_for_read(point)
            data.append(
                SearchItemResult(
                    id=original_id,
                    fields=payload,
                    score=score,
                )
            )
        return SearchResult(data=data)

    def _extract_named_vectors(
        self,
        point: Dict[str, Any],
    ) -> Tuple[Optional[List[float]], Optional[Dict[str, List[Any]]]]:
        vectors = point.get("vector")
        if not isinstance(vectors, dict):
            return None, None

        dense_vector = vectors.get(self._dense_vector_name)
        sparse_vector = vectors.get(self._sparse_vector_name)
        if not isinstance(dense_vector, list):
            dense_vector = None
        if not isinstance(sparse_vector, dict):
            sparse_vector = None
        return dense_vector, sparse_vector

    def _scroll_points(
        self,
        *,
        filters: Optional[Dict[str, Any]] = None,
        with_payload: bool | Dict[str, List[str]] = True,
        limit: Optional[int] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        points: List[Dict[str, Any]] = []
        next_offset: Any = None
        seen_offsets: set[str] = set()
        iterations = 0

        while iterations < self.MAX_SCROLL_ITERATIONS:
            iterations += 1
            remaining = None if limit is None else max(limit - len(points), 0)
            if remaining == 0:
                break

            body: Dict[str, Any] = {
                "limit": min(
                    self.DEFAULT_SCROLL_PAGE_SIZE,
                    remaining if remaining is not None else self.DEFAULT_SCROLL_PAGE_SIZE,
                ),
                "with_payload": with_payload,
                "with_vector": False,
            }
            if filters:
                body["filter"] = filters
            if next_offset is not None:
                body["offset"] = next_offset
            if order_by:
                body["order_by"] = {
                    "key": order_by,
                    "direction": "desc" if order_desc else "asc",
                }

            response = self._client.request(
                "POST",
                f"/collections/{self._physical_collection_name}/points/scroll",
                json_body=body,
            )
            result = response.get("result", {})
            page_points = result.get("points", []) if isinstance(result, dict) else []
            if not page_points:
                break

            points.extend(page_points)
            next_offset = result.get("next_page_offset") if isinstance(result, dict) else None
            if next_offset is None:
                break
            offset_key = repr(next_offset)
            if offset_key in seen_offsets:
                logger.warning(
                    "Stopping Qdrant scroll early because next_page_offset repeated for collection=%s offset=%s",
                    self._physical_collection_name,
                    next_offset,
                )
                break
            seen_offsets.add(offset_key)
        else:
            logger.warning(
                "Stopping Qdrant scroll after reaching MAX_SCROLL_ITERATIONS=%s for collection=%s",
                self.MAX_SCROLL_ITERATIONS,
                self._physical_collection_name,
            )
        return points

    def _scan_points_with_warning(
        self,
        *,
        purpose: str,
        filters: Optional[Dict[str, Any]],
        with_payload: bool | Dict[str, List[str]],
        limit: Optional[int] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        if limit is None:
            logger.warning(
                "Qdrant %s uses client-side full scan/scroll. This may be expensive for large collections.",
                purpose,
            )
        elif limit >= self.CLIENT_SIDE_SCAN_WARNING_THRESHOLD:
            logger.warning(
                "Qdrant %s will scan up to %s points client-side. This may be expensive for large collections.",
                purpose,
                limit,
            )
        return self._scroll_points(
            filters=filters,
            with_payload=with_payload,
            limit=limit,
            order_by=order_by,
            order_desc=order_desc,
        )

    def _delete_field_index(self, field_name: str) -> None:
        try:
            self._client.request(
                "DELETE",
                f"/collections/{self._physical_collection_name}/index/{field_name}",
                params={"wait": "true"},
                expected_statuses=(200, 202),
            )
        except QdrantRestError as exc:
            logger.warning("Failed to delete Qdrant field index %s: %s", field_name, exc)

    def _field_type_map(self) -> Dict[str, str]:
        meta = self.get_meta_data() or {}
        fields = meta.get("Fields", [])
        mapping = {field.get("FieldName"): field.get("FieldType") for field in fields}
        mapping.setdefault("parent_uri", "string")
        mapping.setdefault("scope_roots", "string")
        mapping.setdefault("uri_depth", "int64")
        return {str(k): str(v) for k, v in mapping.items() if k}

    def _payload_field_schema(self, field_name: str, field_type: str) -> Optional[str]:
        normalized_type = (field_type or "").lower()
        if normalized_type in {"string", "path"}:
            if self._enable_text_index and field_name in self.TEXT_INDEX_FIELDS:
                return "text"
            return "keyword"
        if normalized_type in {"int64", "int32"}:
            return "integer"
        if normalized_type in {"float", "double"}:
            return "float"
        if normalized_type in {"bool", "boolean"}:
            return "bool"
        if normalized_type == "date_time":
            return "datetime"
        return None

    def _count_points(self, filters: Optional[Dict[str, Any]] = None) -> int:
        response = self._client.request(
            "POST",
            f"/collections/{self._physical_collection_name}/points/count",
            json_body={
                "filter": filters or {},
                "exact": True,
            },
        )
        result = response.get("result", {})
        try:
            return int(result.get("count", 0)) if isinstance(result, dict) else 0
        except (TypeError, ValueError):
            return 0

    def _delete_points(self, point_ids: Sequence[Any]) -> None:
        if not point_ids:
            return
        self._client.request(
            "POST",
            f"/collections/{self._physical_collection_name}/points/delete",
            json_body={"points": list(point_ids)},
            params={"wait": "true"},
            expected_statuses=(200, 202),
        )

    # ---------------------------------------------------------------------
    # ICollection
    # ---------------------------------------------------------------------
    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        meta = self._meta_store.update_collection_meta(
            collection_key=self.collection_key,
            logical_collection_name=self._logical_collection_name,
            project_name=self._project_name,
            fields=fields,
            description=description,
        )
        if isinstance(meta, dict):
            self._apply_meta_schema(meta)
        return meta

    def get_meta_data(self):
        return self._meta_store.get_collection_meta(collection_key=self.collection_key) or {}

    def has_openviking_metadata(self) -> bool:
        return bool(self._meta_store.get_collection_meta(collection_key=self.collection_key))

    def close(self):
        self._client.close()

    def drop(self):
        try:
            self._client.request(
                "DELETE",
                f"/collections/{self._physical_collection_name}",
                expected_statuses=(200, 202, 404),
            )
        finally:
            self._meta_store.delete_collection_meta(collection_key=self.collection_key)

    def create_index(self, index_name: str, meta_data: Dict[str, Any]):
        field_type_map = self._field_type_map()
        scalar_fields = list(meta_data.get("ScalarIndex", []) or [])
        for field_name in scalar_fields:
            field_schema = self._payload_field_schema(field_name, field_type_map.get(field_name, ""))
            if not field_schema:
                continue
            self._client.request(
                "PUT",
                f"/collections/{self._physical_collection_name}/index",
                json_body={
                    "field_name": field_name,
                    "field_schema": field_schema,
                },
                params={"wait": "true"},
                expected_statuses=(200, 202),
            )
        self._meta_store.save_index_meta(
            collection_key=self.collection_key,
            logical_collection_name=self._logical_collection_name,
            index_name=index_name,
            meta=meta_data,
        )
        return QdrantIndex(self, index_name, meta_data)

    def has_index(self, index_name: str) -> bool:
        return index_name in self.list_indexes()

    def get_index(self, index_name: str) -> Optional[IIndex]:
        meta = self.get_index_meta_data(index_name)
        if not meta:
            return None
        return QdrantIndex(self, index_name, meta)

    def search_by_vector(
        self,
        index_name: str,
        dense_vector: Optional[List[float]] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        sparse_vector: Optional[Dict[str, float]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        if dense_vector is None and sparse_vector is None:
            return SearchResult()

        fetch_limit = max(limit + offset, limit)
        body: Dict[str, Any] = {
            "limit": fetch_limit,
            "with_payload": _payload_selector(output_fields),
        }
        if filters:
            body["filter"] = filters

        try:
            if dense_vector is not None and sparse_vector:
                sparse_payload = _sparse_to_qdrant(sparse_vector)
                if not sparse_payload:
                    sparse_vector = None
                else:
                    hybrid_prefetch_limit = min(
                        max(fetch_limit * 4, fetch_limit + 8, self.DEFAULT_HYBRID_PREFETCH_LIMIT),
                        self.MAX_HYBRID_PREFETCH_LIMIT,
                    )
                    body.update(
                        {
                            "prefetch": [
                                {
                                    "query": dense_vector,
                                    "using": self._dense_vector_name,
                                    "limit": hybrid_prefetch_limit,
                                },
                                {
                                    "query": sparse_payload,
                                    "using": self._sparse_vector_name,
                                    "limit": hybrid_prefetch_limit,
                                },
                            ],
                            "query": {"fusion": "rrf"},
                        }
                    )
                    response = self._client.request(
                        "POST",
                        f"/collections/{self._physical_collection_name}/points/query",
                        json_body=body,
                        expected_statuses=(200, 202),
                    )
                    return self._parse_search_result(response, offset=offset, limit=limit)

            if sparse_vector and dense_vector is None:
                sparse_payload = _sparse_to_qdrant(sparse_vector)
                if not sparse_payload:
                    return SearchResult()
                body.update(
                    {
                        "query": sparse_payload,
                        "using": self._sparse_vector_name,
                    }
                )
                response = self._client.request(
                    "POST",
                    f"/collections/{self._physical_collection_name}/points/query",
                    json_body=body,
                    expected_statuses=(200, 202),
                )
                return self._parse_search_result(response, offset=offset, limit=limit)

            if dense_vector is None:
                return SearchResult()

            body.update(
                {
                    "query": dense_vector,
                    "using": self._dense_vector_name,
                }
            )
            response = self._client.request(
                "POST",
                f"/collections/{self._physical_collection_name}/points/query",
                json_body=body,
                expected_statuses=(200, 202),
            )
            return self._parse_search_result(response, offset=offset, limit=limit)
        except QdrantRestError as exc:
            if dense_vector is None:
                raise
            logger.warning(
                "Falling back to Qdrant /points/search for dense-only query because /points/query failed: %s",
                exc,
            )
            fallback_body: Dict[str, Any] = {
                "vector": {
                    "name": self._dense_vector_name,
                    "vector": dense_vector,
                },
                "limit": fetch_limit,
                "with_payload": _payload_selector(output_fields),
            }
            if filters:
                fallback_body["filter"] = filters
            response = self._client.request(
                "POST",
                f"/collections/{self._physical_collection_name}/points/search",
                json_body=fallback_body,
                expected_statuses=(200, 202),
            )
            return self._parse_search_result(response, offset=offset, limit=limit)

    def search_by_keywords(
        self,
        index_name: str,
        keywords: Optional[List[str]] = None,
        query: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        query_text = query or " ".join(keywords or [])
        if not query_text.strip():
            return SearchResult()

        text_filter = {
            "should": [
                {"must": [{"key": field_name, "match": {"text": query_text}}]}
                for field_name in sorted(self.TEXT_INDEX_FIELDS)
            ]
        }
        compound_filter = {"must": [filters, text_filter]} if filters else text_filter
        points = self._scroll_points(
            filters=compound_filter,
            with_payload=_payload_selector(output_fields),
            limit=limit + offset,
        )
        if offset > 0:
            points = points[offset:]
        if len(points) > limit:
            points = points[:limit]
        return SearchResult(
            data=self._parse_points_as_search_result(points, score=1.0).data
        )

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        point_id = _to_qdrant_point_id(id)
        response = self._client.request(
            "POST",
            f"/collections/{self._physical_collection_name}/points",
            json_body={
                "ids": [point_id],
                "with_payload": _payload_selector([ORIGINAL_ID_FIELD]),
                "with_vector": True,
            },
        )
        points = _extract_points(response)
        if not points:
            return SearchResult()

        dense_vector, sparse_payload = self._extract_named_vectors(points[0])
        if dense_vector is None and sparse_payload is None:
            return SearchResult()

        fetch_limit = max(limit + offset + 1, limit + 1)
        body: Dict[str, Any] = {
            "limit": fetch_limit,
            "with_payload": _payload_selector(output_fields),
        }
        if filters:
            body["filter"] = filters

        try:
            if dense_vector is not None and sparse_payload is not None:
                hybrid_prefetch_limit = min(
                    max(fetch_limit * 4, fetch_limit + 8, self.DEFAULT_HYBRID_PREFETCH_LIMIT),
                    self.MAX_HYBRID_PREFETCH_LIMIT,
                )
                body.update(
                    {
                        "prefetch": [
                            {
                                "query": dense_vector,
                                "using": self._dense_vector_name,
                                "limit": hybrid_prefetch_limit,
                            },
                            {
                                "query": sparse_payload,
                                "using": self._sparse_vector_name,
                                "limit": hybrid_prefetch_limit,
                            },
                        ],
                        "query": {"fusion": "rrf"},
                    }
                )
            elif sparse_payload is not None:
                body.update({"query": sparse_payload, "using": self._sparse_vector_name})
            else:
                body.update({"query": dense_vector, "using": self._dense_vector_name})

            search_response = self._client.request(
                "POST",
                f"/collections/{self._physical_collection_name}/points/query",
                json_body=body,
                expected_statuses=(200, 202),
            )
        except QdrantRestError as exc:
            if dense_vector is None:
                raise
            logger.warning(
                "Falling back to Qdrant /points/search for search_by_id because /points/query failed: %s",
                exc,
            )
            fallback_body: Dict[str, Any] = {
                "vector": {
                    "name": self._dense_vector_name,
                    "vector": dense_vector,
                },
                "limit": fetch_limit,
                "with_payload": _payload_selector(output_fields),
            }
            if filters:
                fallback_body["filter"] = filters
            search_response = self._client.request(
                "POST",
                f"/collections/{self._physical_collection_name}/points/search",
                json_body=fallback_body,
                expected_statuses=(200, 202),
            )

        data = []
        for item in self._parse_search_result(search_response, offset=0, limit=fetch_limit).data:
            if item.id == id or str(item.id) == str(id):
                continue
            data.append(item)
        return SearchResult(data=data[offset : offset + limit])

    def search_by_multimodal(
        self,
        index_name: str,
        text: Optional[str],
        image: Optional[Any],
        video: Optional[Any],
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError("QdrantCollection.search_by_multimodal is not supported")

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        points = self._scroll_points(
            filters=filters,
            with_payload=_payload_selector(output_fields),
            limit=limit + offset,
        )
        if offset > 0:
            points = points[offset:]
        if len(points) > limit:
            points = points[:limit]
        return SearchResult(
            data=self._parse_points_as_search_result(points, score=1.0).data
        )

    def search_by_scalar(
        self,
        index_name: str,
        field: str,
        order: Optional[str] = "desc",
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        need_cleanup = False
        with_payload = list(output_fields or [])
        if field not in with_payload:
            with_payload.append(field)
            need_cleanup = True

        reverse = (order or "desc").lower() == "desc"
        try:
            points = self._scan_points_with_warning(
                purpose="scalar sort",
                filters=filters,
                with_payload=_payload_selector(with_payload),
                limit=limit + offset,
                order_by=field,
                order_desc=reverse,
            )
        except QdrantRestError as exc:
            logger.warning(
                "Falling back to client-side scalar sort because Qdrant order_by is unavailable for field=%s: %s",
                field,
                exc,
            )
            points = self._scan_points_with_warning(
                purpose="scalar sort fallback",
                filters=filters,
                with_payload=_payload_selector(with_payload),
                limit=None,
            )
            points.sort(
                key=lambda point: (
                    point.get("payload", {}).get(field) is None,
                    point.get("payload", {}).get(field),
                ),
                reverse=reverse,
            )
        points = points[offset : offset + limit]
        data: List[SearchItemResult] = []
        for point in points:
            original_id, payload = self._point_payload_for_read(point)
            score = payload.get(field)
            if need_cleanup:
                payload.pop(field, None)
            data.append(
                SearchItemResult(
                    id=original_id,
                    fields=payload,
                    score=score if isinstance(score, (int, float)) else None,
                )
            )
        return SearchResult(data=data)

    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ):
        current_meta = self.get_index_meta_data(index_name) or {"IndexName": index_name}
        old_scalar_fields = set(current_meta.get("ScalarIndex", []) or [])

        if scalar_index is not None:
            if isinstance(scalar_index, dict):
                new_scalar_fields = list(scalar_index.keys())
            else:
                new_scalar_fields = list(scalar_index)
            current_meta["ScalarIndex"] = new_scalar_fields
        else:
            new_scalar_fields = list(current_meta.get("ScalarIndex", []) or [])

        if description is not None:
            current_meta["Description"] = description

        field_type_map = self._field_type_map()
        for field_name in new_scalar_fields:
            if field_name in old_scalar_fields:
                continue
            field_schema = self._payload_field_schema(field_name, field_type_map.get(field_name, ""))
            if not field_schema:
                continue
            self._client.request(
                "PUT",
                f"/collections/{self._physical_collection_name}/index",
                json_body={
                    "field_name": field_name,
                    "field_schema": field_schema,
                },
                params={"wait": "true"},
                expected_statuses=(200, 202),
            )

        for field_name in sorted(old_scalar_fields - set(new_scalar_fields)):
            self._delete_field_index(field_name)

        self._meta_store.save_index_meta(
            collection_key=self.collection_key,
            logical_collection_name=self._logical_collection_name,
            index_name=index_name,
            meta=current_meta,
        )
        return current_meta

    def get_index_meta_data(self, index_name: str):
        return self._meta_store.get_index_meta(collection_key=self.collection_key, index_name=index_name)

    def list_indexes(self):
        return self._meta_store.list_indexes(collection_key=self.collection_key)

    def drop_index(self, index_name: str):
        index_meta = self.get_index_meta_data(index_name) or {}
        for field_name in index_meta.get("ScalarIndex", []) or []:
            self._delete_field_index(field_name)
        self._meta_store.delete_index_meta(collection_key=self.collection_key, index_name=index_name)

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl=0):
        # Qdrant does not provide a collection-level per-point TTL primitive that
        # matches the existing ICollection contract, so ttl is accepted only for
        # interface compatibility and intentionally ignored here.
        points = [self._make_point(data) for data in data_list]
        self._client.request(
            "PUT",
            f"/collections/{self._physical_collection_name}/points",
            json_body={"points": points},
            params={"wait": "true"},
            expected_statuses=(200, 202),
        )
        return [data.get("id") for data in data_list]

    def fetch_data(self, primary_keys: List[Any]):
        if not primary_keys:
            return FetchDataInCollectionResult()
        response = self._client.request(
            "POST",
            f"/collections/{self._physical_collection_name}/points",
            json_body={
                "ids": [_to_qdrant_point_id(pk) for pk in primary_keys],
                "with_payload": True,
                "with_vector": False,
            },
        )
        points = _extract_points(response)
        items: List[DataItem] = []
        found_ids: set[str] = set()
        for point in points:
            if not isinstance(point, dict):
                continue
            original_id, payload = self._point_payload_for_read(point)
            found_ids.add(str(original_id))
            items.append(DataItem(id=original_id, fields=payload))
        return FetchDataInCollectionResult(
            items=items,
            ids_not_exist=[pk for pk in primary_keys if str(pk) not in found_ids],
        )

    def delete_data(self, primary_keys: List[Any]):
        self._delete_points([_to_qdrant_point_id(pk) for pk in primary_keys])

    def delete_all_data(self):
        try:
            self._client.request(
                "POST",
                f"/collections/{self._physical_collection_name}/points/delete",
                json_body={"filter": {}},
                params={"wait": "true"},
                expected_statuses=(200, 202),
            )
        except QdrantRestError as exc:
            logger.warning(
                "Falling back to batched delete_all_data for collection=%s because filter delete failed: %s",
                self._physical_collection_name,
                exc,
            )
            points = self._scan_points_with_warning(
                purpose="delete_all_data fallback",
                filters=None,
                with_payload=False,
                limit=None,
            )
            batch_ids: List[str] = []
            for point in points:
                batch_ids.append(_parse_point_id(point.get("id")))
                if len(batch_ids) >= self.DEFAULT_DELETE_BATCH_SIZE:
                    self._delete_points(batch_ids)
                    batch_ids = []
            if batch_ids:
                self._delete_points(batch_ids)

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        cond: Optional[Dict[str, Any]] = None,
    ) -> AggregateResult:
        if op != "count":
            return AggregateResult(agg={}, op=op, field=field)

        if not field:
            return AggregateResult(
                agg={"_total": self._count_points(filters)},
                op="count",
                field=None,
            )

        grouped: Dict[Any, int] = {}
        points = self._scan_points_with_warning(
            purpose=f"aggregate(count by {field})",
            filters=filters,
            with_payload=_payload_selector([field]),
            limit=None,
        )
        for point in points:
            payload = point.get("payload", {})
            value = payload.get(field)
            if value is None:
                continue
            if field in self._path_payload_fields:
                value = self._decode_path_value(value)
            grouped[value] = grouped.get(value, 0) + 1

        if cond:
            filtered_grouped: Dict[Any, int] = {}
            for key, value in grouped.items():
                include = True
                if cond.get("gt") is not None:
                    include = include and value > cond["gt"]
                if cond.get("gte") is not None:
                    include = include and value >= cond["gte"]
                if cond.get("lt") is not None:
                    include = include and value < cond["lt"]
                if cond.get("lte") is not None:
                    include = include and value <= cond["lte"]
                if include:
                    filtered_grouped[key] = value
            grouped = filtered_grouped

        return AggregateResult(agg=grouped, op="count", field=field)
