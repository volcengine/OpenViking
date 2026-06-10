# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""openGauss-backed vector collection adapter.

The official openGauss container ships a native ``vector`` type and HNSW/IVFFlat
index support.  The adapter stores one SQL table per OpenViking collection and
keeps collection/index metadata in small sidecar tables.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

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

_COLLECTION_META_TABLE = "__openviking_opengauss_collections"
_INDEX_META_TABLE = "__openviking_opengauss_indexes"
_DEFAULT_SCHEMA = "public"
_IDENT_RE = re.compile(r"[^a-zA-Z0-9_]+")
_MAX_IDENTIFIER_BYTES = 63

_FIELD_TYPE_SQL: Dict[str, str] = {
    "string": "TEXT",
    "path": "TEXT",
    "int64": "BIGINT",
    "int32": "INTEGER",
    "float": "DOUBLE PRECISION",
    "double": "DOUBLE PRECISION",
    "bool": "BOOLEAN",
    "boolean": "BOOLEAN",
    "date_time": "TIMESTAMP WITH TIME ZONE",
}

_VECTOR_OPS: Dict[str, Dict[str, str]] = {
    "cosine": {"operator": "<=>", "hnsw": "vector_cosine_ops"},
    "ip": {"operator": "<#>", "hnsw": "vector_ip_ops"},
    "l2": {"operator": "<->", "hnsw": "vector_l2_ops"},
}


def _import_psycopg2():
    try:
        import psycopg2  # type: ignore  # noqa: PLC0415

        return psycopg2
    except ImportError as exc:  # pragma: no cover - exercised only without optional driver
        raise ImportError(
            "The openGauss backend requires a psycopg2-compatible driver. "
            'Install it with `pip install "openviking[opengauss]"`, or install another '
            "psycopg2-compatible driver that can connect to openGauss."
        ) from exc


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _qualify(schema: str, name: str) -> str:
    return f"{_quote_ident(schema)}.{_quote_ident(name)}"


def _safe_identifier(*parts: Any, prefix: str = "ov") -> str:
    raw = "_".join(str(part or "") for part in parts)
    normalized = _IDENT_RE.sub("_", raw).strip("_").lower() or "default"
    name = f"{prefix}_{normalized}"
    encoded = name.encode("utf-8")
    if len(encoded) <= _MAX_IDENTIFIER_BYTES:
        return name
    import hashlib

    digest = hashlib.sha1(encoded).hexdigest()[:10]
    keep = max(8, _MAX_IDENTIFIER_BYTES - len(prefix) - len(digest) - 3)
    return f"{prefix}_{normalized[:keep]}_{digest}"


def _json_default(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)


def _encode_scope_roots(value: Any) -> str:
    roots = value if isinstance(value, list) else [value]
    normalized = [str(root) for root in roots if root is not None]
    return "\n" + "\n".join(normalized) + "\n" if normalized else "\n"


def _vector_literal(vector: Sequence[float]) -> str:
    values = []
    for item in vector:
        number = float(item)
        if not math.isfinite(number):
            number = 0.0
        values.append(format(number, ".12g"))
    return "[" + ",".join(values) + "]"


def _normalize_distance(distance: str) -> str:
    value = (distance or "cosine").strip().lower()
    if value not in _VECTOR_OPS:
        raise ValueError(
            "openGauss vector backend supports only cosine, l2, and ip distance metrics; "
            f"got {distance!r}"
        )
    return value


def _coerce_sql_value(value: Any, field_type: str | None) -> Any:
    if value is None:
        return None
    normalized_type = (field_type or "").lower()
    if normalized_type == "date_time":
        if isinstance(value, dt.datetime):
            return value
        if isinstance(value, dt.date):
            return dt.datetime.combine(value, dt.time.min, tzinfo=dt.timezone.utc)
        return str(value)
    if normalized_type in {"int64", "int32"}:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if normalized_type in {"float", "double"}:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
    if normalized_type in {"bool", "boolean"}:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "t", "yes", "y", "1", "on"}:
                return True
            if lowered in {"false", "f", "no", "n", "0", "off"}:
                return False
            return None
        return bool(value)
    if isinstance(value, (dict, list, tuple)):
        return _json_dumps(value)
    return value


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


class OpenGaussIndex(IIndex):
    """Metadata-only logical index facade for openGauss."""

    def __init__(
        self, collection: "OpenGaussCollection", index_name: str, meta: Dict[str, Any]
    ) -> None:
        super().__init__(meta=meta)
        self._collection = collection
        self._index_name = index_name
        self._meta = dict(meta)

    def upsert_data(self, delta_list: List[DeltaRecord]):
        raise NotImplementedError("OpenGaussIndex.upsert_data is managed at collection level")

    def delete_data(self, delta_list: List[DeltaRecord]):
        raise NotImplementedError("OpenGaussIndex.delete_data is managed at collection level")

    def search(
        self,
        query_vector: Optional[List[float]],
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        sparse_raw_terms: Optional[List[str]] = None,
        sparse_values: Optional[List[float]] = None,
    ) -> Tuple[List[int], List[float]]:
        raise NotImplementedError("OpenGaussIndex.search is not exposed via raw index interface")

    def aggregate(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raise NotImplementedError("OpenGaussIndex.aggregate is not exposed via raw index interface")

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


class OpenGaussCollection(ICollection):
    """A single OpenViking collection stored in openGauss."""

    DEFAULT_FETCH_LIMIT = 256
    DEFAULT_SPARSE_SCAN_LIMIT = 10_000
    INTERNAL_PATH_FIELDS = {
        "parent_uri": "TEXT",
        "scope_roots": "TEXT",
        "uri_depth": "BIGINT",
    }

    def __init__(
        self,
        *,
        conn: Any,
        schema_name: str,
        table_name: str,
        logical_collection_name: str,
        project_name: str,
        dense_vector_name: str,
        sparse_vector_name: str,
        distance_metric: str,
        meta: Optional[Dict[str, Any]] = None,
        vector_dim: int = 0,
        lock: Optional[threading.RLock] = None,
        reconnect: Optional[Any] = None,
    ) -> None:
        super().__init__()
        self._conn = conn
        self._lock = lock or threading.RLock()
        self._reconnect = reconnect
        self._schema_name = (schema_name or _DEFAULT_SCHEMA).strip() or _DEFAULT_SCHEMA
        self._table_name = table_name
        self._logical_collection_name = logical_collection_name
        self._project_name = project_name
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._distance_metric = _normalize_distance(distance_metric)
        self._meta = dict(meta or {})
        self._vector_dim = int(vector_dim or self._extract_vector_dim(self._meta) or 0)
        self._field_types = self._build_field_type_map(self._meta)

    @property
    def collection_key(self) -> str:
        return self._table_name

    @staticmethod
    def _extract_vector_dim(meta: Dict[str, Any]) -> int:
        for field in meta.get("Fields", []) or []:
            if field.get("FieldType") == "vector":
                try:
                    return int(field.get("Dim") or 0)
                except (TypeError, ValueError):
                    return 0
        return 0

    @staticmethod
    def _build_field_type_map(meta: Dict[str, Any]) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for field in meta.get("Fields", []) or []:
            name = field.get("FieldName")
            field_type = field.get("FieldType")
            if name and field_type:
                mapping[str(name)] = str(field_type)
        mapping.setdefault("id", "string")
        mapping.setdefault("parent_uri", "path")
        mapping.setdefault("scope_roots", "string")
        mapping.setdefault("uri_depth", "int64")
        return mapping

    def _ensure_connection(self) -> Any:
        if getattr(self._conn, "closed", 0):
            if self._reconnect is None:
                raise RuntimeError("openGauss connection is closed")
            self._conn = self._reconnect()
        return self._conn

    def _safe_rollback(self) -> None:
        try:
            if not getattr(self._conn, "closed", 1):
                self._conn.rollback()
        except Exception:
            logger.debug("Failed to rollback openGauss connection", exc_info=True)

    def _execute(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None,
        *,
        fetch: bool = False,
    ) -> List[Tuple[Any, ...]]:
        with self._lock:
            conn = self._ensure_connection()
            cur = conn.cursor()
            try:
                cur.execute(sql, list(params or []))
                rows = cur.fetchall() if fetch else []
                conn.commit()
                return rows
            except Exception:
                self._safe_rollback()
                raise
            finally:
                cur.close()

    def _table_exists(self) -> bool:
        rows = self._execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            LIMIT 1
            """,
            [self._schema_name, self._table_name],
            fetch=True,
        )
        return bool(rows)

    def create_remote_collection(self, meta_data: Dict[str, Any]) -> None:
        self._meta = dict(meta_data)
        self._vector_dim = self._extract_vector_dim(self._meta)
        self._field_types = self._build_field_type_map(self._meta)
        if self._vector_dim <= 0:
            raise ValueError("openGauss collection requires a positive dense vector dimension")

        columns = ["id TEXT PRIMARY KEY"]
        seen = {"id"}
        for field in meta_data.get("Fields", []) or []:
            ddl = self._field_to_column_ddl(field)
            field_name = field.get("FieldName")
            if ddl and field_name not in seen:
                columns.append(ddl)
                seen.add(str(field_name))
        for field_name, sql_type in self.INTERNAL_PATH_FIELDS.items():
            if field_name not in seen:
                columns.append(f"{_quote_ident(field_name)} {sql_type}")
                seen.add(field_name)

        self._execute(f"CREATE TABLE IF NOT EXISTS {self._table_ref()} ({', '.join(columns)})")
        self._save_collection_meta(meta_data)

    def _field_to_column_ddl(self, field: Dict[str, Any]) -> Optional[str]:
        name = field.get("FieldName")
        field_type = str(field.get("FieldType") or "").lower()
        if not name or name == "id":
            return None
        if field_type == "vector":
            dim = int(field.get("Dim") or self._vector_dim or 0)
            if dim <= 0:
                raise ValueError("openGauss vector field requires Dim")
            return f"{_quote_ident(name)} vector({dim})"
        if field_type == "sparse_vector":
            return f"{_quote_ident(name)} TEXT"
        return f"{_quote_ident(name)} {_FIELD_TYPE_SQL.get(field_type, 'TEXT')}"

    def _save_collection_meta(self, meta: Dict[str, Any]) -> None:
        self._execute(
            f"""
            MERGE INTO {self._meta_table_ref(_COLLECTION_META_TABLE)} target
            USING (
                SELECT
                    %s AS table_name,
                    %s AS logical_collection_name,
                    %s AS project_name,
                    %s AS meta_json
            ) source
            ON (target.table_name = source.table_name)
            WHEN MATCHED THEN UPDATE SET
                logical_collection_name = source.logical_collection_name,
                project_name = source.project_name,
                meta_json = source.meta_json,
                updated_at = CURRENT_TIMESTAMP
            WHEN NOT MATCHED THEN INSERT
                (table_name, logical_collection_name, project_name, meta_json, updated_at)
                VALUES (
                    source.table_name,
                    source.logical_collection_name,
                    source.project_name,
                    source.meta_json,
                    CURRENT_TIMESTAMP
                )
            """,
            [
                self.collection_key,
                self._logical_collection_name,
                self._project_name,
                _json_dumps(meta),
            ],
        )

    def _save_index_meta(self, index_name: str, meta: Dict[str, Any]) -> None:
        self._execute(
            f"""
            MERGE INTO {self._meta_table_ref(_INDEX_META_TABLE)} target
            USING (
                SELECT
                    %s AS table_name,
                    %s AS index_name,
                    %s AS meta_json
            ) source
            ON (
                target.table_name = source.table_name
                AND target.index_name = source.index_name
            )
            WHEN MATCHED THEN UPDATE SET
                meta_json = source.meta_json,
                updated_at = CURRENT_TIMESTAMP
            WHEN NOT MATCHED THEN INSERT
                (table_name, index_name, meta_json, updated_at)
                VALUES (
                    source.table_name,
                    source.index_name,
                    source.meta_json,
                    CURRENT_TIMESTAMP
                )
            """,
            [self.collection_key, index_name, _json_dumps(meta)],
        )

    def _delete_index_meta(self, index_name: str) -> None:
        self._execute(
            f"DELETE FROM {self._meta_table_ref(_INDEX_META_TABLE)} "
            "WHERE table_name = %s AND index_name = %s",
            [self.collection_key, index_name],
        )

    def _all_columns(self) -> List[str]:
        rows = self._execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            [self._schema_name, self._table_name],
            fetch=True,
        )
        return [str(row[0]) for row in rows]

    def _table_ref(self) -> str:
        return _qualify(self._schema_name, self._table_name)

    def _meta_table_ref(self, table_name: str) -> str:
        return _qualify(self._schema_name, table_name)

    def _index_ref(self, index_name: str) -> str:
        return _qualify(self._schema_name, index_name)

    def _function_table_name(self, table_name: str) -> str:
        return f"{_quote_ident(self._schema_name)}.{_quote_ident(table_name)}"

    def _ensure_columns_for_record(self, record: Dict[str, Any]) -> None:
        existing = set(self._all_columns())
        for field_name in record:
            if field_name in existing:
                continue
            if field_name == "id":
                continue
            if field_name == self._dense_vector_name:
                sql_type = f"vector({self._vector_dim})"
            elif field_name == self._sparse_vector_name:
                sql_type = "TEXT"
            elif field_name in self.INTERNAL_PATH_FIELDS:
                sql_type = self.INTERNAL_PATH_FIELDS[field_name]
            else:
                field_type = self._field_types.get(field_name)
                sql_type = _FIELD_TYPE_SQL.get(str(field_type or "").lower(), "TEXT")
                self._field_types.setdefault(field_name, field_type or "string")
            self._execute(
                f"ALTER TABLE {self._table_ref()} ADD COLUMN {_quote_ident(field_name)} {sql_type}"
            )
            existing.add(field_name)

    def _select_columns(
        self,
        output_fields: Optional[List[str]],
        *,
        include_vector: bool = False,
        include_sparse: bool = False,
    ) -> List[str]:
        columns = self._all_columns()
        wanted = ["id"]
        if output_fields:
            for field in output_fields:
                if field != "id" and field in columns and field not in wanted:
                    wanted.append(field)
        else:
            for field in columns:
                if field == "id":
                    continue
                if field == self._dense_vector_name and not include_vector:
                    continue
                if field == self._sparse_vector_name and not include_sparse:
                    continue
                wanted.append(field)
        if (
            include_vector
            and self._dense_vector_name in columns
            and self._dense_vector_name not in wanted
        ):
            wanted.append(self._dense_vector_name)
        if (
            include_sparse
            and self._sparse_vector_name in columns
            and self._sparse_vector_name not in wanted
        ):
            wanted.append(self._sparse_vector_name)
        return wanted

    def _row_to_payload(
        self, row: Sequence[Any], columns: Sequence[str]
    ) -> Tuple[Any, Dict[str, Any]]:
        record = dict(zip(columns, row, strict=False))
        record_id = record.pop("id", "")
        record.pop(self._dense_vector_name, None)
        sparse = record.pop(self._sparse_vector_name, None)
        if sparse is not None and self._sparse_vector_name in columns:
            try:
                record[self._sparse_vector_name] = json.loads(sparse)
            except (TypeError, ValueError):
                record[self._sparse_vector_name] = sparse
        return record_id, record

    def _where_sql(self, filters: Optional[Dict[str, Any]]) -> Tuple[str, List[Any]]:
        clause, params = self._compile_filter(filters or {})
        return (f" WHERE {clause}", params) if clause else ("", [])

    def _compile_filter(self, payload: Any) -> Tuple[str, List[Any]]:
        if not payload:
            return "", []
        if not isinstance(payload, dict):
            return "", []
        op = str(payload.get("op") or "").lower()
        if not op:
            clauses = []
            params: List[Any] = []
            for key, value in payload.items():
                clauses.append(f"{_quote_ident(str(key))} = %s")
                params.append(value)
            return " AND ".join(clauses), params
        if op == "and":
            return self._join_filter_clauses("AND", payload.get("conds", []) or [])
        if op == "or":
            return self._join_filter_clauses("OR", payload.get("conds", []) or [])
        if op == "must":
            field = str(payload.get("field") or "")
            values = list(payload.get("conds", []) or [])
            if not field or not values:
                return "", []
            return self._match_sql(field, values, negate=False)
        if op == "must_not":
            field = str(payload.get("field") or "")
            values = list(payload.get("conds", []) or [])
            if not field or not values:
                return "", []
            return self._match_sql(field, values, negate=True)
        if op in {"range", "time_range"}:
            field = str(payload.get("field") or "")
            clauses = []
            params = []
            for sql_op, key in ((">=", "gte"), (">", "gt"), ("<=", "lte"), ("<", "lt")):
                if payload.get(key) is not None:
                    clauses.append(f"{_quote_ident(field)} {sql_op} %s")
                    params.append(_coerce_sql_value(payload[key], self._field_types.get(field)))
            return " AND ".join(clauses), params
        if op == "range_out":
            field = str(payload.get("field") or "")
            clauses = []
            params = []
            if payload.get("gte") is not None:
                clauses.append(f"{_quote_ident(field)} < %s")
                params.append(payload["gte"])
            if payload.get("lte") is not None:
                clauses.append(f"{_quote_ident(field)} > %s")
                params.append(payload["lte"])
            return " OR ".join(clauses), params
        if op == "contains":
            field = str(payload.get("field") or "")
            return f"{_quote_ident(field)} LIKE %s", [f"%{payload.get('substring', '')}%"]
        if op == "prefix":
            field = str(payload.get("field") or "")
            return f"{_quote_ident(field)} LIKE %s", [f"{payload.get('prefix', '')}%"]
        logger.warning("openGauss vector backend ignoring unsupported filter op=%r", op)
        return "", []

    def _join_filter_clauses(self, operator: str, conds: Iterable[Any]) -> Tuple[str, List[Any]]:
        clauses = []
        params: List[Any] = []
        for cond in conds:
            clause, cond_params = self._compile_filter(cond)
            if clause:
                clauses.append(f"({clause})")
                params.extend(cond_params)
        return f" {operator} ".join(clauses), params

    def _match_sql(self, field: str, values: List[Any], *, negate: bool) -> Tuple[str, List[Any]]:
        if field == "scope_roots":
            clauses = []
            params = []
            for value in values:
                clauses.append(f"{_quote_ident(field)} LIKE %s")
                params.append(f"%\n{str(value)}\n%")
            joined = " OR ".join(clauses)
        elif len(values) == 1:
            joined = f"{_quote_ident(field)} = %s"
            params = [values[0]]
        else:
            joined = f"{_quote_ident(field)} IN ({', '.join(['%s'] * len(values))})"
            params = values
        return (f"NOT ({joined})" if negate else joined), params

    # ------------------------------------------------------------------
    # ICollection lifecycle
    # ------------------------------------------------------------------
    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        meta = dict(self.get_meta_data() or {})
        if fields is not None:
            meta["Fields"] = fields
        if description is not None:
            meta["Description"] = description
        self._meta = meta
        self._field_types = self._build_field_type_map(meta)
        self._save_collection_meta(meta)
        return meta

    def get_meta_data(self):
        rows = self._execute(
            f"SELECT meta_json FROM {self._meta_table_ref(_COLLECTION_META_TABLE)} WHERE table_name = %s",
            [self.collection_key],
            fetch=True,
        )
        if not rows:
            return {}
        try:
            return json.loads(rows[0][0])
        except (TypeError, ValueError):
            return {}

    def close(self):
        return None

    def drop(self):
        self._execute(f"DROP TABLE IF EXISTS {self._table_ref()}")
        self._execute(
            f"DELETE FROM {self._meta_table_ref(_INDEX_META_TABLE)} WHERE table_name = %s",
            [self.collection_key],
        )
        self._execute(
            f"DELETE FROM {self._meta_table_ref(_COLLECTION_META_TABLE)} WHERE table_name = %s",
            [self.collection_key],
        )

    def create_index(self, index_name: str, meta_data: Dict[str, Any]):
        full_meta = dict(meta_data)
        full_meta["IndexName"] = index_name
        vector_meta = dict(full_meta.get("VectorIndex", {}) or {})
        distance = _normalize_distance(vector_meta.get("Distance") or self._distance_metric)
        vector_meta["IndexType"] = "hnsw"
        full_meta["VectorIndex"] = vector_meta
        self._create_vector_index(index_name, distance, full_meta)
        for field_name in full_meta.get("ScalarIndex", []) or []:
            self._create_scalar_index(index_name, str(field_name))
        self._save_index_meta(index_name, full_meta)
        return OpenGaussIndex(self, index_name, full_meta)

    def _create_vector_index(
        self,
        index_name: str,
        distance: str,
        meta: Dict[str, Any],
    ) -> None:
        if self._dense_vector_name not in self._all_columns():
            return
        ops_class = _VECTOR_OPS[distance]["hnsw"]
        pg_index_name = _safe_identifier(self._table_name, index_name, "vec", prefix="idx")
        try:
            hnsw = meta.get("VectorIndex") or {}
            m = int(hnsw.get("M") or hnsw.get("m") or 16)
            ef = int(hnsw.get("EfConstruction") or hnsw.get("ef_construction") or 64)
            self._execute(
                f"""
                CREATE INDEX IF NOT EXISTS {_quote_ident(pg_index_name)}
                ON {self._table_ref()}
                USING hnsw ({_quote_ident(self._dense_vector_name)} {ops_class})
                WITH (m = {m}, ef_construction = {ef})
                """
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to create openGauss vector index {pg_index_name}") from exc

    def _create_scalar_index(self, index_name: str, field_name: str) -> None:
        if field_name not in self._all_columns():
            return
        pg_index_name = _safe_identifier(self._table_name, index_name, field_name, prefix="idx")
        try:
            self._execute(
                f"""
                CREATE INDEX IF NOT EXISTS {_quote_ident(pg_index_name)}
                ON {self._table_ref()} ({_quote_ident(field_name)})
                """
            )
        except Exception as exc:
            logger.warning("Failed to create openGauss scalar index %s: %s", field_name, exc)

    def has_index(self, index_name: str) -> bool:
        return index_name in self.list_indexes()

    def get_index(self, index_name: str) -> Optional[IIndex]:
        meta = self.get_index_meta_data(index_name)
        if not meta:
            return None
        return OpenGaussIndex(self, index_name, meta)

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
        for field_name in meta.get("ScalarIndex", []) or []:
            self._create_scalar_index(index_name, str(field_name))
        self._save_index_meta(index_name, meta)
        return meta

    def get_index_meta_data(self, index_name: str):
        rows = self._execute(
            f"""
            SELECT meta_json
            FROM {self._meta_table_ref(_INDEX_META_TABLE)}
            WHERE table_name = %s AND index_name = %s
            """,
            [self.collection_key, index_name],
            fetch=True,
        )
        if not rows:
            return None
        try:
            return json.loads(rows[0][0])
        except (TypeError, ValueError):
            return None

    def list_indexes(self):
        rows = self._execute(
            f"SELECT index_name FROM {self._meta_table_ref(_INDEX_META_TABLE)} "
            "WHERE table_name = %s ORDER BY index_name",
            [self.collection_key],
            fetch=True,
        )
        return [str(row[0]) for row in rows]

    def drop_index(self, index_name: str):
        meta = self.get_index_meta_data(index_name) or {}
        pg_index_name = _safe_identifier(self._table_name, index_name, "vec", prefix="idx")
        self._execute(f"DROP INDEX IF EXISTS {self._index_ref(pg_index_name)}")
        for field_name in meta.get("ScalarIndex", []) or []:
            scalar_index_name = _safe_identifier(
                self._table_name, index_name, field_name, prefix="idx"
            )
            self._execute(f"DROP INDEX IF EXISTS {self._index_ref(scalar_index_name)}")
        self._delete_index_meta(index_name)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
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
        if limit <= 0:
            return SearchResult()
        if dense_vector is None and sparse_vector is None:
            return SearchResult()
        fetch_limit = max(limit + offset, limit)
        if dense_vector is None:
            return self._search_by_sparse(
                sparse_vector, fetch_limit, offset, limit, filters, output_fields
            )

        columns = self._select_columns(output_fields, include_sparse=bool(sparse_vector))
        where_sql, params = self._where_sql(filters)
        operator = _VECTOR_OPS[self._distance_metric]["operator"]
        vector_text = _vector_literal(dense_vector)
        sql = (
            f"SELECT {', '.join(_quote_ident(col) for col in columns)}, "
            f"{_quote_ident(self._dense_vector_name)} {operator} %s::vector AS _distance "
            f"FROM {self._table_ref()}"
            f"{where_sql} "
            f"ORDER BY {_quote_ident(self._dense_vector_name)} {operator} %s::vector "
            "LIMIT %s OFFSET %s"
        )
        rows = self._execute(
            sql, [vector_text] + params + [vector_text, fetch_limit, 0], fetch=True
        )
        scored_items: List[SearchItemResult] = []
        for row in rows:
            record_id, payload = self._row_to_payload(row[:-1], columns)
            distance = row[-1]
            score = self._distance_to_score(distance, self._distance_metric)
            if sparse_vector:
                sparse_payload = payload.pop(self._sparse_vector_name, None)
                if isinstance(sparse_payload, dict):
                    score += _sparse_dot(sparse_vector, sparse_payload)
            scored_items.append(SearchItemResult(id=record_id, fields=payload, score=score))
        if sparse_vector:
            scored_items.sort(key=lambda item: item.score or 0.0, reverse=True)
        return SearchResult(data=scored_items[offset : offset + limit])

    def _search_by_sparse(
        self,
        sparse_vector: Optional[Dict[str, float]],
        fetch_limit: int,
        offset: int,
        limit: int,
        filters: Optional[Dict[str, Any]],
        output_fields: Optional[List[str]],
    ) -> SearchResult:
        if not sparse_vector:
            return SearchResult()
        columns = self._select_columns(output_fields, include_sparse=True)
        where_sql, params = self._where_sql(filters)
        sql = (
            f"SELECT {', '.join(_quote_ident(col) for col in columns)} "
            f"FROM {self._table_ref()}{where_sql} LIMIT %s"
        )
        rows = self._execute(
            sql, params + [max(fetch_limit, self.DEFAULT_SPARSE_SCAN_LIMIT)], fetch=True
        )
        items = []
        for row in rows:
            record_id, payload = self._row_to_payload(row, columns)
            sparse_payload = payload.pop(self._sparse_vector_name, None)
            score = _sparse_dot(
                sparse_vector, sparse_payload if isinstance(sparse_payload, dict) else None
            )
            if score > 0:
                items.append(SearchItemResult(id=record_id, fields=payload, score=score))
        items.sort(key=lambda item: item.score or 0.0, reverse=True)
        return SearchResult(data=items[offset : offset + limit])

    @staticmethod
    def _distance_to_score(distance: Any, distance_metric: str = "cosine") -> float:
        try:
            value = float(distance)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(value):
            return 0.0
        if distance_metric == "ip":
            return -value
        return 1.0 / (1.0 + max(value, 0.0))

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
            "op": "or",
            "conds": [
                {"op": "contains", "field": field, "substring": query_text}
                for field in ("name", "description", "abstract", "tags")
                if field in self._all_columns()
            ],
        }
        filters = {"op": "and", "conds": [filters, text_filter]} if filters else text_filter
        return self.search_by_random(index_name, limit, offset, filters, output_fields)

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        columns = self._select_columns(output_fields, include_vector=True, include_sparse=True)
        rows = self._execute(
            f"SELECT {', '.join(_quote_ident(col) for col in columns)} "
            f"FROM {self._table_ref()} WHERE id = %s",
            [str(id)],
            fetch=True,
        )
        if not rows:
            return SearchResult()
        row_payload = dict(zip(columns, rows[0], strict=False))
        dense_vector = row_payload.get(self._dense_vector_name)
        sparse_raw = row_payload.get(self._sparse_vector_name)
        sparse_vector = None
        if sparse_raw:
            try:
                sparse_vector = json.loads(sparse_raw)
            except (TypeError, ValueError):
                sparse_vector = None
        result = self.search_by_vector(
            index_name=index_name,
            dense_vector=self._parse_vector_value(dense_vector),
            sparse_vector=sparse_vector,
            limit=limit + offset + 1,
            filters=filters,
            output_fields=output_fields,
        )
        data = [item for item in result.data if str(item.id) != str(id)]
        return SearchResult(data=data[offset : offset + limit])

    @staticmethod
    def _parse_vector_value(value: Any) -> Optional[List[float]]:
        if isinstance(value, list):
            return [float(item) for item in value]
        if not isinstance(value, str):
            return None
        stripped = value.strip().strip("[]")
        if not stripped:
            return None
        return [float(item.strip()) for item in stripped.split(",")]

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
        raise NotImplementedError("OpenGaussCollection.search_by_multimodal is not supported")

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        columns = self._select_columns(output_fields)
        where_sql, params = self._where_sql(filters)
        rows = self._execute(
            f"SELECT {', '.join(_quote_ident(col) for col in columns)} "
            f"FROM {self._table_ref()}{where_sql} LIMIT %s OFFSET %s",
            params + [limit, offset],
            fetch=True,
        )
        return SearchResult(
            data=[
                SearchItemResult(id=record_id, fields=payload, score=1.0)
                for record_id, payload in (self._row_to_payload(row, columns) for row in rows)
            ]
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
        columns = self._select_columns(output_fields)
        if field not in columns:
            columns.append(field)
        where_sql, params = self._where_sql(filters)
        direction = "DESC" if (order or "desc").lower() == "desc" else "ASC"
        rows = self._execute(
            f"SELECT {', '.join(_quote_ident(col) for col in columns)} "
            f"FROM {self._table_ref()}{where_sql} "
            f"ORDER BY {_quote_ident(field)} {direction} NULLS LAST LIMIT %s OFFSET %s",
            params + [limit, offset],
            fetch=True,
        )
        items = []
        for row in rows:
            record_id, payload = self._row_to_payload(row, columns)
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

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------
    def upsert_data(self, data_list: List[Dict[str, Any]], ttl=0):
        if not data_list:
            return []
        ids = []
        for record in data_list:
            record_id = record.get("id")
            if record_id is None or record_id == "":
                raise ValueError("openGauss vector record requires id")
            self._ensure_columns_for_record(record)
            columns = []
            values = []
            for column in self._all_columns():
                if column not in record:
                    continue
                columns.append(column)
                if column == self._dense_vector_name:
                    values.append(_vector_literal(record[column]))
                elif column == self._sparse_vector_name:
                    values.append(_json_dumps(record[column] or {}))
                elif column == "scope_roots":
                    values.append(_encode_scope_roots(record[column]))
                else:
                    values.append(_coerce_sql_value(record[column], self._field_types.get(column)))
            if "id" not in columns:
                columns.insert(0, "id")
                values.insert(0, str(record_id))
            self._upsert_row(columns, values)
            ids.append(record_id)
        return ids

    def _upsert_row(self, columns: List[str], values: List[Any]) -> None:
        id_index = columns.index("id")
        update_columns = [column for column in columns if column != "id"]
        update_values = [
            value for column, value in zip(columns, values, strict=False) if column != "id"
        ]
        set_parts = [
            f"{_quote_ident(column)} = {'%s::vector' if column == self._dense_vector_name else '%s'}"
            for column in update_columns
        ]
        insert_placeholders = [
            "%s::vector" if column == self._dense_vector_name else "%s" for column in columns
        ]

        with self._lock:
            conn = self._ensure_connection()
            cur = conn.cursor()
            try:
                if update_columns:
                    cur.execute(
                        f"UPDATE {self._table_ref()} SET {', '.join(set_parts)} WHERE id = %s",
                        update_values + [values[id_index]],
                    )
                if not update_columns or cur.rowcount == 0:
                    try:
                        cur.execute(
                            f"INSERT INTO {self._table_ref()} "
                            f"({', '.join(_quote_ident(column) for column in columns)}) "
                            f"VALUES ({', '.join(insert_placeholders)})",
                            values,
                        )
                    except Exception as exc:
                        if getattr(exc, "pgcode", None) != "23505" or not update_columns:
                            raise
                        self._safe_rollback()
                        cur.close()
                        conn = self._ensure_connection()
                        cur = conn.cursor()
                        cur.execute(
                            f"UPDATE {self._table_ref()} SET {', '.join(set_parts)} WHERE id = %s",
                            update_values + [values[id_index]],
                        )
                conn.commit()
            except Exception:
                self._safe_rollback()
                raise
            finally:
                cur.close()

    def fetch_data(self, primary_keys: List[Any]):
        if not primary_keys:
            return FetchDataInCollectionResult()
        columns = self._select_columns(None)
        placeholders = ", ".join(["%s"] * len(primary_keys))
        rows = self._execute(
            f"SELECT {', '.join(_quote_ident(col) for col in columns)} "
            f"FROM {self._table_ref()} WHERE id IN ({placeholders})",
            [str(pk) for pk in primary_keys],
            fetch=True,
        )
        items = []
        found_ids = set()
        for row in rows:
            record_id, payload = self._row_to_payload(row, columns)
            found_ids.add(str(record_id))
            items.append(DataItem(id=record_id, fields=payload))
        return FetchDataInCollectionResult(
            items=items,
            ids_not_exist=[pk for pk in primary_keys if str(pk) not in found_ids],
        )

    def delete_data(self, primary_keys: List[Any]):
        if not primary_keys:
            return None
        placeholders = ", ".join(["%s"] * len(primary_keys))
        self._execute(
            f"DELETE FROM {self._table_ref()} WHERE id IN ({placeholders})",
            [str(pk) for pk in primary_keys],
        )
        return None

    def delete_all_data(self):
        self._execute(f"DELETE FROM {self._table_ref()}")

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
        where_sql, params = self._where_sql(filters)
        if not field:
            rows = self._execute(
                f"SELECT COUNT(*) FROM {self._table_ref()}{where_sql}",
                params,
                fetch=True,
            )
            return AggregateResult(
                agg={"_total": int(rows[0][0]) if rows else 0}, op=op, field=None
            )
        rows = self._execute(
            f"SELECT {_quote_ident(field)}, COUNT(*) FROM {self._table_ref()}"
            f"{where_sql} GROUP BY {_quote_ident(field)}",
            params,
            fetch=True,
        )
        grouped = {row[0]: int(row[1]) for row in rows if row[0] is not None}
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


class OpenGaussCollectionAdapter(CollectionAdapter):
    """CollectionAdapter backed by openGauss native vector tables."""

    mode = "opengauss"
    INTERNAL_PATH_FIELDS = ["parent_uri", "scope_roots", "uri_depth"]

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        db_name: str,
        schema_name: str,
        project_name: str,
        collection_name: str,
        index_name: str,
        distance_metric: str,
        dense_vector_name: str,
        sparse_vector_name: str,
        connect_timeout: int,
        mode: str,
        shard_count: int,
    ) -> None:
        super().__init__(collection_name=collection_name, index_name=index_name)
        self._host = host
        self._port = int(port)
        self._user = user
        self._password = password
        self._db_name = db_name
        self._schema_name = (schema_name or _DEFAULT_SCHEMA).strip() or _DEFAULT_SCHEMA
        self._project_name = project_name
        self._distance_metric = _normalize_distance(distance_metric)
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._connect_timeout = int(connect_timeout)
        self._deployment_mode = mode
        self._shard_count = int(shard_count)
        self._conn = None
        self._lock = threading.RLock()

    @classmethod
    def from_config(cls, config: Any):
        cfg = getattr(config, "opengauss", None)
        params = dict(getattr(config, "custom_params", {}) or {})
        if cfg is None:
            raise ValueError("openGauss backend requires opengauss config")
        return cls(
            host=str(getattr(cfg, "host", None) or params.get("host") or "127.0.0.1"),
            port=int(getattr(cfg, "port", None) or params.get("port") or 5432),
            user=str(getattr(cfg, "user", None) or params.get("user") or "omm"),
            password=str(getattr(cfg, "password", None) or params.get("password") or ""),
            db_name=str(getattr(cfg, "db_name", None) or params.get("db_name") or "postgres"),
            schema_name=str(
                getattr(cfg, "schema_name", None)
                or getattr(cfg, "schema", None)
                or params.get("schema")
                or _DEFAULT_SCHEMA
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
            connect_timeout=int(
                getattr(cfg, "connect_timeout", None) or params.get("connect_timeout") or 10
            ),
            mode=str(getattr(cfg, "mode", None) or params.get("mode") or "standalone"),
            shard_count=int(getattr(cfg, "shard_count", None) or params.get("shard_count") or 32),
        )

    @property
    def physical_table_name(self) -> str:
        return _safe_identifier(self._project_name, self._collection_name, prefix="ov")

    def _connect(self):
        if self._conn is not None and not getattr(self._conn, "closed", 0):
            return self._conn
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                logger.debug("Failed to close stale openGauss connection", exc_info=True)
        psycopg2 = _import_psycopg2()
        self._conn = psycopg2.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            dbname=self._db_name,
            connect_timeout=self._connect_timeout,
        )
        self._ensure_meta_tables()
        return self._conn

    def _ensure_meta_tables(self) -> None:
        conn = self._conn
        if conn is None:
            return
        cur = conn.cursor()
        try:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_ident(self._schema_name)}")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_qualify(self._schema_name, _COLLECTION_META_TABLE)} (
                    table_name TEXT PRIMARY KEY,
                    logical_collection_name TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    meta_json TEXT NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_qualify(self._schema_name, _INDEX_META_TABLE)} (
                    table_name TEXT NOT NULL,
                    index_name TEXT NOT NULL,
                    meta_json TEXT NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (table_name, index_name)
                )
                """
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
        if self._deployment_mode == "distributed":
            self._try_make_reference_table(_COLLECTION_META_TABLE)
            self._try_make_reference_table(_INDEX_META_TABLE)

    def _try_make_reference_table(self, table_name: str) -> None:
        conn = self._conn
        if conn is None:
            return
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT create_reference_table(%s)", [self._function_table_name(table_name)]
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.warning(
                "Failed to mark openGauss metadata table %s as reference table: %s",
                self._function_table_name(table_name),
                exc,
            )
        finally:
            cur.close()

    def _new_collection(self, meta: Optional[Dict[str, Any]] = None) -> OpenGaussCollection:
        return OpenGaussCollection(
            conn=self._connect(),
            schema_name=self._schema_name,
            table_name=self.physical_table_name,
            logical_collection_name=self._collection_name,
            project_name=self._project_name,
            dense_vector_name=self._dense_vector_name,
            sparse_vector_name=self._sparse_vector_name,
            distance_metric=self._distance_metric,
            meta=meta,
            lock=self._lock,
            reconnect=self._connect,
        )

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        conn = self._connect()
        raw_collection = self._new_collection()
        if not raw_collection._table_exists():
            return
        meta = raw_collection.get_meta_data()
        if not meta:
            raise RuntimeError(
                "openGauss collection table exists but OpenViking metadata is missing: "
                f"{self.physical_table_name}. Use a different project/name, restore metadata, "
                "or drop the stale table."
            )
        self._collection = Collection(
            OpenGaussCollection(
                conn=conn,
                schema_name=self._schema_name,
                table_name=self.physical_table_name,
                logical_collection_name=self._collection_name,
                project_name=self._project_name,
                dense_vector_name=self._dense_vector_name,
                sparse_vector_name=self._sparse_vector_name,
                distance_metric=self._distance_metric,
                meta=meta,
                lock=self._lock,
                reconnect=self._connect,
            )
        )

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        raw_collection = self._new_collection(meta)
        raw_collection.create_remote_collection(meta)
        if self._deployment_mode == "distributed":
            self._try_make_distributed_table(self.physical_table_name)
        return Collection(raw_collection)

    def _try_make_distributed_table(self, table_name: str) -> None:
        conn = self._conn
        if conn is None:
            return
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT create_distributed_table(%s, 'id', 'hash', %s)",
                [self._function_table_name(table_name), self._shard_count],
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.warning(
                "Failed to distribute openGauss table %s with shard_count=%s: %s",
                table_name,
                self._shard_count,
                exc,
            )
        finally:
            cur.close()

    def close(self) -> None:
        super().close()
        if self._conn is not None:
            self._conn.close()
            self._conn = None

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
        index_meta = super()._build_default_index_meta(
            index_name=index_name,
            distance=distance,
            use_sparse=use_sparse,
            sparse_weight=sparse_weight,
            scalar_index_fields=scalar_index_fields,
        )
        index_meta["VectorIndex"]["IndexType"] = "hnsw_hybrid" if use_sparse else "hnsw"
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
            normalized["uri_depth"] = len(
                [part for part in normalized_uri.strip("/").split("/") if part]
            )
        return normalized

    def _normalize_record_for_read(self, record: Dict[str, Any]) -> Dict[str, Any]:
        normalized = super()._normalize_record_for_read(record)
        for field_name in self.INTERNAL_PATH_FIELDS:
            normalized.pop(field_name, None)
        return normalized

    def _coerce_datetime_value(self, value: Any) -> Any:
        if isinstance(value, dt.datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=dt.timezone.utc)
            return value.isoformat()
        return value

    def _compile_filter(self, expr: FilterExpr | Dict[str, Any] | None) -> Dict[str, Any]:
        if expr is None:
            return {}
        if isinstance(expr, dict):
            return self._normalize_filter_payload_for_write(expr)
        if isinstance(expr, RawDSL):
            return self._normalize_filter_payload_for_write(expr.payload)
        if isinstance(expr, And):
            conds = [self._compile_filter(cond) for cond in expr.conds if cond]
            return {"op": "and", "conds": [cond for cond in conds if cond]}
        if isinstance(expr, Or):
            conds = [self._compile_filter(cond) for cond in expr.conds if cond]
            return {"op": "or", "conds": [cond for cond in conds if cond]}
        if isinstance(expr, Eq):
            value = expr.value
            if expr.field in self._URI_FIELD_NAMES:
                value = self._normalize_path(self._encode_uri_field_value(value))
            return {"op": "must", "field": expr.field, "conds": [value]}
        if isinstance(expr, In):
            values = list(expr.values)
            if expr.field in self._URI_FIELD_NAMES:
                values = [
                    self._normalize_path(self._encode_uri_field_value(value)) for value in values
                ]
            return {"op": "must", "field": expr.field, "conds": values}
        if isinstance(expr, Range):
            payload: Dict[str, Any] = {"op": "range", "field": expr.field}
            for key in ("gte", "gt", "lte", "lt"):
                value = getattr(expr, key)
                if value is not None:
                    payload[key] = value
            return payload
        if isinstance(expr, TimeRange):
            payload: Dict[str, Any] = {"op": "range", "field": expr.field}
            if expr.start is not None:
                payload["gte"] = self._coerce_datetime_value(expr.start)
            if expr.end is not None:
                payload["lt"] = self._coerce_datetime_value(expr.end)
            return payload
        if isinstance(expr, Contains):
            return {"op": "contains", "field": expr.field, "substring": expr.substring}
        if isinstance(expr, PathScope):
            encoded_path = (
                self._normalize_path(self._encode_uri_field_value(expr.path))
                if expr.field in self._URI_FIELD_NAMES
                else expr.path
            )
            if expr.depth == 0:
                return {"op": "must", "field": expr.field, "conds": [encoded_path]}
            if expr.depth == 1:
                return {"op": "must", "field": "parent_uri", "conds": [encoded_path]}
            if expr.depth == -1:
                return {"op": "must", "field": "scope_roots", "conds": [encoded_path]}
            raise ValueError(
                f"OpenGauss adapter only supports PathScope depth 0/1/-1, got {expr.depth}"
            )
        raise TypeError(f"Unsupported filter expr type: {type(expr)!r}")
