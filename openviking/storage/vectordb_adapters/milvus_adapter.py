# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Milvus-backed vector collection adapter."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from copy import deepcopy
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
_TRUNCATION_PROBE_LIMIT = _DEFAULT_QUERY_LIMIT + 1
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
_DATA_COLLECTION_PREFIX = "ov_data"
_META_COLLECTION_NAME = "ov_internal_metadata_v1"
_LEGACY_META_COLLECTION_NAME = "ov_openviking_milvus_meta"
_META_IDENTITY_KEY = "_openviking_identity"
_PHYSICAL_NAMING_VERSION = 2
_META_VECTOR_FIELD = "meta_vector"
_META_VECTOR_INDEX = "meta_vector_index"
_META_VECTOR_DIM = 2
_META_VECTOR_VALUE = [0.0] * _META_VECTOR_DIM


def _import_pymilvus():
    try:
        import pymilvus  # noqa: PLC0415

        return pymilvus
    except ImportError as exc:  # pragma: no cover - exercised only without optional driver
        raise ImportError(
            "The Milvus backend requires pymilvus with Milvus Lite support. "
            "Install the `openviking[milvus]` optional extra."
        ) from exc


def _safe_collection_name(*parts: Any, prefix: str = "ov") -> str:
    raw_parts = [str(part or "") for part in parts]
    raw = "_".join(raw_parts)
    normalized = _COLLECTION_NAME_RE.sub("_", raw).strip("_")
    if not normalized:
        normalized = "default"
    if normalized[0].isdigit():
        normalized = f"{prefix}_{normalized}"
    elif prefix and not normalized.startswith(f"{prefix}_"):
        normalized = f"{prefix}_{normalized}"
    digest = hashlib.sha256("\0".join(raw_parts).encode("utf-8")).hexdigest()[:12]
    keep = _MILVUS_MAX_COLLECTION_NAME_LENGTH - len(digest) - 1
    return f"{normalized[:keep]}_{digest}"


def _legacy_collection_name(*parts: Any) -> str:
    """Return the pre-hash physical name for restart compatibility."""
    raw = "_".join(str(part or "") for part in parts)
    normalized = _COLLECTION_NAME_RE.sub("_", raw).strip("_") or "default"
    if normalized[0].isdigit():
        normalized = f"ov_{normalized}"
    elif not normalized.startswith("ov_"):
        normalized = f"ov_{normalized}"
    if len(normalized) <= _MILVUS_MAX_COLLECTION_NAME_LENGTH:
        return normalized
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
    keep = _MILVUS_MAX_COLLECTION_NAME_LENGTH - len(digest) - 1
    return f"{normalized[:keep]}_{digest}"


def _validate_business_namespace(project_name: str, collection_name: str) -> None:
    reserved = {_META_COLLECTION_NAME, _LEGACY_META_COLLECTION_NAME}
    candidates = {
        str(project_name),
        str(collection_name),
        _legacy_collection_name(project_name),
        _legacy_collection_name(collection_name),
        _legacy_collection_name(project_name, collection_name),
    }
    if candidates & reserved:
        raise ValueError(
            "Milvus project/collection name resolves to a reserved OpenViking metadata "
            "namespace; choose a different business name"
        )


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
    if raw_score is None:
        return 0.0
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
        allow_legacy_sidecar: bool = False,
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
        self._allow_legacy_sidecar = bool(allow_legacy_sidecar)
        _validate_business_namespace(project_name, logical_collection_name)
        self._meta: Dict[str, Any] = {}
        self._field_types: Dict[str, str] = {}
        self._field_defaults: Dict[str, Any] = {}
        self._varchar_lengths: Dict[str, int] = {}
        self._vector_dim = 0
        self._set_meta(dict(meta or {}))

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

    @staticmethod
    def _build_field_default_map(meta: Dict[str, Any]) -> Dict[str, Any]:
        return {
            str(field["FieldName"]): deepcopy(field["DefaultValue"])
            for field in meta.get("Fields", []) or []
            if field.get("FieldName") and "DefaultValue" in field
        }

    def _set_meta(self, meta: Dict[str, Any]) -> None:
        self._meta = dict(meta)
        self._field_types = self._build_field_type_map(self._meta)
        self._field_defaults = self._build_field_default_map(self._meta)
        self._varchar_lengths = self._build_varchar_length_map()
        self._vector_dim = self._extract_vector_dim(self._meta)

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
        desc = self._client.describe_collection(
            collection_name=self._physical_collection_name,
            timeout=self._timeout_seconds,
        )
        props = desc.get("properties") if isinstance(desc, dict) else None
        return dict(props or {})

    def _identity(self) -> Dict[str, Any]:
        naming_version = (
            _PHYSICAL_NAMING_VERSION
            if self._physical_collection_name
            == _safe_collection_name(
                self._project_name,
                self._logical_collection_name,
                prefix=_DATA_COLLECTION_PREFIX,
            )
            else 1
        )
        return {
            "logical_project": self._project_name,
            "logical_collection": self._logical_collection_name,
            "naming_version": naming_version,
            "physical_collection": self._physical_collection_name,
        }

    def _encode_owned_meta(self, meta: Dict[str, Any]) -> str:
        persisted = deepcopy(meta)
        persisted[_META_IDENTITY_KEY] = self._identity()
        return _json_dumps(persisted)

    def _decode_owned_meta(self, raw_meta: Any, *, source: str) -> Dict[str, Any]:
        try:
            persisted = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Milvus {source} metadata is invalid; migration/rebuild is required"
            ) from exc
        if not isinstance(persisted, dict):
            raise RuntimeError(
                f"Milvus {source} metadata is invalid; migration/rebuild is required"
            )
        identity = persisted.get(_META_IDENTITY_KEY)
        if not isinstance(identity, dict):
            raise RuntimeError(
                f"Milvus {source} metadata has no verifiable ownership identity; refusing "
                "automatic binding. Migrate/rebuild the collection or add an explicit, "
                "verified binding."
            )
        expected = self._identity()
        if identity != expected:
            raise RuntimeError(
                f"Milvus {source} ownership identity does not match the requested logical "
                "project/collection and physical name; refusing read, update, or delete. "
                "Migrate/rebuild the collection or use an explicit, verified binding."
            )
        schema_meta = deepcopy(persisted)
        schema_meta.pop(_META_IDENTITY_KEY, None)
        return schema_meta

    def _validate_meta_record_identity(
        self, record: Dict[str, Any], *, source: str
    ) -> Dict[str, Any]:
        if record:
            self._decode_owned_meta(record.get("meta_json"), source=source)
        return record

    def _ensure_meta_collection(self) -> None:
        collection_exists = self._client.has_collection(
            collection_name=_META_COLLECTION_NAME,
            timeout=self._timeout_seconds,
        )
        if not collection_exists:
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
            schema.add_field(
                field_name=_META_VECTOR_FIELD,
                datatype=DataType.FLOAT_VECTOR,
                dim=_META_VECTOR_DIM,
            )
            self._client.create_collection(
                collection_name=_META_COLLECTION_NAME,
                schema=schema,
                timeout=self._timeout_seconds,
            )

        indexes = list(
            self._client.list_indexes(
                collection_name=_META_COLLECTION_NAME,
                timeout=self._timeout_seconds,
            )
            or []
        )
        if _META_VECTOR_FIELD not in indexes and _META_VECTOR_INDEX not in indexes:
            index_params = self._client.prepare_index_params()
            index_params.add_index(
                field_name=_META_VECTOR_FIELD,
                index_name=_META_VECTOR_INDEX,
                index_type="AUTOINDEX",
                metric_type="COSINE",
            )
            self._client.create_index(
                collection_name=_META_COLLECTION_NAME,
                index_params=index_params,
                timeout=self._timeout_seconds,
            )
        self._client.load_collection(
            collection_name=_META_COLLECTION_NAME,
            timeout=self._timeout_seconds,
        )

    def _load_meta_record(self) -> Dict[str, Any]:
        self._ensure_meta_collection()
        rows = self._client.get(
            collection_name=_META_COLLECTION_NAME,
            ids=[self._physical_collection_name],
            output_fields=["meta_json", "indexes_json"],
            timeout=self._timeout_seconds,
        )
        if rows:
            return self._validate_meta_record_identity(
                dict(rows[0]), source=f"sidecar {_META_COLLECTION_NAME!r}"
            )
        if not self._allow_legacy_sidecar or self._identity()["naming_version"] != 1:
            return {}
        if not self._client.has_collection(
            collection_name=_LEGACY_META_COLLECTION_NAME,
            timeout=self._timeout_seconds,
        ):
            return {}
        self._client.load_collection(
            collection_name=_LEGACY_META_COLLECTION_NAME,
            timeout=self._timeout_seconds,
        )
        legacy_rows = self._client.get(
            collection_name=_LEGACY_META_COLLECTION_NAME,
            ids=[self._physical_collection_name],
            output_fields=["meta_json", "indexes_json"],
            timeout=self._timeout_seconds,
        )
        if not legacy_rows:
            return {}
        return self._validate_meta_record_identity(
            dict(legacy_rows[0]), source=f"legacy sidecar {_LEGACY_META_COLLECTION_NAME!r}"
        )

    def _save_meta_record(self, *, meta: Optional[Dict[str, Any]] = None) -> None:
        self._ensure_meta_collection()
        existing = self._load_meta_record()
        meta_json = self._encode_owned_meta(meta if meta is not None else self._meta)
        indexes_json = deepcopy(existing.get("indexes_json")) if existing else {}
        self._client.upsert(
            collection_name=_META_COLLECTION_NAME,
            data=[
                {
                    "id": self._physical_collection_name,
                    "meta_json": meta_json,
                    "indexes_json": indexes_json if isinstance(indexes_json, dict) else {},
                    _META_VECTOR_FIELD: _META_VECTOR_VALUE,
                }
            ],
            timeout=self._timeout_seconds,
        )

    def load_remote_meta(self) -> Optional[Dict[str, Any]]:
        record = self._load_meta_record()
        raw_meta = record.get("meta_json")
        if raw_meta is not None:
            meta = self._decode_owned_meta(raw_meta, source="sidecar")
            property_raw = self._collection_properties().get(_META_PROPERTY_KEY)
            if property_raw is not None:
                property_meta = self._decode_owned_meta(property_raw, source="collection property")
                if property_meta != meta:
                    raise RuntimeError(
                        "Milvus collection metadata is inconsistent between sidecar and "
                        "collection property; repair or rebuild before binding"
                    )
            self._set_meta(meta)
            return meta

        props = self._collection_properties()
        raw_meta = props.get(_META_PROPERTY_KEY)
        if raw_meta is not None:
            meta = self._decode_owned_meta(raw_meta, source="collection property")
            self._set_meta(meta)
            return meta

        return None

    def create_remote_collection(
        self,
        meta_data: Dict[str, Any],
        *,
        consistency_level: Optional[str] = None,
    ) -> None:
        self._set_meta(dict(meta_data))
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
        try:
            self._save_collection_meta()
        except Exception as exc:
            try:
                self._client.drop_collection(
                    collection_name=self._physical_collection_name,
                    timeout=self._timeout_seconds,
                )
                self._delete_meta_record(ignore_missing=True)
            except Exception as rollback_exc:
                raise RuntimeError(
                    "Milvus collection metadata persistence failed and rollback left an "
                    f"inconsistent collection {self._physical_collection_name!r}: {rollback_exc}"
                ) from exc
            raise RuntimeError(
                f"Milvus collection metadata persistence failed; rolled back "
                f"{self._physical_collection_name!r}"
            ) from exc

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
            if "DefaultValue" in field and field_type not in _LIST_STRING_FIELD_TYPES:
                kwargs["default_value"] = deepcopy(field["DefaultValue"])
            schema.add_field(field_name=field_name, datatype=datatype, **kwargs)
        return schema

    def _iter_schema_fields(self) -> List[Dict[str, Any]]:
        fields = [dict(field) for field in self._meta.get("Fields", []) or []]
        names = {field.get("FieldName") for field in fields}
        for field_name, field_type in self.INTERNAL_PATH_FIELDS.items():
            if field_name not in names:
                fields.append({"FieldName": field_name, "FieldType": field_type})
        return fields

    @staticmethod
    def _expected_physical_type(field: Dict[str, Any], sparse_vector_name: str) -> str:
        field_name = str(field.get("FieldName") or "")
        field_type = str(field.get("FieldType") or "").lower()
        if field_type in _VECTOR_FIELD_TYPES:
            return "FLOAT_VECTOR"
        if field_name == sparse_vector_name or field_type in {"json", "sparse_vector"}:
            return "JSON"
        if field_type in _LIST_STRING_FIELD_TYPES:
            return "ARRAY"
        if field_type in _INT_FIELD_TYPES:
            return "INT64"
        if field_type in _FLOAT_FIELD_TYPES:
            return "DOUBLE"
        if field_type in _BOOL_FIELD_TYPES:
            return "BOOL"
        return "VARCHAR"

    def ensure_schema_compatible(self, desired_meta: Dict[str, Any]) -> None:
        """Upgrade dynamic metadata or fail before using an incompatible static schema."""
        desc = self._client.describe_collection(
            collection_name=self._physical_collection_name,
            timeout=self._timeout_seconds,
        )
        described_fields = {
            str(field.get("name")): field
            for field in (desc.get("fields", []) if isinstance(desc, dict) else [])
            if field.get("name")
        }
        desired_fields = [dict(field) for field in desired_meta.get("Fields", []) or []]
        desired_names = {str(field.get("FieldName")) for field in desired_fields}
        for field_name, field_type in self.INTERNAL_PATH_FIELDS.items():
            if field_name not in desired_names:
                desired_fields.append({"FieldName": field_name, "FieldType": field_type})

        mismatches: List[str] = []
        missing: List[str] = []
        if bool(desc.get("auto_id")):
            mismatches.append("collection auto_id (True != False)")
        if not bool(desc.get("enable_dynamic_field")):
            mismatches.append("collection enable_dynamic_field (False != True)")

        vector_fields = [
            str(field.get("FieldName") or "")
            for field in desired_fields
            if str(field.get("FieldType") or "").lower() in _VECTOR_FIELD_TYPES
        ]
        if vector_fields != [self._dense_vector_name]:
            mismatches.append(
                f"dense vector field ({vector_fields!r} != {[self._dense_vector_name]!r})"
            )
        desired_primary = [
            str(field.get("FieldName") or "")
            for field in desired_fields
            if field.get("IsPrimaryKey")
        ]
        if desired_primary != ["id"]:
            mismatches.append(f"logical primary key ({desired_primary!r} != ['id'])")
        physical_primary = [
            name for name, field in described_fields.items() if bool(field.get("is_primary"))
        ]
        if physical_primary != ["id"]:
            mismatches.append(f"physical primary key ({physical_primary!r} != ['id'])")

        for field in desired_fields:
            field_name = str(field.get("FieldName") or "")
            if not field_name:
                continue
            physical_field = described_fields.get(field_name)
            if physical_field is None:
                missing.append(field_name)
                continue
            actual_type = self._physical_type_name(physical_field)
            expected_type = self._expected_physical_type(field, self._sparse_vector_name)
            if actual_type != expected_type:
                mismatches.append(f"{field_name} ({actual_type} != {expected_type})")
                continue
            params = physical_field.get("params") or {}
            if expected_type == "FLOAT_VECTOR":
                expected_dim = int(field.get("Dim") or 0)
                actual_dim = int(params.get("dim") or 0)
                if actual_dim != expected_dim:
                    mismatches.append(f"{field_name}.dim ({actual_dim} != {expected_dim})")
            elif expected_type == "ARRAY":
                element_type = self._physical_type_name(
                    {"type": physical_field.get("element_type")}
                )
                if element_type != "VARCHAR":
                    mismatches.append(f"{field_name}.element_type ({element_type} != VARCHAR)")
                actual_capacity = int(params.get("max_capacity") or 0)
                if actual_capacity != 1024:
                    mismatches.append(f"{field_name}.max_capacity ({actual_capacity} != 1024)")
            elif expected_type == "VARCHAR":
                expected_length = self._varchar_lengths.get(field_name, _MILVUS_VARCHAR_MAX_LENGTH)
                actual_length = int(params.get("max_length") or 0)
                if actual_length != expected_length:
                    mismatches.append(
                        f"{field_name}.max_length ({actual_length} != {expected_length})"
                    )
            if field_name == "id" and not bool(physical_field.get("is_primary")):
                mismatches.append("id.is_primary (False != True)")
            if field_name not in {"id", self._dense_vector_name} and not bool(
                physical_field.get("nullable")
            ):
                mismatches.append(f"{field_name}.nullable (False != True)")
        if mismatches:
            raise RuntimeError(
                "Existing Milvus schema is incompatible and requires migration/rebuild: "
                + ", ".join(mismatches)
            )
        if missing and not bool(desc.get("enable_dynamic_field")):
            raise RuntimeError(
                "Existing static Milvus schema is missing fields and requires migration/rebuild: "
                + ", ".join(sorted(missing))
            )

        upgraded = dict(desired_meta)
        existing_fields = [dict(field) for field in self._meta.get("Fields", []) or []]
        desired_field_names = {field.get("FieldName") for field in desired_fields}
        upgraded["Fields"] = desired_fields + [
            field for field in existing_fields if field.get("FieldName") not in desired_field_names
        ]
        self._set_meta(upgraded)
        self._save_collection_meta()

    def _save_collection_meta(self) -> None:
        self._save_meta_record(meta=self._meta)
        self._client.alter_collection_properties(
            collection_name=self._physical_collection_name,
            properties={_META_PROPERTY_KEY: self._encode_owned_meta(self._meta)},
            timeout=self._timeout_seconds,
        )

    def _delete_meta_record(self, *, ignore_missing: bool = False) -> None:
        owned_records = []
        collection_names = [_META_COLLECTION_NAME]
        if self._allow_legacy_sidecar and self._identity()["naming_version"] == 1:
            collection_names.append(_LEGACY_META_COLLECTION_NAME)
        for collection_name in collection_names:
            exists = self._client.has_collection(
                collection_name=collection_name,
                timeout=self._timeout_seconds,
            )
            if not exists:
                if ignore_missing:
                    continue
                if collection_name == _META_COLLECTION_NAME:
                    raise RuntimeError("Milvus metadata collection is missing")
                continue
            rows = self._client.get(
                collection_name=collection_name,
                ids=[self._physical_collection_name],
                output_fields=["meta_json"],
                timeout=self._timeout_seconds,
            )
            if not rows:
                continue
            self._validate_meta_record_identity(
                dict(rows[0]), source=f"sidecar {collection_name!r}"
            )
            owned_records.append(collection_name)
        for collection_name in owned_records:
            self._client.delete(
                collection_name=collection_name,
                ids=[self._physical_collection_name],
                timeout=self._timeout_seconds,
            )

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
            if not self.load_remote_meta():
                raise RuntimeError(
                    "Milvus collection has no verifiable ownership metadata; refusing delete. "
                    "Migrate/rebuild the collection or use an explicit, verified binding."
                )
            self._validate_all_sidecar_ownership()
            self._client.drop_collection(
                collection_name=self._physical_collection_name,
                timeout=self._timeout_seconds,
            )
        self._delete_meta_record(ignore_missing=True)

    def _validate_all_sidecar_ownership(self) -> None:
        collection_names = [_META_COLLECTION_NAME]
        if self._allow_legacy_sidecar and self._identity()["naming_version"] == 1:
            collection_names.append(_LEGACY_META_COLLECTION_NAME)
        for collection_name in collection_names:
            if not self._client.has_collection(
                collection_name=collection_name,
                timeout=self._timeout_seconds,
            ):
                continue
            rows = self._client.get(
                collection_name=collection_name,
                ids=[self._physical_collection_name],
                output_fields=["meta_json"],
                timeout=self._timeout_seconds,
            )
            if rows:
                self._validate_meta_record_identity(
                    dict(rows[0]), source=f"sidecar {collection_name!r}"
                )

    def create_index(self, index_name: str, meta_data: Dict[str, Any]) -> IIndex:
        meta = dict(meta_data or {})
        vector_meta = dict(meta.get("VectorIndex") or {})
        metric_type = _milvus_metric(vector_meta.get("Distance") or self._distance_metric)
        actual_scalar_fields: List[str] = []
        degraded_scalar_fields: List[str] = []
        initial_index_definitions = self._physical_index_definitions()
        initial_physical_indexes = {
            field_name: str(definition["index_name"])
            for field_name, definition in initial_index_definitions.items()
        }
        was_loaded = self._is_collection_loaded()
        load_attempted = False
        try:
            physical_fields = self._physical_field_types()
            physical_indexes = dict(initial_physical_indexes)
            if self._dense_vector_name not in physical_indexes:
                index_params = self._client.prepare_index_params()
                index_params.add_index(
                    field_name=self._dense_vector_name,
                    index_name=index_name,
                    index_type="AUTOINDEX",
                    metric_type=metric_type,
                )
                self._client.create_index(
                    collection_name=self._physical_collection_name,
                    index_params=index_params,
                    timeout=self._timeout_seconds,
                )
                physical_indexes = self._physical_indexes()

            requested_scalar_fields = list(dict.fromkeys(meta.get("ScalarIndex") or []))
            for field_name in requested_scalar_fields:
                field_type = physical_fields.get(field_name)
                if field_type not in {"VARCHAR", "INT64", "BOOL"}:
                    degraded_scalar_fields.append(field_name)
                    continue
                if field_name not in physical_indexes:
                    scalar_params = self._client.prepare_index_params()
                    scalar_params.add_index(
                        field_name=field_name,
                        index_name=f"{index_name}_{field_name}",
                        index_type="INVERTED",
                    )
                    self._client.create_index(
                        collection_name=self._physical_collection_name,
                        index_params=scalar_params,
                        timeout=self._timeout_seconds,
                    )
                    physical_indexes = self._physical_indexes()
                if field_name in physical_indexes:
                    actual_scalar_fields.append(field_name)

            if degraded_scalar_fields:
                logger.warning(
                    "Milvus scalar indexes are unavailable for fields %s; Lite does not support "
                    "ARRAY indexes and dynamic-only fields cannot be indexed",
                    ", ".join(sorted(degraded_scalar_fields)),
                )
            meta["VectorIndex"] = {
                **vector_meta,
                "IndexType": "AUTOINDEX",
                "Distance": self._distance_metric,
            }
            meta["ScalarIndex"] = actual_scalar_fields
            if degraded_scalar_fields:
                meta["ScalarIndexUnavailable"] = degraded_scalar_fields
            else:
                meta.pop("ScalarIndexUnavailable", None)
            load_attempted = True
            self._client.load_collection(
                collection_name=self._physical_collection_name,
                timeout=self._timeout_seconds,
            )
            self._save_index_meta(index_name, meta)
        except Exception as exc:
            rollback_errors: List[str] = []
            current_indexes = self._physical_indexes()
            created_index_names = [
                remote_name
                for field_name, remote_name in current_indexes.items()
                if field_name not in initial_physical_indexes
            ]
            released_for_rollback = False
            if created_index_names or (load_attempted and not was_loaded):
                try:
                    self._client.release_collection(
                        collection_name=self._physical_collection_name,
                        timeout=self._timeout_seconds,
                    )
                    released_for_rollback = True
                except Exception as rollback_exc:
                    rollback_errors.append(f"release: {rollback_exc}")
            for remote_name in reversed(created_index_names):
                try:
                    if remote_name not in (
                        self._client.list_indexes(
                            collection_name=self._physical_collection_name,
                            timeout=self._timeout_seconds,
                        )
                        or []
                    ):
                        continue
                    self._client.drop_index(
                        collection_name=self._physical_collection_name,
                        index_name=remote_name,
                        timeout=self._timeout_seconds,
                    )
                except Exception as rollback_exc:
                    rollback_errors.append(f"drop {remote_name}: {rollback_exc}")
            try:
                remaining_fields = set(self._physical_indexes())
                for field_name, definition in initial_index_definitions.items():
                    if field_name in remaining_fields:
                        continue
                    restore_params = self._client.prepare_index_params()
                    restore_kwargs = {
                        "field_name": field_name,
                        "index_name": definition["index_name"],
                        "index_type": definition["index_type"],
                    }
                    restore_metric: Optional[str] = definition.get("metric_type")
                    if restore_metric and restore_metric != "NONE":
                        restore_kwargs["metric_type"] = restore_metric
                    restore_params.add_index(**restore_kwargs)
                    self._client.create_index(
                        collection_name=self._physical_collection_name,
                        index_params=restore_params,
                        timeout=self._timeout_seconds,
                    )
                    remaining_fields.add(field_name)
            except Exception as rollback_exc:
                rollback_errors.append(f"restore existing indexes: {rollback_exc}")
            if was_loaded and released_for_rollback:
                try:
                    self._client.load_collection(
                        collection_name=self._physical_collection_name,
                        timeout=self._timeout_seconds,
                    )
                except Exception as rollback_exc:
                    rollback_errors.append(f"restore load state: {rollback_exc}")
            if rollback_errors:
                raise RuntimeError(
                    "Milvus index creation failed and rollback left physical indexes or load "
                    f"state inconsistent: {'; '.join(rollback_errors)}"
                ) from exc
            raise RuntimeError(
                "Milvus index creation failed; new physical indexes rolled back"
            ) from exc
        return MilvusIndex(self, index_name, meta)

    def _is_collection_loaded(self) -> bool:
        try:
            load_state = self._client.get_load_state(
                collection_name=self._physical_collection_name,
                timeout=self._timeout_seconds,
            )
        except (AttributeError, NotImplementedError):
            return True
        state = load_state.get("state") if isinstance(load_state, dict) else load_state
        state_name = getattr(state, "name", str(state))
        return str(state_name).lower() == "loaded"

    @staticmethod
    def _physical_type_name(field: Dict[str, Any]) -> str:
        value = field.get("type")
        name = getattr(value, "name", None)
        if name:
            return str(name).upper()
        text = str(value or "").upper()
        return text.rsplit(".", 1)[-1]

    def _physical_field_types(self) -> Dict[str, str]:
        desc = self._client.describe_collection(
            collection_name=self._physical_collection_name,
            timeout=self._timeout_seconds,
        )
        return {
            str(field.get("name")): self._physical_type_name(field)
            for field in (desc.get("fields", []) if isinstance(desc, dict) else [])
            if field.get("name")
        }

    def _physical_indexes(self) -> Dict[str, str]:
        return {
            field_name: str(definition["index_name"])
            for field_name, definition in self._physical_index_definitions().items()
        }

    def _physical_index_definitions(self) -> Dict[str, Dict[str, str]]:
        result: Dict[str, Dict[str, str]] = {}
        remote_names = (
            self._client.list_indexes(
                collection_name=self._physical_collection_name,
                timeout=self._timeout_seconds,
            )
            or []
        )
        for remote_name in remote_names:
            desc = self._client.describe_index(
                collection_name=self._physical_collection_name,
                index_name=remote_name,
                timeout=self._timeout_seconds,
            )
            field_name = desc.get("field_name") if isinstance(desc, dict) else None
            actual_name = desc.get("index_name") if isinstance(desc, dict) else None
            if field_name:
                result[str(field_name)] = {
                    "field_name": str(field_name),
                    "index_name": str(actual_name or remote_name),
                    "index_type": str(desc.get("index_type") or "AUTOINDEX"),
                    "metric_type": str(desc.get("metric_type") or "NONE"),
                }
        return result

    def _save_index_meta(self, index_name: str, meta: Dict[str, Any]) -> None:
        self._ensure_meta_collection()
        record = deepcopy(self._load_meta_record())
        if not record:
            raise RuntimeError(
                "Milvus collection metadata is missing; refusing index update until the "
                "collection is migrated/rebuilt"
            )
        old_indexes = deepcopy(record.get("indexes_json"))
        if not isinstance(old_indexes, dict):
            old_indexes = {}
        property_key = f"{_INDEX_META_PROPERTY_PREFIX}{index_name}"
        old_property = self._collection_properties().get(property_key)
        old_sidecar_meta = old_indexes.get(index_name)
        if old_sidecar_meta is not None and old_property is not None:
            parsed_property = _json_loads(old_property)
            if parsed_property != old_sidecar_meta:
                raise RuntimeError(
                    "Milvus index metadata is inconsistent between sidecar and collection "
                    "properties; repair or rebuild before retrying"
                )

        indexes = deepcopy(old_indexes)
        indexes[index_name] = deepcopy(meta)
        persisted_record = {
            "id": self._physical_collection_name,
            "meta_json": record["meta_json"],
            "indexes_json": deepcopy(old_indexes),
            _META_VECTOR_FIELD: _META_VECTOR_VALUE,
        }
        new_record = deepcopy(persisted_record)
        new_record["indexes_json"] = indexes
        try:
            self._client.alter_collection_properties(
                collection_name=self._physical_collection_name,
                properties={property_key: _json_dumps(meta)},
                timeout=self._timeout_seconds,
            )
            self._client.upsert(
                collection_name=_META_COLLECTION_NAME,
                data=[new_record],
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            rollback_errors: List[str] = []
            try:
                self._client.upsert(
                    collection_name=_META_COLLECTION_NAME,
                    data=[persisted_record],
                    timeout=self._timeout_seconds,
                )
            except Exception as rollback_exc:
                rollback_errors.append(f"sidecar: {rollback_exc}")
            try:
                if old_property is not None:
                    self._client.alter_collection_properties(
                        collection_name=self._physical_collection_name,
                        properties={property_key: old_property},
                        timeout=self._timeout_seconds,
                    )
                else:
                    self._client.drop_collection_properties(
                        collection_name=self._physical_collection_name,
                        property_keys=[property_key],
                        timeout=self._timeout_seconds,
                    )
            except Exception as rollback_exc:
                rollback_errors.append(f"property: {rollback_exc}")
            if rollback_errors:
                raise RuntimeError(
                    "Milvus index metadata persistence failed and rollback left an "
                    f"inconsistent state: {'; '.join(rollback_errors)}"
                ) from exc
            raise RuntimeError(
                "Milvus index metadata persistence failed; sidecar and property rolled back"
            ) from exc

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
        if scalar_index is not None:
            updated = self.create_index(index_name, meta)
            return updated.get_meta_data()
        self._save_index_meta(index_name, meta)
        return meta

    def get_index_meta_data(self, index_name: str):
        record = self._load_meta_record()
        indexes = record.get("indexes_json") if record else {}
        sidecar_meta = indexes.get(index_name) if isinstance(indexes, dict) else None
        props = self._collection_properties()
        raw_meta = props.get(f"{_INDEX_META_PROPERTY_PREFIX}{index_name}")
        property_meta = _json_loads(raw_meta) if raw_meta is not None else None
        if property_meta is not None and not isinstance(property_meta, dict):
            raise RuntimeError("Milvus index collection property contains invalid metadata")
        if sidecar_meta is not None and property_meta is not None and sidecar_meta != property_meta:
            raise RuntimeError(
                "Milvus index metadata is inconsistent between sidecar and collection "
                "properties; repair or rebuild before retrying"
            )
        if isinstance(sidecar_meta, dict):
            return deepcopy(sidecar_meta)
        return deepcopy(property_meta) if isinstance(property_meta, dict) else None

    def list_indexes(self):
        return list(
            self._client.list_indexes(
                collection_name=self._physical_collection_name,
                timeout=self._timeout_seconds,
            )
            or []
        )

    def drop_index(self, index_name: str):
        try:
            self._client.release_collection(
                collection_name=self._physical_collection_name,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            logger.debug("Milvus collection release before index drop failed: %s", exc)

        meta = self.get_index_meta_data(index_name) or {}
        physical_indexes = self._physical_indexes()
        if meta:
            target_fields = [self._dense_vector_name, *(meta.get("ScalarIndex") or [])]
        else:
            target_fields = [
                field_name
                for field_name, remote_name in physical_indexes.items()
                if field_name == index_name or remote_name == index_name
            ]
        for field_name in target_fields:
            remote_name = physical_indexes.get(field_name)
            if remote_name:
                self._client.drop_index(
                    collection_name=self._physical_collection_name,
                    index_name=remote_name,
                    timeout=self._timeout_seconds,
                )
        if meta:
            self._client.drop_collection_properties(
                collection_name=self._physical_collection_name,
                property_keys=[f"{_INDEX_META_PROPERTY_PREFIX}{index_name}"],
                timeout=self._timeout_seconds,
            )
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
                        _META_VECTOR_FIELD: _META_VECTOR_VALUE,
                    }
                ],
                timeout=self._timeout_seconds,
            )

    def _prepare_record_for_write(self, record: Dict[str, Any]) -> Dict[str, Any]:
        prepared: Dict[str, Any] = {}
        materialized = dict(record)
        for field_name, default_value in self._field_defaults.items():
            if materialized.get(field_name) is None:
                materialized[field_name] = deepcopy(default_value)
        for field_name, value in materialized.items():
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
        for field_name, default_value in self._field_defaults.items():
            if decoded.get(field_name) is None:
                decoded[field_name] = deepcopy(default_value)
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
        if sparse_vector:
            raise NotImplementedError(
                "Milvus sparse and hybrid search is not supported because candidate recall "
                "cannot be made complete"
            )
        if dense_vector is None:
            return self._search_by_sparse(sparse_vector, limit, offset, filters, output_fields)

        fetch_limit = max(limit + offset, limit)
        fields = self._select_output_fields(
            output_fields,
            include_vector=False,
            include_sparse=False,
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
            items.append(SearchItemResult(id=record_id, fields=payload, score=score))
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
        del limit, offset, filters, output_fields
        raise NotImplementedError(
            "Milvus sparse search is not supported because a complete sparse candidate set "
            "cannot be guaranteed"
        )

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
        result = self.search_by_vector(
            index_name=index_name,
            dense_vector=dense_vector,
            sparse_vector=None,
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
        if limit + offset > _DEFAULT_QUERY_LIMIT:
            raise ValueError(
                "Milvus scalar ordering supports at most 10000 rows; the requested window "
                "would be incomplete"
            )
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
            limit=_TRUNCATION_PROBE_LIMIT,
            timeout=self._timeout_seconds,
        )
        if len(rows) >= _TRUNCATION_PROBE_LIMIT:
            raise ValueError(
                "Milvus scalar ordering cannot safely process more than 10000 matching rows"
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
                    limit=_TRUNCATION_PROBE_LIMIT,
                    timeout=self._timeout_seconds,
                )
                if len(rows) >= _TRUNCATION_PROBE_LIMIT:
                    raise ValueError(
                        "Milvus count fallback cannot safely process more than 10000 rows"
                    )
                total = len(rows)
            return AggregateResult(agg={"_total": total}, op=op, field=None)

        rows = self._client.query(
            collection_name=self._physical_collection_name,
            filter=filters or "",
            output_fields=[field],
            limit=_TRUNCATION_PROBE_LIMIT,
            timeout=self._timeout_seconds,
        )
        if len(rows) >= _TRUNCATION_PROBE_LIMIT:
            raise ValueError(
                "Milvus grouped aggregation cannot safely process more than 10000 matching rows"
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
            if not expr:
                return ""
            field_name = self._validate_field(field)
            return self._join("or", [f"not ({expr})", f"{field_name} is null"])
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
        self._collection: Optional[Collection]
        self._uri = uri
        self._token = token
        self._db_name = db_name
        self._consistency_level = consistency_level
        self._timeout_seconds = int(timeout_seconds)
        self._project_name = project_name
        _validate_business_namespace(project_name, collection_name)
        self._distance_metric = _normalize_distance(distance_metric)
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._client = None
        self._resolved_physical_collection_name: Optional[str] = None

    @classmethod
    def from_config(cls, config: Any):
        cfg = getattr(config, "milvus", None)
        params = dict(getattr(config, "custom_params", {}) or {})
        cfg_fields_set: set[str] = (
            getattr(cfg, "model_fields_set", set()) if cfg is not None else set()
        )
        explicit_uri = getattr(cfg, "uri", None) if "uri" in cfg_fields_set else None
        uri = (
            explicit_uri
            or getattr(config, "url", None)
            or params.get("uri")
            or getattr(cfg, "uri", None)
        )
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
        return self._resolved_physical_collection_name or _safe_collection_name(
            self._project_name,
            self._collection_name,
            prefix=_DATA_COLLECTION_PREFIX,
        )

    @property
    def legacy_physical_collection_name(self) -> str:
        return _legacy_collection_name(self._project_name, self._collection_name)

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

    def _new_collection(
        self,
        meta: Optional[Dict[str, Any]] = None,
        *,
        physical_collection_name: Optional[str] = None,
    ) -> MilvusCollection:
        resolved_physical_name = physical_collection_name or self.physical_collection_name
        allow_legacy_sidecar = (
            self._resolved_physical_collection_name == resolved_physical_name
            and resolved_physical_name == self.legacy_physical_collection_name
        )
        return MilvusCollection(
            client=self._connect(),
            logical_collection_name=self._collection_name,
            physical_collection_name=resolved_physical_name,
            project_name=self._project_name,
            dense_vector_name=self._dense_vector_name,
            sparse_vector_name=self._sparse_vector_name,
            distance_metric=self._distance_metric,
            timeout_seconds=self._timeout_seconds,
            allow_legacy_sidecar=allow_legacy_sidecar,
            meta=meta,
        )

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        raw_collection = self._new_collection()
        if not raw_collection.collection_exists():
            legacy_name = self.legacy_physical_collection_name
            if legacy_name == self.physical_collection_name:
                return
            raw_collection = self._new_collection(physical_collection_name=legacy_name)
            if not raw_collection.collection_exists():
                return
            self._resolved_physical_collection_name = legacy_name
            raw_collection = self._new_collection(physical_collection_name=legacy_name)
        meta = raw_collection.load_remote_meta()
        if not meta:
            raise RuntimeError(
                "Milvus collection exists but OpenViking metadata is missing: "
                f"{self.physical_collection_name}. Use a different project/name, restore metadata, "
                "or drop the stale Milvus collection."
            )
        self._collection = Collection(raw_collection)

    def create_collection(
        self,
        name: str,
        schema: Dict[str, Any],
        *,
        distance: str,
        sparse_weight: float,
        index_name: str,
    ) -> bool:
        if sparse_weight > 0.0:
            raise NotImplementedError(
                "Milvus sparse and hybrid indexes are not supported because complete recall "
                "cannot be guaranteed"
            )
        _validate_business_namespace(self._project_name, name)
        self._collection_name = name
        self._index_name = index_name
        self._load_existing_collection_if_needed()
        if self._collection is None:
            return super().create_collection(
                name,
                schema,
                distance=distance,
                sparse_weight=sparse_weight,
                index_name=index_name,
            )

        raw_collection = self._new_collection()
        remote_meta = raw_collection.load_remote_meta()
        if not remote_meta:
            raise RuntimeError(
                f"Existing Milvus collection {self.physical_collection_name!r} has no metadata"
            )
        raw_collection.ensure_schema_compatible(schema)
        scalar_fields = self._sanitize_scalar_index_fields(
            scalar_index_fields=schema.get("ScalarIndex", []),
            fields_meta=schema.get("Fields", []),
        )
        raw_collection.create_index(
            index_name,
            self._build_default_index_meta(
                index_name=index_name,
                distance=distance,
                use_sparse=False,
                sparse_weight=0.0,
                scalar_index_fields=scalar_fields,
            ),
        )
        self._collection = Collection(raw_collection)
        return False

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        raw_collection = self._new_collection(meta)
        raw_collection.create_remote_collection(meta, consistency_level=self._consistency_level)
        return Collection(raw_collection)

    def drop_collection(self) -> bool:
        """Drop an owned business collection before removing its exact sidecar row."""
        self._load_existing_collection_if_needed()
        raw_collection = self._new_collection()
        if not raw_collection.collection_exists():
            raw_collection._validate_all_sidecar_ownership()
            raw_collection._delete_meta_record(ignore_missing=True)
            self._collection = None
            return False

        raw_collection.drop()
        self._collection = None
        return True

    def close(self) -> None:
        super().close()
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()
            self._client = None
        self._resolved_physical_collection_name = None

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
            raise NotImplementedError(
                "Milvus sparse and hybrid indexes are not supported because complete recall "
                "cannot be guaranteed"
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
        collection = self.get_collection()
        meta = collection.get_meta_data() or {}
        return MilvusCollection._build_field_type_map(meta)

    def _compile_filter(self, expr: FilterExpr | Dict[str, Any] | str | None) -> str:
        return MilvusFilterCompiler(self._field_types_for_filter()).compile(expr)

    def update_data(self, data_list: List[Dict[str, Any]]):
        collection = self.get_collection()
        normalized = [self._normalize_record_for_write(item) for item in data_list]
        result = collection.update_data(normalized)
        return [str(item) for item in (result or []) if item is not None]
