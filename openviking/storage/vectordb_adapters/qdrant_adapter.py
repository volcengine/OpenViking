# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Qdrant backend collection adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from openviking.storage.expr import (
    And,
    Contains,
    Eq,
    FilterExpr,
    In,
    Or,
    PathScope,
    Range,
    RawDSL,
    TimeRange,
)
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.qdrant_collection import QdrantCollection

from .base import CollectionAdapter


class QdrantCollectionAdapter(CollectionAdapter):
    """Adapter for Qdrant-backed vector collections."""

    INTERNAL_PATH_FIELDS = ["parent_uri", "scope_roots", "uri_depth"]
    DEFAULT_TEXT_INDEX_FIELDS = ["name", "description", "abstract", "tags"]

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None,
        timeout_seconds: int,
        project_name: str,
        collection_name: str,
        index_name: str,
        distance_metric: str,
        dense_vector_name: str,
        sparse_vector_name: str,
        meta_collection_name: str,
        enable_text_index: bool,
    ) -> None:
        super().__init__(collection_name=collection_name, index_name=index_name)
        self.mode = "qdrant"
        self._url = url
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._project_name = project_name
        self._distance_metric = distance_metric
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._meta_collection_name = meta_collection_name
        self._enable_text_index = enable_text_index

    @classmethod
    def from_config(cls, config: Any):
        cfg = getattr(config, "qdrant", None)
        params = dict(getattr(config, "custom_params", {}) or {})
        url = (
            getattr(cfg, "url", None)
            or getattr(config, "url", None)
            or params.get("url")
        )
        if not url:
            raise ValueError("Qdrant backend requires qdrant.url or url")
        return cls(
            url=str(url).strip().rstrip("/"),
            api_key=getattr(cfg, "api_key", None) or params.get("api_key"),
            timeout_seconds=int(getattr(cfg, "timeout_seconds", None) or params.get("timeout_seconds") or 10),
            project_name=config.project_name or "default",
            collection_name=config.name or "context",
            index_name=config.index_name or "default",
            distance_metric=config.distance_metric or "cosine",
            dense_vector_name=getattr(cfg, "dense_vector_name", "vector"),
            sparse_vector_name=getattr(cfg, "sparse_vector_name", "sparse_vector"),
            meta_collection_name=getattr(cfg, "meta_collection_name", "__openviking_meta"),
            enable_text_index=bool(getattr(cfg, "enable_text_index", True)),
        )

    @property
    def physical_collection_name(self) -> str:
        return f"{self._project_name}__{self._collection_name}"

    def _new_qdrant_collection(self) -> QdrantCollection:
        return QdrantCollection(
            url=self._url,
            api_key=self._api_key,
            timeout_seconds=self._timeout_seconds,
            project_name=self._project_name,
            logical_collection_name=self._collection_name,
            physical_collection_name=self.physical_collection_name,
            dense_vector_name=self._dense_vector_name,
            sparse_vector_name=self._sparse_vector_name,
            meta_collection_name=self._meta_collection_name,
            distance_metric=self._distance_metric,
            enable_text_index=self._enable_text_index,
        )

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        candidate = self._new_qdrant_collection()
        try:
            if not candidate.collection_exists():
                return
            if not candidate.has_openviking_metadata():
                raise RuntimeError(
                    "Qdrant collection exists but OpenViking metadata is missing: "
                    f"{self.physical_collection_name}. "
                    "Use a different project/name, restore metadata, or drop the stale Qdrant collection."
                )
            self._collection = Collection(candidate)
            candidate = None
        finally:
            if candidate is not None:
                candidate.close()

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        raw_collection = self._new_qdrant_collection()
        raw_collection.create_remote_collection(meta)
        return Collection(raw_collection)

    def _sanitize_scalar_index_fields(
        self,
        scalar_index_fields: list[str],
        fields_meta: list[dict[str, Any]],
    ) -> list[str]:
        text_fields = self.DEFAULT_TEXT_INDEX_FIELDS if self._enable_text_index else []
        merged = list(dict.fromkeys(list(scalar_index_fields) + self.INTERNAL_PATH_FIELDS + text_fields))
        return merged

    def _build_default_index_meta(
        self,
        *,
        index_name: str,
        distance: str,
        use_sparse: bool,
        sparse_weight: float,
        scalar_index_fields: list[str],
    ) -> Dict[str, Any]:
        index_type = "hnsw_hybrid" if use_sparse else "hnsw"
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

    @staticmethod
    def _normalize_path(path: str) -> str:
        stripped = (path or "").strip()
        if not stripped:
            return "/"
        if not stripped.startswith("/"):
            stripped = f"/{stripped}"
        if len(stripped) > 1:
            stripped = stripped.rstrip("/")
        return stripped or "/"

    @classmethod
    def _compute_parent_uri(cls, uri: str) -> str:
        normalized = cls._normalize_path(uri)
        if normalized == "/":
            return "/"
        parts = normalized.strip("/").split("/")
        if len(parts) <= 1:
            return "/"
        return "/" + "/".join(parts[:-1])

    @classmethod
    def _compute_scope_roots(cls, uri: str) -> List[str]:
        normalized = cls._normalize_path(uri)
        if normalized == "/":
            return ["/"]
        parts = normalized.strip("/").split("/")
        roots = ["/"]
        current_parts: List[str] = []
        for part in parts[:-1]:
            current_parts.append(part)
            roots.append("/" + "/".join(current_parts))
        return roots

    def _normalize_record_for_write(self, record: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(super()._normalize_record_for_write(record))
        raw_uri = normalized.get("uri")
        if isinstance(raw_uri, str):
            normalized_uri = self._normalize_path(raw_uri)
            normalized["uri"] = normalized_uri
            normalized["parent_uri"] = self._compute_parent_uri(normalized_uri)
            normalized["scope_roots"] = self._compute_scope_roots(normalized_uri)
            normalized["uri_depth"] = len([part for part in normalized_uri.strip("/").split("/") if part])
        return normalized

    def _normalize_record_for_read(self, record: Dict[str, Any]) -> Dict[str, Any]:
        normalized = super()._normalize_record_for_read(record)
        for field_name in self.INTERNAL_PATH_FIELDS:
            normalized.pop(field_name, None)
        return normalized

    def _coerce_datetime_value(self, value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.isoformat()
        return value

    def _must_clause(self, *conds: Dict[str, Any]) -> Dict[str, Any]:
        items = [cond for cond in conds if cond]
        if not items:
            return {}
        if len(items) == 1 and set(items[0].keys()) in ({"must"}, {"should"}, {"must_not"}):
            return items[0]
        flattened: List[Dict[str, Any]] = []
        for item in items:
            if set(item.keys()) == {"must"}:
                flattened.extend(item.get("must", []))
            else:
                flattened.append(item)
        return {"must": flattened}

    def _should_clause(self, *conds: Dict[str, Any]) -> Dict[str, Any]:
        items = [cond for cond in conds if cond]
        if not items:
            return {}
        flattened: List[Dict[str, Any]] = []
        for item in items:
            if set(item.keys()) == {"should"}:
                flattened.extend(item.get("should", []))
            else:
                flattened.append(item)
        return {"should": flattened}

    @staticmethod
    def _match_condition(field: str, value: Any) -> Dict[str, Any]:
        return {"key": field, "match": {"value": value}}

    @staticmethod
    def _match_any_condition(field: str, values: List[Any]) -> Dict[str, Any]:
        return {"key": field, "match": {"any": values}}

    @staticmethod
    def _range_condition(field: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"key": field, "range": payload}

    @staticmethod
    def _text_condition(field: str, text: str) -> Dict[str, Any]:
        return {"key": field, "match": {"text": text}}

    def _compile_legacy_dict_filter(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        op = str(payload.get("op") or "").lower()
        if not op:
            return self._normalize_filter_payload_for_write(payload)

        if op == "and":
            return self._must_clause(
                *(self._compile_legacy_dict_filter(cond) for cond in payload.get("conds", []) if cond)
            )
        if op == "or":
            return self._should_clause(
                *(self._compile_legacy_dict_filter(cond) for cond in payload.get("conds", []) if cond)
            )
        if op == "must":
            field = payload.get("field")
            values = payload.get("conds", []) or []
            if field in self._URI_FIELD_NAMES:
                values = [self._normalize_path(self._encode_uri_field_value(value)) for value in values]
            if len(values) <= 1:
                value = values[0] if values else None
                return {"must": [self._match_condition(field, value)]} if value is not None else {}
            return {"must": [self._match_any_condition(field, list(values))]}
        if op == "must_not":
            field = payload.get("field")
            values = payload.get("conds", []) or []
            if field in self._URI_FIELD_NAMES:
                values = [self._normalize_path(self._encode_uri_field_value(value)) for value in values]
            condition = (
                self._match_any_condition(field, list(values))
                if len(values) > 1
                else self._match_condition(field, values[0] if values else None)
            )
            return {"must_not": [condition]} if condition else {}
        if op in {"range", "time_range"}:
            range_payload: Dict[str, Any] = {}
            for key in ("gte", "gt", "lte", "lt"):
                if payload.get(key) is not None:
                    range_payload[key] = self._coerce_datetime_value(payload[key])
            return {"must": [self._range_condition(payload.get("field"), range_payload)]}
        if op == "range_out":
            field = payload.get("field")
            branches: List[Dict[str, Any]] = []
            if payload.get("gte") is not None:
                branches.append({"must": [self._range_condition(field, {"lt": payload["gte"]})]})
            if payload.get("lte") is not None:
                branches.append({"must": [self._range_condition(field, {"gt": payload["lte"]})]})
            return self._should_clause(*branches)
        if op == "contains":
            return {"must": [self._text_condition(payload.get("field"), payload.get("substring", ""))]}
        if op == "prefix":
            field = payload.get("field")
            prefix = payload.get("prefix", "")
            if field in self._URI_FIELD_NAMES:
                return self._compile_filter(PathScope(field, prefix, depth=-1))
            return {"must": [self._text_condition(field, prefix)]}
        return self._normalize_filter_payload_for_write(payload)

    def _compile_filter(self, expr: FilterExpr | Dict[str, Any] | None) -> Dict[str, Any]:
        if expr is None:
            return {}
        if isinstance(expr, dict):
            if "op" in expr:
                return self._compile_legacy_dict_filter(expr)
            return self._normalize_filter_payload_for_write(expr)
        if isinstance(expr, RawDSL):
            payload = expr.payload
            if isinstance(payload, dict) and "op" in payload:
                return self._compile_legacy_dict_filter(payload)
            return self._normalize_filter_payload_for_write(payload)
        if isinstance(expr, And):
            return self._must_clause(*(self._compile_filter(cond) for cond in expr.conds if cond))
        if isinstance(expr, Or):
            return self._should_clause(*(self._compile_filter(cond) for cond in expr.conds if cond))
        if isinstance(expr, Eq):
            value = expr.value
            if expr.field in self._URI_FIELD_NAMES:
                value = self._normalize_path(self._encode_uri_field_value(value))
            return {"must": [self._match_condition(expr.field, value)]}
        if isinstance(expr, In):
            values = list(expr.values)
            if expr.field in self._URI_FIELD_NAMES:
                values = [self._normalize_path(self._encode_uri_field_value(value)) for value in values]
            if len(values) == 1:
                return {"must": [self._match_condition(expr.field, values[0])]}
            return {"must": [self._match_any_condition(expr.field, values)]}
        if isinstance(expr, Range):
            numeric_range_payload: Dict[str, Any] = {}
            for key in ("gte", "gt", "lte", "lt"):
                value = getattr(expr, key)
                if value is not None:
                    numeric_range_payload[key] = value
            return {"must": [self._range_condition(expr.field, numeric_range_payload)]}
        if isinstance(expr, TimeRange):
            time_range_payload: Dict[str, Any] = {}
            if expr.start is not None:
                time_range_payload["gte"] = self._coerce_datetime_value(expr.start)
            if expr.end is not None:
                time_range_payload["lt"] = self._coerce_datetime_value(expr.end)
            return {"must": [self._range_condition(expr.field, time_range_payload)]}
        if isinstance(expr, Contains):
            return {"must": [self._text_condition(expr.field, expr.substring)]}
        if isinstance(expr, PathScope):
            encoded_path = (
                self._normalize_path(self._encode_uri_field_value(expr.path))
                if expr.field in self._URI_FIELD_NAMES
                else expr.path
            )
            if expr.depth == 0:
                return {"must": [self._match_condition(expr.field, encoded_path)]}
            if expr.depth == 1:
                return {"must": [self._match_condition("parent_uri", encoded_path)]}
            if expr.depth == -1:
                return {"must": [self._match_condition("scope_roots", encoded_path)]}
            raise ValueError(f"Qdrant adapter only supports PathScope depth 0/1/-1, got {expr.depth}")
        raise TypeError(f"Unsupported filter expr type: {type(expr)!r}")
