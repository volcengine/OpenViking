# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Milvus-backed vector collection adapter."""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

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
from openviking.storage.vectordb.collection.collection import Collection, ICollection
from openviking.storage.vectordb.collection.result import (
    AggregateResult,
    DataItem,
    FetchDataInCollectionResult,
    SearchItemResult,
    SearchResult,
)
from openviking.storage.vectordb.index.index import IIndex
from openviking.storage.vectordb.store.data import DeltaRecord
from openviking.storage.vectordb_adapters.base import CollectionAdapter
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_DEFAULT_URI = "./milvus.db"
_DEFAULT_TIMEOUT_SECONDS = 30
_DEFAULT_QUERY_LIMIT = 10_000
_MILVUS_MAX_COLLECTION_NAME_LENGTH = 255
_MILVUS_VARCHAR_MAX_LENGTH = 65_535
_ID_MAX_LENGTH = 512
_URI_MAX_LENGTH = 4096
_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLLECTION_NAME_RE = re.compile(r"[^A-Za-z0-9_]+")
_VECTOR_FIELD_TYPES = {"vector", "float_vector"}
_LIST_STRING_FIELD_TYPES = {"list<string>", "array<string>"}
_STRING_FIELD_TYPES = {"string", "path", "text", "date_time"}
_INT_FIELD_TYPES = {"int64", "int32", "integer", "long"}
_FLOAT_FIELD_TYPES = {"float", "double"}
_BOOL_FIELD_TYPES = {"bool", "boolean"}
_META_PROPERTY_KEY = "openviking_meta"
_INDEX_META_PROPERTY_PREFIX = "openviking_index_"
_META_COLLECTION_NAME = "ov_openviking_milvus_meta"
_META_VECTOR_FIELD = "meta_vector"


def _import_pymilvus():
    try:
        import pymilvus  # type: ignore  # noqa: PLC0415

        return pymilvus
    except ImportError as exc:  # pragma: no cover - exercised only without optional driver
        raise ImportError(
            "The Milvus backend requires pymilvus with Milvus Lite support. "
            "Install the `openviking[milvus]` optional extra."
        ) from exc


def _safe_collection_name(*parts: Any, prefix: str = "ov") -> str:
    raw = "_".join(str(part or "") for part in parts)
    normalized = _COLLECTION_NAME_RE.sub("_", raw).strip("_")
    if not normalized:
        normalized = "default"
    if normalized[0].isdigit():
        normalized = f"{prefix}_{normalized}"
    elif prefix and not normalized.startswith(f"{prefix}_"):
        normalized = f"{prefix}_{normalized}"
    if len(normalized) <= _MILVUS_MAX_COLLECTION_NAME_LENGTH:
        return normalized

    import hashlib

    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
    keep = _MILVUS_MAX_COLLECTION_NAME_LENGTH - len(digest) - 1
    return f"{normalized[:keep]}_{digest}"


def _normalize_distance(distance: str) -> str:
    value = (distance or "cosine").strip().lower()
    if value not in {"cosine", "l2", "ip"}:
        raise ValueError(
            f"Milvus backend supports only cosine, l2, and ip distance metrics; got {distance!r}"
        )
    return value


def _milvus_metric(distance: str) -> str:
    return {"cosine": "COSINE", "l2": "L2", "ip": "IP"}[_normalize_distance(distance)]


def _json_default(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)


def _json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _truncate_utf8(value: str, byte_limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= byte_limit:
        return value
    cut = byte_limit
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    return encoded[:cut].decode("utf-8")


def _encode_scope_roots(value: Any) -> str:
    roots = value if isinstance(value, list) else [value]
    normalized = [str(root) for root in roots if root is not None]
    return "\n" + "\n".join(normalized) + "\n" if normalized else "\n"


def _sparse_dot(left: Optional[Dict[str, float]], right: Optional[Dict[str, float]]) -> float:
    if not left or not right:
        return 0.0
    total = 0.0
    for key, raw_value in left.items():
        try:
            total += float(raw_value) * float(right.get(key, 0.0))
        except (TypeError, ValueError):
            continue
    return total


def _coerce_datetime_value(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def _format_number(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("Boolean values are not valid numeric filter operands")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Invalid numeric filter value: {value!r}")
    if number.is_integer():
        return str(int(number))
    return format(number, ".12g")


def _quote_value(value: Any) -> str:
    value = _coerce_datetime_value(value)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _format_number(value)
    text = (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace('"', '\\"')
    )
    return f'"{text}"'


def _format_value_list(values: Iterable[Any]) -> str:
    return "[" + ", ".join(_quote_value(value) for value in values) + "]"


def _score_from_hit(hit: Dict[str, Any], distance_metric: str) -> float:
    raw_score = (
        hit.get("score")
        if hit.get("score") is not None
        else hit.get("distance", hit.get("_distance", 0.0))
    )
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(score):
        return 0.0
    if distance_metric == "l2":
        return 1.0 / (1.0 + max(score, 0.0))
    return score


class MilvusIndex(IIndex):
    """Metadata-only logical index facade for Milvus."""

    def __init__(self, collection: "MilvusCollection", index_name: str, meta: Dict[str, Any]):
        super().__init__(meta=meta)
        self._collection = collection
        self._index_name = index_name
        self._meta = dict(meta)

    def upsert_data(self, delta_list: List[DeltaRecord]):
        raise NotImplementedError("MilvusIndex.upsert_data is managed at collection level")

    def delete_data(self, delta_list: List[DeltaRecord]):
        raise NotImplementedError("MilvusIndex.delete_data is managed at collection level")

    def search(
        self,
        query_vector: Optional[List[float]],
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        sparse_raw_terms: Optional[List[str]] = None,
        sparse_values: Optional[List[float]] = None,
    ):
        raise NotImplementedError("MilvusIndex.search is not exposed via raw index interface")

    def aggregate(self, filters: Optional[Dict[str, Any]] = None):
        raise NotImplementedError("MilvusIndex.aggregate is not exposed via raw index interface")

    def update(
        self, scalar_index: Optional[Dict[str, Any]] = None, description: Optional[str] = None
    ):
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


class MilvusCollection(ICollection):
    """A single OpenViking collection stored in Milvus."""

    INTERNAL_PATH_FIELDS = {
        "parent_uri": "path",
        "scope_roots": "string",
        "uri_depth": "int64",
    }

    def __init__(
        self,
        *,
        client: Any,
        logical_collection_name: str,
        physical_collection_name: str,
        project_name: str,
        dense_vector_name: str,
        sparse_vector_name: str,
        distance_metric: str,
        timeout_seconds: int,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__()
        self._client = client
        self._logical_collection_name = logical_collection_name
        self._physical_collection_name = physical_collection_name
        self._project_name = project_name
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._distance_metric = _normalize_distance(distance_metric)
        self._timeout_seconds = int(timeout_seconds)
        self._meta = dict(meta or {})
        self._field_types = self._build_field_type_map(self._meta)
        self._varchar_lengths = self._build_varchar_length_map()
        self._vector_dim = self._extract_vector_dim(self._meta)

    @property
    def collection_name(self) -> str:
        return self._physical_collection_name

    @staticmethod
    def _extract_vector_dim(meta: Dict[str, Any]) -> int:
        for field in meta.get("Fields", []) or []:
            if str(field.get("FieldType") or "").lower() in _VECTOR_FIELD_TYPES:
                try:
                    return int(field.get("Dim") or 0)
                except (TypeError, ValueError):
                    return 0
        return 0

    @classmethod
    def _build_field_type_map(cls, meta: Dict[str, Any]) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for field in meta.get("Fields", []) or []:
            name = field.get("FieldName")
            field_type = field.get("FieldType")
            if name and field_type:
                mapping[str(name)] = str(field_type).lower()
        mapping.setdefault("id", "string")
        mapping.setdefault("vector", "vector")
        mapping.setdefault("sparse_vector", "json")
        mapping.update(cls.INTERNAL_PATH_FIELDS)
        return mapping

    def _build_varchar_length_map(self) -> Dict[str, int]:
        lengths: Dict[str, int] = {
            "id": _ID_MAX_LENGTH,
            "uri": _URI_MAX_LENGTH,
            "parent_uri": _URI_MAX_LENGTH,
            "scope_roots": _MILVUS_VARCHAR_MAX_LENGTH,
        }
        for field_name, field_type in self._field_types.items():
            if field_type in _STRING_FIELD_TYPES:
                lengths.setdefault(field_name, _MILVUS_VARCHAR_MAX_LENGTH)
        return lengths

    def collection_exists(self) -> bool:
        return bool(
            self._client.has_collection(
                collection_name=self._physical_collection_name,
                timeout=self._timeout_seconds,
            )
        )

    def _collection_properties(self) -> Dict[str, Any]:
        try:
            desc = self._client.describe_collection(
                collection_name=self._physical_collection_name,
                timeout=self._timeout_seconds,
            )
        except Exception:
            return {}
        props = desc.get("properties") if isinstance(desc, dict) else None
        return dict(props or {})

    def _ensure_meta_collection(self) -> None:
        try:
            if self._client.has_collection(
                collection_name=_META_COLLECTION_NAME,
                timeout=self._timeout_seconds,
            ):
                return
            pymilvus = _import_pymilvus()
            DataType = pymilvus.DataType
            schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field(
                field_name="id",
                datatype=DataType.VARCHAR,
                is_primary=True,
                max_length=_MILVUS_MAX_COLLECTION_NAME_LENGTH,
            )
            schema.add_field(
                field_name="meta_json",
                datatype=DataType.VARCHAR,
                max_length=_MILVUS_VARCHAR_MAX_LENGTH,
            )
            schema.add_field(field_name="indexes_json", datatype=DataType.JSON, nullable=True)
            schema.add_field(field_name=_META_VECTOR_FIELD, datatype=DataType.FLOAT_VECTOR, dim=1)
            self._client.create_collection(
                collection_name=_META_COLLECTION_NAME,
                schema=schema,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            logger.warning("Failed to ensure Milvus metadata collection: %s", exc)

    def _load_meta_record(self) -> Dict[str, Any]:
        self._ensure_meta_collection()
        try:
            rows = self._client.get(
                collection_name=_META_COLLECTION_NAME,
                ids=[self._physical_collection_name],
                output_fields=["meta_json", "indexes_json"],
                timeout=self._timeout_seconds,
            )
        except Exception:
            return {}
        return dict(rows[0]) if rows else {}

    def _save_meta_record(self, *, meta: Optional[Dict[str, Any]] = None) -> None:
        self._ensure_meta_collection()
        existing = self._load_meta_record()
        meta_json = _json_dumps(meta if meta is not None else self._meta)
        indexes_json = existing.get("indexes_json") if existing else {}
        try:
            self._client.upsert(
                collection_name=_META_COLLECTION_NAME,
                data=[
                    {
                        "id": self._physical_collection_name,
                        "meta_json": meta_json,
                        "indexes_json": indexes_json if isinstance(indexes_json, dict) else {},
                        _META_VECTOR_FIELD: [0.0],
                    }
                ],
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            logger.warning("Failed to persist Milvus metadata record: %s", exc)

    def load_remote_meta(self) -> Optional[Dict[str, Any]]:
        record = self._load_meta_record()
        raw_meta = record.get("meta_json")
        if isinstance(raw_meta, str):
            try:
                meta = json.loads(raw_meta)
            except (TypeError, ValueError):
                meta = None
            if isinstance(meta, dict):
                self._meta = meta
                self._field_types = self._build_field_type_map(meta)
                self._varchar_lengths = self._build_varchar_length_map()
                self._vector_dim = self._extract_vector_dim(meta)
                return meta

        props = self._collection_properties()
        raw_meta = props.get(_META_PROPERTY_KEY)
        if isinstance(raw_meta, str):
            try:
                meta = json.loads(raw_meta)
            except (TypeError, ValueError):
                meta = None
            if isinstance(meta, dict):
                self._meta = meta
                self._field_types = self._build_field_type_map(meta)
                self._varchar_lengths = self._build_varchar_length_map()
                self._vector_dim = self._extract_vector_dim(meta)
                return meta

        return None

    def create_remote_collection(
        self,
        meta_data: Dict[str, Any],
        *,
        consistency_level: Optional[str] = None,
    ) -> None:
        self._meta = dict(meta_data)
        self._field_types = self._build_field_type_map(self._meta)
        self._varchar_lengths = self._build_varchar_length_map()
        self._vector_dim = self._extract_vector_dim(self._meta)
        if self._vector_dim <= 0:
            raise ValueError("Milvus collection requires a positive dense vector dimension")

        pymilvus = _import_pymilvus()
        schema = self._build_schema(pymilvus)
        create_kwargs: Dict[str, Any] = {}
        if consistency_level:
            create_kwargs["consistency_level"] = consistency_level
        self._client.create_collection(
            collection_name=self._physical_collection_name,
            schema=schema,
            timeout=self._timeout_seconds,
            **create_kwargs,
        )
        self._save_collection_meta()

    def _build_schema(self, pymilvus: Any):
        DataType = pymilvus.DataType
        schema = self._client.create_schema(
            auto_id=False,
            enable_dynamic_field=True,
            description=self._meta.get("Description") or "",
        )
        seen = set()
        for field in self._iter_schema_fields():
            field_name = str(field["FieldName"])
            if field_name in seen:
                continue
            seen.add(field_name)
            field_type = str(field.get("FieldType") or "").lower()
            kwargs: Dict[str, Any] = {}
            if field_name == "id":
                kwargs.update(is_primary=True, max_length=_ID_MAX_LENGTH)
                datatype = DataType.VARCHAR
            elif field_type in _VECTOR_FIELD_TYPES:
                dim = int(field.get("Dim") or self._vector_dim)
                if dim <= 0:
                    raise ValueError("Milvus vector field requires Dim")
                datatype = DataType.FLOAT_VECTOR
                kwargs["dim"] = dim
            elif field_name == self._sparse_vector_name or field_type in {"json", "sparse_vector"}:
                datatype = DataType.JSON
                kwargs["nullable"] = True
            elif field_type in _LIST_STRING_FIELD_TYPES:
                datatype = DataType.ARRAY
                kwargs.update(
                    element_type=DataType.VARCHAR,
                    max_capacity=1024,
                    max_length=1024,
                    nullable=True,
                )
            elif field_type in _INT_FIELD_TYPES:
                datatype = DataType.INT64
                kwargs["nullable"] = True
            elif field_type in _FLOAT_FIELD_TYPES:
                datatype = DataType.DOUBLE
                kwargs["nullable"] = True
            elif field_type in _BOOL_FIELD_TYPES:
                datatype = DataType.BOOL
                kwargs["nullable"] = True
            else:
                datatype = DataType.VARCHAR
                kwargs.update(
                    max_length=self._varchar_lengths.get(field_name, _MILVUS_VARCHAR_MAX_LENGTH),
                    nullable=True,
                )
            schema.add_field(field_name=field_name, datatype=datatype, **kwargs)
        return schema

    def _iter_schema_fields(self) -> List[Dict[str, Any]]:
        fields = [dict(field) for field in self._meta.get("Fields", []) or []]
        names = {field.get("FieldName") for field in fields}
        for field_name, field_type in self.INTERNAL_PATH_FIELDS.items():
            if field_name not in names:
                fields.append({"FieldName": field_name, "FieldType": field_type})
        return fields

    def _save_collection_meta(self) -> None:
        self._save_meta_record(meta=self._meta)
        try:
            self._client.alter_collection_properties(
                collection_name=self._physical_collection_name,
                properties={_META_PROPERTY_KEY: _json_dumps(self._meta)},
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            logger.debug("Milvus collection properties are not available: %s", exc)

    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        if fields:
            self._meta.update(fields)
        if description is not None:
            self._meta["Description"] = description
        self._save_collection_meta()
        return dict(self._meta)

    def get_meta_data(self):
        if not self._meta:
            self.load_remote_meta()
        return dict(self._meta)

    def close(self):
        return None

    def drop(self):
        if self.collection_exists():
            self._client.drop_collection(
                collection_name=self._physical_collection_name,
                timeout=self._timeout_seconds,
            )
        try:
            self._client.delete(
                collection_name=_META_COLLECTION_NAME,
                ids=[self._physical_collection_name],
                timeout=self._timeout_seconds,
            )
        except Exception:
            pass

    def create_index(self, index_name: str, meta_data: Dict[str, Any]) -> IIndex:
        meta = dict(meta_data or {})
        vector_meta = dict(meta.get("VectorIndex") or {})
        metric_type = _milvus_metric(vector_meta.get("Distance") or self._distance_metric)
        existing_indexes = set(self.list_indexes() or [])
        if index_name in existing_indexes or self._dense_vector_name in existing_indexes:
            meta["VectorIndex"] = {
                **vector_meta,
                "IndexType": "AUTOINDEX",
                "Distance": self._distance_metric,
            }
            self._save_index_meta(index_name, meta)
            return MilvusIndex(self, index_name, meta)

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name=self._dense_vector_name,
            index_name=index_name,
            index_type="AUTOINDEX",
            metric_type=metric_type,
        )
        try:
            self._client.create_index(
                collection_name=self._physical_collection_name,
                index_params=index_params,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            if "index already" not in str(exc).lower():
                raise
        try:
            self._client.load_collection(
                collection_name=self._physical_collection_name,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            logger.debug("Milvus collection load skipped or failed: %s", exc)
        meta["VectorIndex"] = {
            **vector_meta,
            "IndexType": "AUTOINDEX",
            "Distance": self._distance_metric,
        }
        self._save_index_meta(index_name, meta)
        return MilvusIndex(self, index_name, meta)

    def _save_index_meta(self, index_name: str, meta: Dict[str, Any]) -> None:
        self._ensure_meta_collection()
        record = self._load_meta_record()
        indexes = record.get("indexes_json") if record else {}
        if not isinstance(indexes, dict):
            indexes = {}
        indexes[index_name] = meta
        try:
            self._client.upsert(
                collection_name=_META_COLLECTION_NAME,
                data=[
                    {
                        "id": self._physical_collection_name,
                        "meta_json": record.get("meta_json") or _json_dumps(self._meta),
                        "indexes_json": indexes,
                        _META_VECTOR_FIELD: [0.0],
                    }
                ],
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            logger.warning("Failed to persist Milvus index metadata: %s", exc)
        try:
            self._client.alter_collection_properties(
                collection_name=self._physical_collection_name,
                properties={f"{_INDEX_META_PROPERTY_PREFIX}{index_name}": _json_dumps(meta)},
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            logger.debug("Milvus index properties are not available: %s", exc)

    def has_index(self, index_name: str) -> bool:
        return self.get_index_meta_data(index_name) is not None or index_name in (
            self.list_indexes() or []
        )

    def get_index(self, index_name: str) -> Optional[IIndex]:
        meta = self.get_index_meta_data(index_name)
        return MilvusIndex(self, index_name, meta) if meta else None

    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ):
        meta = self.get_index_meta_data(index_name) or {"IndexName": index_name}
        if scalar_index is not None:
            meta["ScalarIndex"] = (
                list(scalar_index.keys()) if isinstance(scalar_index, dict) else list(scalar_index)
            )
        if description is not None:
            meta["Description"] = description
        self._save_index_meta(index_name, meta)
        return meta

    def get_index_meta_data(self, index_name: str):
        record = self._load_meta_record()
        indexes = record.get("indexes_json") if record else {}
        if isinstance(indexes, dict) and isinstance(indexes.get(index_name), dict):
            return indexes[index_name]

        props = self._collection_properties()
        raw_meta = props.get(f"{_INDEX_META_PROPERTY_PREFIX}{index_name}")
        if not isinstance(raw_meta, str):
            return None
        try:
            meta = json.loads(raw_meta)
        except (TypeError, ValueError):
            return None
        return meta if isinstance(meta, dict) else None

    def list_indexes(self):
        try:
            return list(
                self._client.list_indexes(
                    collection_name=self._physical_collection_name,
                    timeout=self._timeout_seconds,
                )
                or []
            )
        except Exception:
            return []

    def drop_index(self, index_name: str):
        try:
            self._client.release_collection(
                collection_name=self._physical_collection_name,
                timeout=self._timeout_seconds,
            )
        except Exception:
            pass
        for remote_name in list(self.list_indexes() or []):
            if remote_name == index_name or str(remote_name).startswith(f"{index_name}_"):
                try:
                    self._client.drop_index(
                        collection_name=self._physical_collection_name,
                        index_name=remote_name,
                        timeout=self._timeout_seconds,
                    )
                except Exception as exc:
                    logger.warning("Failed to drop Milvus index %s: %s", remote_name, exc)
        try:
            self._client.drop_collection_properties(
                collection_name=self._physical_collection_name,
                property_keys=[f"{_INDEX_META_PROPERTY_PREFIX}{index_name}"],
                timeout=self._timeout_seconds,
            )
        except Exception:
            pass
        try:
            record = self._load_meta_record()
            indexes = record.get("indexes_json") if record else {}
            if isinstance(indexes, dict) and index_name in indexes:
                indexes.pop(index_name, None)
                self._client.upsert(
                    collection_name=_META_COLLECTION_NAME,
                    data=[
                        {
                            "id": self._physical_collection_name,
                            "meta_json": record.get("meta_json") or _json_dumps(self._meta),
                            "indexes_json": indexes,
                            _META_VECTOR_FIELD: [0.0],
                        }
                    ],
                    timeout=self._timeout_seconds,
                )
        except Exception:
            pass

    def _prepare_record_for_write(self, record: Dict[str, Any]) -> Dict[str, Any]:
        prepared: Dict[str, Any] = {}
        for field_name, value in record.items():
            if value is None:
                continue
            field_type = self._field_types.get(field_name, "")
            if field_name == "id":
                text = str(value)
                if len(text.encode("utf-8")) > _ID_MAX_LENGTH:
                    raise ValueError("Milvus record id exceeds 512 bytes")
                prepared[field_name] = text
            elif field_name == self._dense_vector_name:
                prepared[field_name] = self._coerce_dense_vector(value)
            elif field_name == "scope_roots":
                prepared[field_name] = _encode_scope_roots(value)
            elif field_name == self._sparse_vector_name or field_type == "sparse_vector":
                prepared[field_name] = self._coerce_sparse_vector(value)
            elif field_type in _LIST_STRING_FIELD_TYPES:
                prepared[field_name] = [str(item) for item in (value or []) if item is not None]
            elif field_type in _INT_FIELD_TYPES:
                prepared[field_name] = int(value)
            elif field_type in _FLOAT_FIELD_TYPES:
                number = float(value)
                prepared[field_name] = number if math.isfinite(number) else 0.0
            elif field_type in _BOOL_FIELD_TYPES:
                prepared[field_name] = bool(value)
            elif field_type == "date_time":
                prepared[field_name] = str(_coerce_datetime_value(value))
            elif isinstance(value, str):
                limit = self._varchar_lengths.get(field_name)
                prepared[field_name] = _truncate_utf8(value, limit) if limit else value
            else:
                prepared[field_name] = value
        return prepared

    def _coerce_dense_vector(self, value: Any) -> List[float]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ValueError("Milvus dense vector must be a sequence of floats")
        vector = []
        for item in value:
            number = float(item)
            vector.append(number if math.isfinite(number) else 0.0)
        if self._vector_dim > 0 and len(vector) != self._vector_dim:
            raise ValueError(
                f"Milvus dense vector dimension mismatch: expected {self._vector_dim}, "
                f"got {len(vector)}"
            )
        return vector

    @staticmethod
    def _coerce_sparse_vector(value: Any) -> Dict[str, float]:
        if value in (None, ""):
            return {}
        if isinstance(value, str):
            decoded = _json_loads(value)
            value = decoded if isinstance(decoded, dict) else {}
        if not isinstance(value, dict):
            return {}
        result: Dict[str, float] = {}
        for key, raw_value in value.items():
            try:
                number = float(raw_value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                result[str(key)] = number
        return result

    def _record_from_entity(self, entity: Dict[str, Any]) -> tuple[Any, Dict[str, Any]]:
        record = dict(entity or {})
        record_id = record.pop("id", None)
        if record_id is None:
            record_id = entity.get("pk") or entity.get("primary_key")
        return record_id, self._decode_record(record)

    def _decode_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        decoded = dict(record)
        sparse = decoded.get(self._sparse_vector_name)
        if isinstance(sparse, str):
            parsed = _json_loads(sparse)
            decoded[self._sparse_vector_name] = parsed if isinstance(parsed, dict) else sparse
        return decoded

    def _select_output_fields(
        self,
        output_fields: Optional[List[str]],
        *,
        include_vector: bool = False,
        include_sparse: bool = False,
    ) -> List[str]:
        if output_fields:
            fields = [field for field in output_fields if field != "id"]
        else:
            fields = [field for field in self._field_types if field != "id"]
        if not include_vector:
            fields = [field for field in fields if field != self._dense_vector_name]
        if not include_sparse:
            fields = [field for field in fields if field != self._sparse_vector_name]
        return list(dict.fromkeys(fields))

    def search_by_vector(
        self,
        index_name: str,
        dense_vector: Optional[List[float]] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[str] = None,
        sparse_vector: Optional[Dict[str, float]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        del index_name
        if limit <= 0:
            return SearchResult()
        if dense_vector is None:
            return self._search_by_sparse(sparse_vector, limit, offset, filters, output_fields)

        fetch_limit = max(limit + offset, limit)
        fields = self._select_output_fields(
            output_fields,
            include_vector=False,
            include_sparse=bool(sparse_vector),
        )
        raw_results = self._client.search(
            collection_name=self._physical_collection_name,
            data=[self._coerce_dense_vector(dense_vector)],
            anns_field=self._dense_vector_name,
            filter=filters or "",
            limit=fetch_limit,
            output_fields=fields,
            search_params={"metric_type": _milvus_metric(self._distance_metric)},
            timeout=self._timeout_seconds,
        )
        hits = raw_results[0] if raw_results else []
        items: List[SearchItemResult] = []
        for hit in hits:
            entity = hit.get("entity") if isinstance(hit, dict) else None
            entity = dict(entity or {})
            if "id" not in entity and isinstance(hit, dict):
                entity["id"] = hit.get("id")
            record_id, payload = self._record_from_entity(entity)
            score = _score_from_hit(hit, self._distance_metric) if isinstance(hit, dict) else 0.0
            if sparse_vector:
                sparse_payload = payload.pop(self._sparse_vector_name, None)
                score += _sparse_dot(
                    sparse_vector, sparse_payload if isinstance(sparse_payload, dict) else None
                )
            items.append(SearchItemResult(id=record_id, fields=payload, score=score))
        if sparse_vector:
            items.sort(key=lambda item: item.score or 0.0, reverse=True)
        return SearchResult(data=items[offset : offset + limit])

    def _search_by_sparse(
        self,
        sparse_vector: Optional[Dict[str, float]],
        limit: int,
        offset: int,
        filters: Optional[str],
        output_fields: Optional[List[str]],
    ) -> SearchResult:
        if not sparse_vector:
            return SearchResult()
        fields = self._select_output_fields(output_fields, include_sparse=True)
        rows = self._client.query(
            collection_name=self._physical_collection_name,
            filter=filters or "",
            output_fields=fields,
            limit=max(limit + offset, _DEFAULT_QUERY_LIMIT),
            timeout=self._timeout_seconds,
        )
        items = []
        for row in rows:
            record_id, payload = self._record_from_entity(row)
            sparse_payload = payload.pop(self._sparse_vector_name, None)
            score = _sparse_dot(
                sparse_vector, sparse_payload if isinstance(sparse_payload, dict) else None
            )
            if score > 0:
                items.append(SearchItemResult(id=record_id, fields=payload, score=score))
        items.sort(key=lambda item: item.score or 0.0, reverse=True)
        return SearchResult(data=items[offset : offset + limit])

    def search_by_keywords(
        self,
        index_name: str,
        keywords: Optional[List[str]] = None,
        query: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        del index_name
        query_text = query or " ".join(keywords or [])
        if not query_text.strip():
            return SearchResult()
        compiler = MilvusFilterCompiler(self._field_types)
        text_filter = compiler.compile_legacy_filter(
            {
                "op": "or",
                "conds": [
                    {"op": "contains", "field": field, "substring": query_text}
                    for field in ("name", "description", "abstract", "tags", "content")
                    if field in self._field_types
                ],
            }
        )
        combined = (
            f"({filters}) and ({text_filter})"
            if filters and text_filter
            else filters or text_filter
        )
        return self.search_by_random("", limit, offset, combined, output_fields)

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        rows = self._client.get(
            collection_name=self._physical_collection_name,
            ids=[str(id)],
            output_fields=self._select_output_fields(
                None,
                include_vector=True,
                include_sparse=True,
            ),
            timeout=self._timeout_seconds,
        )
        if not rows:
            return SearchResult()
        dense_vector = rows[0].get(self._dense_vector_name)
        sparse_vector = rows[0].get(self._sparse_vector_name)
        result = self.search_by_vector(
            index_name=index_name,
            dense_vector=dense_vector,
            sparse_vector=sparse_vector if isinstance(sparse_vector, dict) else None,
            limit=limit + offset + 1,
            offset=0,
            filters=filters,
            output_fields=output_fields,
        )
        data = [item for item in result.data if str(item.id) != str(id)]
        return SearchResult(data=data[offset : offset + limit])

    def search_by_multimodal(
        self,
        index_name: str,
        text: Optional[str],
        image: Optional[Any],
        video: Optional[Any],
        limit: int = 10,
        offset: int = 0,
        filters: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError("MilvusCollection.search_by_multimodal is not supported")

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        del index_name
        rows = self._client.query(
            collection_name=self._physical_collection_name,
            filter=filters or "",
            output_fields=self._select_output_fields(output_fields),
            limit=limit,
            offset=offset,
            timeout=self._timeout_seconds,
        )
        items = []
        for row in rows:
            record_id, payload = self._record_from_entity(row)
            items.append(SearchItemResult(id=record_id, fields=payload, score=1.0))
        return SearchResult(data=items)

    def search_by_scalar(
        self,
        index_name: str,
        field: str,
        order: Optional[str] = "desc",
        limit: int = 10,
        offset: int = 0,
        filters: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        del index_name
        fields = self._select_output_fields(output_fields)
        if field not in fields:
            fields.append(field)
        rows = self._client.query(
            collection_name=self._physical_collection_name,
            filter=filters or "",
            output_fields=fields,
            limit=max(limit + offset, _DEFAULT_QUERY_LIMIT),
            timeout=self._timeout_seconds,
        )
        reverse = (order or "desc").lower() == "desc"
        rows.sort(key=lambda row: (row.get(field) is None, row.get(field)), reverse=reverse)
        items = []
        for row in rows[offset : offset + limit]:
            record_id, payload = self._record_from_entity(row)
            score = (
                payload.pop(field, None)
                if output_fields and field not in output_fields
                else payload.get(field)
            )
            items.append(
                SearchItemResult(
                    id=record_id,
                    fields=payload,
                    score=score if isinstance(score, (int, float)) else None,
                )
            )
        return SearchResult(data=items)

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl=0):
        del ttl
        if not data_list:
            return []
        records = [self._prepare_record_for_write(record) for record in data_list]
        self._client.upsert(
            collection_name=self._physical_collection_name,
            data=records,
            timeout=self._timeout_seconds,
        )
        return [record.get("id") for record in records if record.get("id") is not None]

    def update_data(self, data_list: List[Dict[str, Any]]):
        updated_records: List[Dict[str, Any]] = []
        updated_ids: List[Any] = []
        for raw_data in data_list:
            if "id" not in raw_data or raw_data.get("id") in (None, ""):
                raise ValueError("Milvus update requires id")
            record_id = str(raw_data["id"])
            existing = self.fetch_data([record_id]).items
            if not existing:
                raise ValueError(f"Milvus entity does not exist for update: {record_id}")
            merged = dict(existing[0].fields or {})
            merged["id"] = existing[0].id
            merged.update(raw_data)
            updated_records.append(self._prepare_record_for_write(merged))
            updated_ids.append(record_id)
        if updated_records:
            self._client.upsert(
                collection_name=self._physical_collection_name,
                data=updated_records,
                timeout=self._timeout_seconds,
            )
        return updated_ids

    def fetch_data(self, primary_keys: List[Any]):
        if not primary_keys:
            return FetchDataInCollectionResult()
        rows = self._client.get(
            collection_name=self._physical_collection_name,
            ids=[str(pk) for pk in primary_keys],
            output_fields=self._select_output_fields(
                None,
                include_vector=True,
                include_sparse=True,
            ),
            timeout=self._timeout_seconds,
        )
        items = []
        found_ids = set()
        for row in rows:
            record_id, payload = self._record_from_entity(row)
            if record_id is not None:
                found_ids.add(str(record_id))
            items.append(DataItem(id=record_id, fields=payload))
        return FetchDataInCollectionResult(
            items=items,
            ids_not_exist=[pk for pk in primary_keys if str(pk) not in found_ids],
        )

    def delete_data(self, primary_keys: List[Any]):
        if not primary_keys:
            return None
        self._client.delete(
            collection_name=self._physical_collection_name,
            ids=[str(pk) for pk in primary_keys],
            timeout=self._timeout_seconds,
        )
        return None

    def delete_all_data(self):
        self._client.delete(
            collection_name=self._physical_collection_name,
            filter='id != ""',
            timeout=self._timeout_seconds,
        )

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: Optional[str] = None,
        filters: Optional[str] = None,
        cond: Optional[Dict[str, Any]] = None,
    ) -> AggregateResult:
        del index_name
        if op != "count":
            return AggregateResult(agg={}, op=op, field=field)
        if not field:
            try:
                rows = self._client.query(
                    collection_name=self._physical_collection_name,
                    filter=filters or "",
                    output_fields=["count(*)"],
                    timeout=self._timeout_seconds,
                )
                total = int((rows[0] if rows else {}).get("count(*)", 0))
            except Exception:
                rows = self._client.query(
                    collection_name=self._physical_collection_name,
                    filter=filters or "",
                    output_fields=["id"],
                    limit=_DEFAULT_QUERY_LIMIT,
                    timeout=self._timeout_seconds,
                )
                total = len(rows)
            return AggregateResult(agg={"_total": total}, op=op, field=None)

        rows = self._client.query(
            collection_name=self._physical_collection_name,
            filter=filters or "",
            output_fields=[field],
            limit=_DEFAULT_QUERY_LIMIT,
            timeout=self._timeout_seconds,
        )
        grouped: Dict[Any, int] = {}
        for row in rows:
            value = row.get(field)
            if value is not None:
                grouped[value] = grouped.get(value, 0) + 1
        if cond:
            grouped = {
                key: value
                for key, value in grouped.items()
                if (cond.get("gt") is None or value > cond["gt"])
                and (cond.get("gte") is None or value >= cond["gte"])
                and (cond.get("lt") is None or value < cond["lt"])
                and (cond.get("lte") is None or value <= cond["lte"])
            }
        return AggregateResult(agg=grouped, op=op, field=field)


class MilvusFilterCompiler:
    """Compile OpenViking filters to safe Milvus boolean expressions."""

    def __init__(self, field_types: Optional[Dict[str, str]] = None) -> None:
        self._field_types = field_types or {}

    def compile(self, expr: FilterExpr | Dict[str, Any] | str | None) -> str:
        if expr is None:
            return ""
        if isinstance(expr, str):
            return expr.strip()
        if isinstance(expr, dict):
            if "op" in expr:
                return self.compile_legacy_filter(expr)
            return self._compile_mapping(expr)
        if isinstance(expr, RawDSL):
            payload = expr.payload
            if isinstance(payload, dict) and "expr" in payload:
                return str(payload["expr"]).strip()
            return self.compile(payload)
        if isinstance(expr, And):
            return self._join("and", [self.compile(cond) for cond in expr.conds if cond])
        if isinstance(expr, Or):
            return self._join("or", [self.compile(cond) for cond in expr.conds if cond])
        if isinstance(expr, Eq):
            return self._eq(expr.field, expr.value)
        if isinstance(expr, In):
            return self._in(expr.field, list(expr.values))
        if isinstance(expr, Range):
            return self._range(
                expr.field,
                gte=expr.gte,
                gt=expr.gt,
                lte=expr.lte,
                lt=expr.lt,
            )
        if isinstance(expr, TimeRange):
            return self._range(
                expr.field,
                gte=_coerce_datetime_value(expr.start),
                lt=_coerce_datetime_value(expr.end),
            )
        if isinstance(expr, Contains):
            return self._contains(expr.field, expr.substring)
        if isinstance(expr, PathScope):
            path = MilvusCollectionAdapter._normalize_path(
                CollectionAdapter._encode_uri_field_value(expr.path)
                if expr.field in CollectionAdapter._URI_FIELD_NAMES
                else expr.path
            )
            if expr.depth == 0:
                return self._eq(expr.field, path)
            if expr.depth == 1:
                return self._eq("parent_uri", path)
            if expr.depth == -1:
                return self._contains("scope_roots", f"\n{path}\n")
            raise ValueError(
                f"Milvus adapter only supports PathScope depth 0/1/-1, got {expr.depth}"
            )
        raise TypeError(f"Unsupported filter expr type: {type(expr)!r}")

    def compile_legacy_filter(self, payload: Dict[str, Any]) -> str:
        op = str(payload.get("op") or "").lower()
        if not op:
            return self._compile_mapping(payload)
        if op in {"and", "or"}:
            return self._join(
                op,
                [self.compile_legacy_filter(cond) for cond in payload.get("conds", []) if cond],
            )
        if op == "must":
            field = payload.get("field")
            values = payload.get("conds", []) or []
            if not values:
                return ""
            if field in CollectionAdapter._URI_FIELD_NAMES:
                values = [
                    MilvusCollectionAdapter._normalize_path(
                        CollectionAdapter._encode_uri_field_value(value)
                    )
                    for value in values
                ]
            return (
                self._in(str(field), list(values))
                if len(values) > 1
                else self._eq(field, values[0])
            )
        if op == "must_not":
            field = payload.get("field")
            values = payload.get("conds", []) or []
            if not values:
                return ""
            expr = (
                self._in(str(field), list(values))
                if len(values) > 1
                else self._eq(field, values[0])
            )
            return f"not ({expr})" if expr else ""
        if op in {"range", "time_range"}:
            return self._range(
                str(payload.get("field")),
                gte=payload.get("gte"),
                gt=payload.get("gt"),
                lte=payload.get("lte"),
                lt=payload.get("lt"),
            )
        if op == "range_out":
            field = str(payload.get("field"))
            branches = []
            if payload.get("gte") is not None:
                branches.append(self._range(field, lt=payload["gte"]))
            if payload.get("lte") is not None:
                branches.append(self._range(field, gt=payload["lte"]))
            return self._join("or", branches)
        if op == "contains":
            return self._contains(str(payload.get("field")), str(payload.get("substring", "")))
        if op == "prefix":
            field = str(payload.get("field"))
            prefix = str(payload.get("prefix", ""))
            if field in CollectionAdapter._URI_FIELD_NAMES:
                return self.compile(PathScope(field, prefix, depth=-1))
            return self._like(field, f"{prefix}%")
        return self._compile_mapping(payload)

    def _compile_mapping(self, payload: Dict[str, Any]) -> str:
        return self._join("and", [self._eq(str(key), value) for key, value in payload.items()])

    @staticmethod
    def _join(op: str, exprs: Iterable[str]) -> str:
        items = [expr for expr in exprs if expr]
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        return f" {op} ".join(f"({item})" for item in items)

    def _validate_field(self, field: Any) -> str:
        if not isinstance(field, str) or not _FIELD_NAME_RE.match(field):
            raise ValueError(f"Invalid Milvus filter field: {field!r}")
        return field

    def _eq(self, field: Any, value: Any) -> str:
        field_name = self._validate_field(field)
        field_type = self._field_types.get(field_name, "")
        if value is None:
            return f"{field_name} is null"
        if field_type in _LIST_STRING_FIELD_TYPES:
            return f"ARRAY_CONTAINS({field_name}, {_quote_value(value)})"
        return f"{field_name} == {_quote_value(value)}"

    def _in(self, field: str, values: List[Any]) -> str:
        field_name = self._validate_field(field)
        if not values:
            return ""
        field_type = self._field_types.get(field_name, "")
        if field_type in _LIST_STRING_FIELD_TYPES:
            return self._join(
                "or",
                [f"ARRAY_CONTAINS({field_name}, {_quote_value(value)})" for value in values],
            )
        non_null = [value for value in values if value is not None]
        expr = f"{field_name} in {_format_value_list(non_null)}" if non_null else ""
        if any(value is None for value in values):
            null_expr = f"{field_name} is null"
            return self._join("or", [expr, null_expr])
        return expr

    def _range(self, field: str, **bounds: Any) -> str:
        field_name = self._validate_field(field)
        parts = []
        operators = {"gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}
        for key, operator in operators.items():
            value = bounds.get(key)
            if value is not None:
                parts.append(f"{field_name} {operator} {_quote_value(value)}")
        return " and ".join(parts)

    def _contains(self, field: str, substring: str) -> str:
        field_name = self._validate_field(field)
        field_type = self._field_types.get(field_name, "")
        if field_type in _LIST_STRING_FIELD_TYPES:
            return f"ARRAY_CONTAINS({field_name}, {_quote_value(substring)})"
        return self._like(field_name, f"%{substring}%")

    def _like(self, field: str, pattern: str) -> str:
        field_name = self._validate_field(field)
        return f"{field_name} like {_quote_value(pattern)}"


class MilvusCollectionAdapter(CollectionAdapter):
    """CollectionAdapter backed by Milvus or Zilliz Cloud."""

    mode = "milvus"
    INTERNAL_PATH_FIELDS = ["parent_uri", "scope_roots", "uri_depth"]

    def __init__(
        self,
        *,
        uri: str,
        token: Optional[str],
        db_name: Optional[str],
        consistency_level: Optional[str],
        timeout_seconds: int,
        project_name: str,
        collection_name: str,
        index_name: str,
        distance_metric: str,
        dense_vector_name: str,
        sparse_vector_name: str,
    ) -> None:
        super().__init__(collection_name=collection_name, index_name=index_name)
        self._uri = uri
        self._token = token
        self._db_name = db_name
        self._consistency_level = consistency_level
        self._timeout_seconds = int(timeout_seconds)
        self._project_name = project_name
        self._distance_metric = _normalize_distance(distance_metric)
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._client = None

    @classmethod
    def from_config(cls, config: Any):
        cfg = getattr(config, "milvus", None)
        params = dict(getattr(config, "custom_params", {}) or {})
        uri = getattr(cfg, "uri", None) or getattr(config, "url", None) or params.get("uri")
        token = getattr(cfg, "token", None) or params.get("token")
        db_name = getattr(cfg, "db_name", None) or params.get("db_name")
        consistency_level = getattr(cfg, "consistency_level", None) or params.get(
            "consistency_level"
        )
        return cls(
            uri=str(uri or _DEFAULT_URI),
            token=str(token) if token else None,
            db_name=str(db_name) if db_name else None,
            consistency_level=str(consistency_level) if consistency_level else None,
            timeout_seconds=int(
                getattr(cfg, "timeout_seconds", None)
                or params.get("timeout_seconds")
                or _DEFAULT_TIMEOUT_SECONDS
            ),
            project_name=config.project_name or "default",
            collection_name=config.name or "context",
            index_name=config.index_name or "default",
            distance_metric=config.distance_metric or "cosine",
            dense_vector_name=str(
                getattr(cfg, "dense_vector_name", None)
                or params.get("dense_vector_name")
                or "vector"
            ),
            sparse_vector_name=str(
                getattr(cfg, "sparse_vector_name", None)
                or params.get("sparse_vector_name")
                or "sparse_vector"
            ),
        )

    @property
    def physical_collection_name(self) -> str:
        return _safe_collection_name(self._project_name, self._collection_name)

    def _connect(self):
        if self._client is not None:
            return self._client
        pymilvus = _import_pymilvus()
        kwargs: Dict[str, Any] = {
            "uri": self._uri,
            "timeout": self._timeout_seconds,
        }
        if self._token:
            kwargs["token"] = self._token
        if self._db_name:
            kwargs["db_name"] = self._db_name
        self._client = pymilvus.MilvusClient(**kwargs)
        return self._client

    def _new_collection(self, meta: Optional[Dict[str, Any]] = None) -> MilvusCollection:
        return MilvusCollection(
            client=self._connect(),
            logical_collection_name=self._collection_name,
            physical_collection_name=self.physical_collection_name,
            project_name=self._project_name,
            dense_vector_name=self._dense_vector_name,
            sparse_vector_name=self._sparse_vector_name,
            distance_metric=self._distance_metric,
            timeout_seconds=self._timeout_seconds,
            meta=meta,
        )

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        raw_collection = self._new_collection()
        if not raw_collection.collection_exists():
            return
        meta = raw_collection.load_remote_meta()
        if not meta:
            raise RuntimeError(
                "Milvus collection exists but OpenViking metadata is missing: "
                f"{self.physical_collection_name}. Use a different project/name, restore metadata, "
                "or drop the stale Milvus collection."
            )
        self._collection = Collection(raw_collection)

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        raw_collection = self._new_collection(meta)
        raw_collection.create_remote_collection(meta, consistency_level=self._consistency_level)
        return Collection(raw_collection)

    def close(self) -> None:
        super().close()
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()
            self._client = None

    def _sanitize_scalar_index_fields(
        self,
        scalar_index_fields: list[str],
        fields_meta: list[dict[str, Any]],
    ) -> list[str]:
        del fields_meta
        return list(dict.fromkeys(list(scalar_index_fields) + self.INTERNAL_PATH_FIELDS))

    def _build_default_index_meta(
        self,
        *,
        index_name: str,
        distance: str,
        use_sparse: bool,
        sparse_weight: float,
        scalar_index_fields: list[str],
    ) -> Dict[str, Any]:
        if use_sparse:
            logger.warning(
                "Milvus adapter stores sparse vectors but currently searches dense vectors first; "
                "sparse scores are only applied to returned dense candidates."
            )
        return {
            "IndexName": index_name,
            "VectorIndex": {
                "IndexType": "AUTOINDEX",
                "Distance": _normalize_distance(distance),
                "Quant": "int8",
                "EnableSparse": bool(use_sparse),
                "SearchWithSparseLogitAlpha": sparse_weight,
            },
            "ScalarIndex": scalar_index_fields,
        }

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
            normalized["uri_depth"] = len(
                [part for part in normalized_uri.strip("/").split("/") if part]
            )
        return normalized

    def _normalize_record_for_read(self, record: Dict[str, Any]) -> Dict[str, Any]:
        normalized = super()._normalize_record_for_read(record)
        for field_name in self.INTERNAL_PATH_FIELDS:
            normalized.pop(field_name, None)
        return normalized

    def _field_types_for_filter(self) -> Dict[str, str]:
        try:
            collection = self.get_collection()
            meta = collection.get_meta_data() or {}
            field_types = MilvusCollection._build_field_type_map(meta)
        except Exception:
            field_types = {
                "id": "string",
                self._dense_vector_name: "vector",
                self._sparse_vector_name: "sparse_vector",
                "uri": "path",
                "parent_uri": "path",
                "scope_roots": "string",
                "uri_depth": "int64",
                "level": "int64",
                "active_count": "int64",
                "search_tags": "list<string>",
            }
        return field_types

    def _compile_filter(self, expr: FilterExpr | Dict[str, Any] | str | None) -> str:
        return MilvusFilterCompiler(self._field_types_for_filter()).compile(expr)

    def update_data(self, data_list: List[Dict[str, Any]]):
        collection = self.get_collection()
        normalized = [self._normalize_record_for_write(item) for item in data_list]
        result = collection.update_data(normalized)
        return [str(item) for item in (result or []) if item is not None]
