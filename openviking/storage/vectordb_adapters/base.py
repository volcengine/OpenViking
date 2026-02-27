# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Base adapter primitives for backend-specific vector collection operations."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse

from openviking.storage.errors import CollectionNotFoundError
from openviking.storage.expr import (
    And,
    Contains,
    Eq,
    FilterExpr,
    In,
    Or,
    Range,
    RawDSL,
    TimeRange,
)
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.result import FetchDataInCollectionResult
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def _parse_url(url: str) -> tuple[str, int]:
    normalized = url
    if not normalized.startswith(("http://", "https://")):
        normalized = f"http://{normalized}"
    parsed = urlparse(normalized)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5000
    return host, port


def _normalize_collection_names(raw_collections: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for item in raw_collections:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("CollectionName") or item.get("collection_name") or item.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


class CollectionAdapter(ABC):
    """Backend-specific adapter for single-collection operations.

    Public API methods are kept without prefix (create/query/upsert/delete/count...).
    Internal extension hooks for subclasses use leading underscore.
    """

    mode: str

    def __init__(self, collection_name: str):
        self._collection_name = collection_name
        self._collection: Optional[Collection] = None

    @property
    def collection_name(self) -> str:
        return self._collection_name

    @classmethod
    @abstractmethod
    def from_config(cls, config: Any) -> "CollectionAdapter":
        """Create an adapter instance from VectorDB backend config."""

    @abstractmethod
    def _load_existing_collection_if_needed(self) -> None:
        """Load existing bound collection handle when possible."""

    @abstractmethod
    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        """Create backend collection handle for bound collection."""

    def collection_exists(self) -> bool:
        self._load_existing_collection_if_needed()
        return self._collection is not None

    def get_collection(self) -> Collection:
        self._load_existing_collection_if_needed()
        if self._collection is None:
            raise CollectionNotFoundError(f"Collection {self._collection_name} does not exist")
        return self._collection

    def create_collection(
        self,
        name: str,
        schema: Dict[str, Any],
        *,
        distance: str,
        sparse_weight: float,
        index_name: str,
    ) -> bool:
        if self.collection_exists():
            return False

        self._collection_name = name
        collection_meta = dict(schema)
        scalar_index_fields = collection_meta.pop("ScalarIndex", [])
        if "CollectionName" not in collection_meta:
            collection_meta["CollectionName"] = name

        self._collection = self._create_backend_collection(collection_meta)

        scalar_index_fields = self._sanitize_scalar_index_fields(
            scalar_index_fields=scalar_index_fields,
            fields_meta=collection_meta.get("Fields", []),
        )
        index_meta = self._build_default_index_meta(
            index_name=index_name,
            distance=distance,
            use_sparse=sparse_weight > 0.0,
            sparse_weight=sparse_weight,
            scalar_index_fields=scalar_index_fields,
        )
        self._collection.create_index(index_name, index_meta)
        return True

    def drop_collection(self) -> bool:
        if not self.collection_exists():
            return False

        coll = self.get_collection()

        # Drop indexes first so index lifecycle remains internal to adapter.
        try:
            for index_name in coll.list_indexes() or []:
                try:
                    coll.drop_index(index_name)
                except Exception as e:
                    logger.warning("Failed to drop index %s: %s", index_name, e)
        except Exception as e:
            logger.warning("Failed to list indexes before dropping collection: %s", e)

        try:
            coll.drop()
        except NotImplementedError:
            logger.warning("Collection drop is not supported by backend mode=%s", self.mode)
            return False
        finally:
            self._collection = None

        return True

    def close(self) -> None:
        if self._collection is not None:
            self._collection.close()
            self._collection = None

    def get_collection_info(self) -> Optional[Dict[str, Any]]:
        if not self.collection_exists():
            return None
        return self.get_collection().get_meta_data()

    def _sanitize_scalar_index_fields(
        self,
        scalar_index_fields: list[str],
        fields_meta: list[dict[str, Any]],
    ) -> list[str]:
        return scalar_index_fields

    def _build_default_index_meta(
        self,
        *,
        index_name: str,
        distance: str,
        use_sparse: bool,
        sparse_weight: float,
        scalar_index_fields: list[str],
    ) -> Dict[str, Any]:
        index_type = "flat_hybrid" if use_sparse else "flat"
        index_meta: Dict[str, Any] = {
            "IndexName": index_name,
            "VectorIndex": {
                "IndexType": index_type,
                "Distance": distance,
                "Quant": "int8",
            },
            "ScalarIndex": scalar_index_fields,
        }
        if use_sparse:
            index_meta["VectorIndex"]["EnableSparse"] = True
            index_meta["VectorIndex"]["SearchWithSparseLogitAlpha"] = sparse_weight
        return index_meta

    def _normalize_record_for_read(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return record

    def _compile_filter(self, expr: FilterExpr | Dict[str, Any] | None) -> Dict[str, Any]:
        if expr is None:
            return {}
        if isinstance(expr, dict):
            return expr
        if isinstance(expr, RawDSL):
            return expr.payload
        if isinstance(expr, And):
            conds = [self._compile_filter(c) for c in expr.conds if c is not None]
            conds = [c for c in conds if c]
            if not conds:
                return {}
            if len(conds) == 1:
                return conds[0]
            return {"op": "and", "conds": conds}
        if isinstance(expr, Or):
            conds = [self._compile_filter(c) for c in expr.conds if c is not None]
            conds = [c for c in conds if c]
            if not conds:
                return {}
            if len(conds) == 1:
                return conds[0]
            return {"op": "or", "conds": conds}
        if isinstance(expr, Eq):
            return {"op": "must", "field": expr.field, "conds": [expr.value]}
        if isinstance(expr, In):
            return {"op": "must", "field": expr.field, "conds": list(expr.values)}
        if isinstance(expr, Range):
            payload: Dict[str, Any] = {"op": "range", "field": expr.field}
            if expr.gte is not None:
                payload["gte"] = expr.gte
            if expr.gt is not None:
                payload["gt"] = expr.gt
            if expr.lte is not None:
                payload["lte"] = expr.lte
            if expr.lt is not None:
                payload["lt"] = expr.lt
            return payload
        if isinstance(expr, Contains):
            return {
                "op": "contains",
                "field": expr.field,
                "substring": expr.substring,
            }
        if isinstance(expr, TimeRange):
            payload: Dict[str, Any] = {"op": "range", "field": expr.field}
            if expr.start is not None:
                payload["gte"] = expr.start
            if expr.end is not None:
                payload["lt"] = expr.end
            return payload
        raise TypeError(f"Unsupported filter expr type: {type(expr)!r}")

    # Backward-compatible aliases: keep old non-underscore names callable.
    def sanitize_scalar_index_fields(
        self,
        scalar_index_fields: list[str],
        fields_meta: list[dict[str, Any]],
    ) -> list[str]:
        return self._sanitize_scalar_index_fields(
            scalar_index_fields=scalar_index_fields,
            fields_meta=fields_meta,
        )

    def build_default_index_meta(
        self,
        *,
        index_name: str,
        distance: str,
        use_sparse: bool,
        sparse_weight: float,
        scalar_index_fields: list[str],
    ) -> Dict[str, Any]:
        return self._build_default_index_meta(
            index_name=index_name,
            distance=distance,
            use_sparse=use_sparse,
            sparse_weight=sparse_weight,
            scalar_index_fields=scalar_index_fields,
        )

    def normalize_record_for_read(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return self._normalize_record_for_read(record)

    def compile_filter(self, expr: FilterExpr | Dict[str, Any] | None) -> Dict[str, Any]:
        return self._compile_filter(expr)

    def upsert(self, data: Dict[str, Any] | list[Dict[str, Any]]) -> list[str]:
        coll = self.get_collection()
        records = [data] if isinstance(data, dict) else data
        normalized: list[Dict[str, Any]] = []
        ids: list[str] = []
        for item in records:
            record = dict(item)
            record_id = record.get("id") or str(uuid.uuid4())
            record["id"] = record_id
            ids.append(record_id)
            normalized.append(record)
        coll.upsert_data(normalized)
        return ids

    def get(self, ids: list[str]) -> list[Dict[str, Any]]:
        coll = self.get_collection()
        result = coll.fetch_data(ids)

        records: list[Dict[str, Any]] = []
        if isinstance(result, FetchDataInCollectionResult):
            for item in result.items:
                record = dict(item.fields) if item.fields else {}
                record["id"] = item.id
                records.append(self._normalize_record_for_read(record))
            return records

        if isinstance(result, dict) and "fetch" in result:
            for item in result.get("fetch", []):
                record = dict(item.get("fields", {})) if item.get("fields") else {}
                record_id = item.get("id")
                if record_id:
                    record["id"] = record_id
                    records.append(self._normalize_record_for_read(record))
        return records

    def query(
        self,
        *,
        query_vector: Optional[list[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[list[str]] = None,
        with_vector: bool = False,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> list[Dict[str, Any]]:
        coll = self.get_collection()
        vectordb_filter = self._compile_filter(filter)

        if query_vector or sparse_query_vector:
            result = coll.search_by_vector(
                index_name="default",
                dense_vector=query_vector,
                sparse_vector=sparse_query_vector,
                limit=limit,
                offset=offset,
                filters=vectordb_filter,
                output_fields=output_fields,
            )
        elif order_by:
            result = coll.search_by_scalar(
                index_name="default",
                field=order_by,
                order="desc" if order_desc else "asc",
                limit=limit,
                offset=offset,
                filters=vectordb_filter,
                output_fields=output_fields,
            )
        else:
            result = coll.search_by_random(
                index_name="default",
                limit=limit,
                offset=offset,
                filters=vectordb_filter,
                output_fields=output_fields,
            )

        records: list[Dict[str, Any]] = []
        for item in result.data:
            record = dict(item.fields) if item.fields else {}
            record["id"] = item.id
            record["_score"] = item.score if item.score is not None else 0.0
            record = self._normalize_record_for_read(record)
            if not with_vector:
                record.pop("vector", None)
                record.pop("sparse_vector", None)
            records.append(record)
        return records

    def delete(
        self,
        *,
        ids: Optional[list[str]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 100000,
    ) -> int:
        coll = self.get_collection()
        delete_ids = list(ids or [])
        if not delete_ids and filter is not None:
            matched = self.query(filter=filter, limit=limit, with_vector=True)
            delete_ids = [record["id"] for record in matched if record.get("id")]

        if not delete_ids:
            return 0

        coll.delete_data(delete_ids)
        return len(delete_ids)

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return int(stripped)
        return None

    def count(self, filter: Optional[Dict[str, Any] | FilterExpr] = None) -> int:
        coll = self.get_collection()
        result = coll.aggregate_data(
            index_name="default",
            op="count",
            filters=self._compile_filter(filter),
        )
        if "_total" in result.agg:
            parsed_total = self._coerce_int(result.agg.get("_total"))
            if parsed_total is not None:
                return parsed_total

        return 0

    def clear(self) -> bool:
        self.get_collection().delete_all_data()
        return True
