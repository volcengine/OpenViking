# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""openGauss vector database adapter using psycopg2.

openGauss (DataVec) provides native vector types and HNSW / IVFFlat / DISKANN
indexes. No separate pgvector extension is required on openGauss >= 6.0.3.

Driver: any psycopg2-compatible driver.
  Install: pip install "openviking[opengauss]"  (installs psycopg2-binary)
  The openGauss-connector-python-psycopg2 package exposes the same `psycopg2`
  module interface and works as a drop-in alternative.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openviking.storage.expr import FilterExpr, Or
from openviking.storage.vectordb.collection.collection import Collection, ICollection
from openviking.storage.vectordb.collection.result import (
    AggregateResult,
    DataItem,
    FetchDataInCollectionResult,
    SearchItemResult,
    SearchResult,
)
from openviking.storage.vectordb.index.index import IIndex
from openviking.storage.vectordb_adapters.base import CollectionAdapter
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.vectordb_config import (
    OpenGaussConfig,
    normalize_opengauss_index_type,
    resolve_opengauss_index_spec,
    validate_opengauss_vector_constraints,
)

logger = get_logger(__name__)

_SUPPORTED_INDEX_TYPES = frozenset(
    {
        "hnsw",
        "hnsw-pq",
        "hnsw-rabitq",
        "ivfflat",
        "ivf-pq",
        "ivf-rabitq",
        "diskann",
    }
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_IDENTIFIER_CHAR_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PATH_SCOPE_DEPTH_PATTERN = re.compile(r"\s*-d=(-?\d+)\s*")
_MAX_SQL_IDENTIFIER = 63
_BOUNDED_IDENT_HASH_LEN = 12
# Official schema advertises ``content``, but USE_CONTENT_FIELD=False backends
# must not persist or SELECT that column.
_UNSTORED_FIELDS = frozenset({"content"})
_EMPTY_OR_FILTER: Dict[str, Any] = {"op": "or", "conds": []}
_QUANTIZATION_OPTION_NAMES = frozenset(
    {
        "enable_pq",
        "enable_rabitq",
        "pq_m",
        "pq_ksub",
        "by_residual",
        "rabitq_refine_type",
        "rabitq_fht",
    }
)

# ---------------------------------------------------------------------------
# Internal metadata table names
# ---------------------------------------------------------------------------
_META_TABLE = "_ov_collection_meta"

# ---------------------------------------------------------------------------
# Field type -> PostgreSQL/openGauss column type mapping
# ---------------------------------------------------------------------------
_FIELD_TYPE_MAP: Dict[str, str] = {
    "string": "TEXT",
    "path": "TEXT",
    "int64": "BIGINT",
    "int32": "INTEGER",
    "float": "DOUBLE PRECISION",
    "float32": "DOUBLE PRECISION",
    "bool": "BOOLEAN",
    "date_time": "BIGINT",
    "list<string>": "TEXT[]",
    "list<int64>": "BIGINT[]",
    "text": "TEXT",
    "vector": None,  # handled separately
    "sparse_vector": "JSONB",
}

# ---------------------------------------------------------------------------
# Distance metric -> DataVec operator
# ---------------------------------------------------------------------------
_DISTANCE_OP: Dict[str, str] = {
    "l2": "<->",
    "ip": "<#>",
    "cosine": "<=>",
    "l1": "<+>",
}

# ---------------------------------------------------------------------------
# index type -> operator class
# ---------------------------------------------------------------------------
_VECTOR_OPS: Dict[str, Dict[str, str]] = {
    "l2": {
        "hnsw": "vector_l2_ops",
        "ivfflat": "vector_l2_ops",
        "diskann": "vector_l2_ops",
    },
    "ip": {
        "hnsw": "vector_ip_ops",
        "ivfflat": "vector_ip_ops",
        "diskann": "vector_ip_ops",
    },
    "cosine": {
        "hnsw": "vector_cosine_ops",
        "ivfflat": "vector_cosine_ops",
        "diskann": "vector_cosine_ops",
    },
    "l1": {
        "hnsw": "vector_l1_ops",
    },
}


def _normalize_index_type(index_type: str | None) -> str:
    value = (index_type or "hnsw").strip().lower()
    try:
        return normalize_opengauss_index_type(value)
    except ValueError:
        if "diskann" in value:
            return "diskann"
        if "ivf" in value:
            return "ivfflat"
        if "hnsw" in value:
            return "hnsw"
        raise


def _index_access_method(index_type: str) -> str:
    return resolve_opengauss_index_spec(index_type)[0]


def _index_quantization(index_type: str) -> Optional[str]:
    return resolve_opengauss_index_spec(index_type)[1]


def _validate_identifier(identifier: str, *, kind: str = "SQL identifier") -> str:
    if not isinstance(identifier, str) or not _IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError(
            f"Invalid {kind}: {identifier!r}; use 1-63 ASCII letters, digits, or underscores"
        )
    return identifier


def _bounded_identifier(identifier: str, *, kind: str = "SQL identifier") -> str:
    """Fit a derived SQL name into NAMEDATALEN without silent truncation.

    Collection/field names stay strictly 1-63 via ``_validate_identifier``.
    Physical index and catalog table names are derived and can exceed 63
    even when every input is legal; hash the tail so CREATE INDEX cannot
    fail after the collection table already exists.
    """
    if not isinstance(identifier, str) or not _IDENTIFIER_CHAR_PATTERN.fullmatch(identifier):
        raise ValueError(
            f"Invalid {kind}: {identifier!r}; use ASCII letters, digits, or underscores"
        )
    if len(identifier) <= _MAX_SQL_IDENTIFIER:
        return identifier
    digest = hashlib.sha1(identifier.encode("utf-8")).hexdigest()[:_BOUNDED_IDENT_HASH_LEN]
    keep = _MAX_SQL_IDENTIFIER - 1 - len(digest)
    return f"{identifier[:keep]}_{digest}"


def _index_meta_table_name(collection_name: str) -> str:
    return _bounded_identifier(
        f"_ov_index_{collection_name}",
        kind="openGauss index metadata table",
    )


def _quote_identifier(identifier: str, *, kind: str = "SQL identifier") -> str:
    return f'"{_validate_identifier(identifier, kind=kind)}"'


def _escape_like_pattern(value: str) -> str:
    r"""Escape `\`, `%`, and `_` for LIKE ... ESCAPE '\'."""
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_scope_path(path: str) -> str:
    stripped = str(path).strip() or "/"
    if stripped != "/":
        stripped = stripped.rstrip("/") or "/"
    return stripped


def _parse_path_scope_depth(para: Any) -> Optional[int]:
    if not isinstance(para, str):
        return None
    match = _PATH_SCOPE_DEPTH_PATTERN.fullmatch(para)
    if not match:
        return None
    return int(match.group(1))


def _sql_normalized_path(quoted_field: str) -> str:
    """SQL equivalent of stripping and rstrip('/') for stored path values."""
    trimmed = f"btrim({quoted_field})"
    stripped = f"rtrim({trimmed}, '/')"
    return (
        f"(CASE WHEN {quoted_field} IS NULL THEN NULL "
        f"WHEN {trimmed} = '' OR {stripped} = '' THEN '/' "
        f"ELSE {stripped} END)"
    )


def _build_path_scope_clause(
    quoted_field: str, prefix: str, depth: int
) -> tuple[str, list]:
    """Translate PathScope to SQL using the official relative-depth contract.

    ``depth < 0`` is unbounded recursion. ``depth == 0`` is an exact match.
    Positive depth includes the node itself and descendants whose relative
    path has at most ``depth`` segments. Stored values are normalized the
    same way as the query prefix so trailing slashes do not change depth.
    """
    normalized = _normalize_scope_path(prefix)
    field_expr = _sql_normalized_path(quoted_field)
    if depth == 0:
        return f"{field_expr} = %s", [normalized]

    child_pattern = (
        "/%" if normalized == "/" else f"{_escape_like_pattern(normalized)}/%"
    )
    start_pos = 2 if normalized == "/" else len(normalized) + 2
    suffix = f"substring({field_expr} from {int(start_pos)})"
    relative_depth = (
        f"(char_length({suffix}) - char_length(replace({suffix}, '/', '')) + 1)"
    )
    if depth < 0:
        return (
            f"({field_expr} = %s OR {field_expr} LIKE %s ESCAPE '\\')",
            [normalized, child_pattern],
        )
    return (
        f"({field_expr} = %s OR "
        f"({field_expr} LIKE %s ESCAPE '\\' AND {relative_depth} <= %s))",
        [normalized, child_pattern, int(depth)],
    )


def _validate_vector(vector: Any, dimension: int, *, field_name: str = "vector") -> list[float]:
    if not isinstance(vector, (list, tuple)):
        raise ValueError(f"openGauss {field_name} must be a list or tuple")
    if dimension > 0 and len(vector) != dimension:
        raise ValueError(
            f"openGauss {field_name} dimension mismatch: expected {dimension}, got {len(vector)}"
        )
    normalized: list[float] = []
    for index, value in enumerate(vector):
        if isinstance(value, bool):
            raise ValueError(f"openGauss {field_name}[{index}] must be numeric")
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"openGauss {field_name}[{index}] must be numeric") from error
        if not math.isfinite(number):
            raise ValueError(f"openGauss {field_name}[{index}] must be finite")
        normalized.append(number)
    return normalized


def _coerce_vector(value: Any) -> Any:
    """Normalize psycopg2/DataVec vector values to ``list[float]``.

    Unregistered ``vector`` columns typically arrive as strings like
    ``'[1,2,3]'``. Official migration treats non-list payloads as missing.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        try:
            return [float(component) for component in value]
        except (TypeError, ValueError):
            return value
    if hasattr(value, "tolist"):
        try:
            coerced = value.tolist()
        except Exception:
            coerced = None
        if isinstance(coerced, (list, tuple)):
            try:
                return [float(component) for component in coerced]
            except (TypeError, ValueError):
                return value
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1].strip()
        if not text:
            return []
        try:
            return [float(part.strip()) for part in text.split(",") if part.strip()]
        except ValueError:
            return value
    return value


def _is_undefined_table_error(error: Exception) -> bool:
    """Return True when *error* is the driver's undefined-table error (SQLSTATE 42P01).

    Distinguishes the legitimate "catalog/metadata table not created yet" case
    from real backend failures, which must propagate to the caller instead of
    being silently converted into an empty/absent result.
    """
    sqlstate = getattr(error, "pgcode", None) or getattr(error, "sqlstate", None)
    return sqlstate == "42P01"


def _coerce_sparse_vector(value: Any) -> Any:
    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return value
        return parsed if isinstance(parsed, dict) else value
    return value


def _resolve_build_params(index_type: str, meta_data: Dict[str, Any]) -> Dict[str, Any]:
    nested = meta_data.get("build_params")
    params = dict(nested) if isinstance(nested, dict) else {}
    # Top-level keys remain supported for backward compatibility.
    for key in (
        "m",
        "ef_construction",
        "lists",
        "index_size",
        "enable_pq",
        "enable_rabitq",
        "pq_m",
        "pq_ksub",
        "by_residual",
        "rabitq_refine_type",
        "rabitq_fht",
        "parallel_workers",
    ):
        if key in meta_data and key not in params:
            params[key] = meta_data[key]
    return params


def _resolve_search_params(index_type: str, meta_data: Dict[str, Any]) -> Dict[str, Any]:
    nested = meta_data.get("search_params")
    params = dict(nested) if isinstance(nested, dict) else {}
    for key in (
        "ef_search",
        "probes",
        "hnsw_ef_search",
        "hnsw_earlystop_threshold",
        "earlystop_threshold",
        "ivfflat_probes",
        "ivfpq_kreorder",
        "diskann_probes",
        "rbq_query_bits",
        "rbq_refinek",
    ):
        if key in meta_data and key not in params:
            params[key] = meta_data[key]
    return params


class _PooledCursor:
    def __init__(self, connection_proxy: "_PooledConnectionProxy", connection, cursor):
        self._connection_proxy = connection_proxy
        self._connection = connection
        self._cursor = cursor
        self._closed = False

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._cursor.close()
        finally:
            self._connection_proxy.release(self._connection)


class _PooledConnectionProxy:
    """Compatibility connection backed by a psycopg2 threaded connection pool."""

    def __init__(self, pool):
        self._pool = pool
        self._local = threading.local()
        self._closed = False

    def _checkout(self):
        if self._closed:
            raise RuntimeError("openGauss connection pool is closed")
        connection = getattr(self._local, "connection", None)
        if connection is not None and getattr(connection, "closed", False):
            self._local.connection = None
            self._local.cursor_count = 0
            self._pool.putconn(connection, close=True)
            connection = None
        if connection is None:
            connection = self._pool.getconn()
            try:
                connection.autocommit = False
            except Exception:
                self._pool.putconn(connection, close=True)
                raise
            self._local.connection = connection
            self._local.cursor_count = 0
        return connection

    def cursor(self, *args, **kwargs):
        connection = self._checkout()
        self._local.cursor_count = getattr(self._local, "cursor_count", 0) + 1
        try:
            cursor = connection.cursor(*args, **kwargs)
        except Exception:
            self.release(connection)
            raise
        return _PooledCursor(self, connection, cursor)

    def commit(self) -> None:
        self._checkout().commit()

    def rollback(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None and not getattr(connection, "closed", False):
            connection.rollback()

    def release(self, connection) -> None:
        if connection is not getattr(self._local, "connection", None):
            return
        cursor_count = max(getattr(self._local, "cursor_count", 1) - 1, 0)
        self._local.cursor_count = cursor_count
        if cursor_count > 0:
            return
        self._local.connection = None
        close_connection = bool(getattr(connection, "closed", False))
        try:
            if not close_connection:
                connection.rollback()
        except Exception:
            close_connection = True
        self._pool.putconn(connection, close=close_connection)

    def close(self) -> None:
        if self._closed:
            return
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            self._local.connection = None
            self._local.cursor_count = 0
            self._pool.putconn(connection, close=True)
        self._pool.closeall()
        self._closed = True


def _create_connection_pool(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    db_name: str,
    min_size: int,
    max_size: int,
):
    psycopg2 = _import_psycopg2()
    return psycopg2.pool.ThreadedConnectionPool(
        minconn=min_size,
        maxconn=max_size,
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=db_name,
        options="-c client_encoding=UTF8 -c search_path=public",
        connect_timeout=10,
    )


def _import_psycopg2():
    """Import psycopg2 (openGauss connector)."""
    try:
        import psycopg2  # noqa: PLC0415
        import psycopg2.pool  # noqa: PLC0415

        return psycopg2
    except ImportError as e:
        raise ImportError(
            "psycopg2 is required for the openGauss backend. Install it via:\n"
            '  pip install "openviking[opengauss]"\n'
            "or install the openGauss-connector-python-psycopg2 package, "
            "which exposes the same psycopg2 module interface."
        ) from e


def _field_to_column_ddl(field: Dict[str, Any]) -> Optional[str]:
    """Convert an OpenViking field definition to a SQL column definition.

    Returns None for vector fields (handled separately) and
    for unrecognised types (skipped with a warning).
    """
    name = field.get("FieldName") or field.get("field_name") or field.get("name", "")
    ftype = field.get("FieldType") or field.get("field_type") or field.get("type", "string")

    if ftype == "vector":
        return None

    # Skip 'id' field as it's already defined as PRIMARY KEY in the table schema
    if name == "id":
        return None

    col_type = _FIELD_TYPE_MAP.get(ftype, "TEXT")
    quoted = _quote_identifier(name, kind="openGauss field name")
    return f"{quoted} {col_type}"


def _date_time_to_epoch_ms(value: Any) -> int:
    """Normalize OpenViking date_time values to epoch milliseconds."""
    if isinstance(value, bool):
        raise ValueError("date_time value cannot be boolean")
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        raise ValueError(f"date_time value must be string or number, got {type(value).__name__}")

    stripped = value.strip()
    if not stripped:
        raise ValueError("date_time value cannot be empty")
    try:
        return int(stripped)
    except ValueError:
        pass

    normalized = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _distance_to_similarity(distance_metric: str, distance_value: Any) -> float:
    """Convert a DataVec distance into OpenViking's higher-is-better score."""
    try:
        distance = float(distance_value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(distance):
        return 0.0

    if distance_metric == "cosine":
        return 1.0 - distance
    if distance_metric == "ip":
        return -distance
    return 1.0 / (1.0 + max(distance, 0.0))


def _build_where_clause(
    filters: Optional[Dict[str, Any]],
    array_fields: Optional[set[str]] = None,
) -> tuple[str, list]:
    """Recursively convert OpenViking filter DSL to a SQL WHERE clause.

    Returns (sql_fragment, params_list).
    """
    if not filters:
        return "", []

    op = filters.get("op", "")

    if op == "and":
        parts, params = [], []
        for cond in filters.get("conds", []):
            frag, p = _build_where_clause(cond, array_fields)
            if frag:
                parts.append(f"({frag})")
                params.extend(p)
        if not parts:
            return "", []
        return " AND ".join(parts), params

    if op == "or":
        parts, params = [], []
        for cond in filters.get("conds", []):
            frag, p = _build_where_clause(cond, array_fields)
            if frag:
                parts.append(f"({frag})")
                params.extend(p)
        if not parts:
            return "FALSE", []
        return " OR ".join(parts), params

    field = filters.get("field", "")
    quoted_field = _quote_identifier(field, kind="openGauss filter field")

    if op == "must":
        conds = filters.get("conds", [])
        depth = _parse_path_scope_depth(filters.get("para", ""))
        if depth is not None and len(conds) == 1:
            return _build_path_scope_clause(quoted_field, str(conds[0]), depth)
        if conds:
            if field in (array_fields or set()):
                comparisons = [f"%s = ANY({quoted_field})" for _ in conds]
                return " OR ".join(comparisons), list(conds)
            placeholders = ", ".join(["%s"] * len(conds))
            return f"{quoted_field} IN ({placeholders})", list(conds)
        # Empty In/Eq is a contradiction, not an unfiltered scan.
        return "FALSE", []

    if op == "must_not":
        conds = filters.get("conds", [])
        depth = _parse_path_scope_depth(filters.get("para", ""))
        if depth is not None and len(conds) == 1:
            clause, params = _build_path_scope_clause(quoted_field, str(conds[0]), depth)
            return f"NOT ({clause})", params
        if not conds:
            return "", []
        if field in (array_fields or set()):
            comparisons = [f"NOT (%s = ANY({quoted_field}))" for _ in conds]
            return " AND ".join(comparisons), list(conds)
        placeholders = ", ".join(["%s"] * len(conds))
        return f"{quoted_field} NOT IN ({placeholders})", list(conds)

    if op == "prefix":
        prefix = str(filters.get("prefix", ""))
        escaped_prefix = _escape_like_pattern(prefix)
        return f"{quoted_field} LIKE %s ESCAPE '\\'", [f"{escaped_prefix}%"]

    if op == "range":
        parts, params = [], []
        if "gte" in filters:
            parts.append(f"{quoted_field} >= %s")
            params.append(filters["gte"])
        if "gt" in filters:
            parts.append(f"{quoted_field} > %s")
            params.append(filters["gt"])
        if "lte" in filters:
            parts.append(f"{quoted_field} <= %s")
            params.append(filters["lte"])
        if "lt" in filters:
            parts.append(f"{quoted_field} < %s")
            params.append(filters["lt"])
        return " AND ".join(parts), params

    if op == "contains":
        raw_substring = filters.get("substring", "")
        substring = _escape_like_pattern("" if raw_substring is None else raw_substring)
        return f"{quoted_field} LIKE %s ESCAPE '\\'", [f"%{substring}%"]

    raise NotImplementedError(f"openGauss backend does not support filter op={op!r}")


# ---------------------------------------------------------------------------
# Dummy IIndex implementation (metadata only, no in-memory structure needed)
# ---------------------------------------------------------------------------
class _PgIndex(IIndex):
    """Dummy IIndex implementation for OpenGauss.

    This class provides metadata-only index operations. The actual vector search
    is performed directly via SQL queries in OpenGaussCollection.
    """
    def __init__(self, name: str, meta: Dict[str, Any]):
        self._name = name
        self._meta = meta

    def get_name(self) -> str:
        return self._name

    def get_meta_data(self) -> Dict[str, Any]:
        return dict(self._meta)

    def upsert_data(self, delta_list):
        """Not used - data operations handled by OpenGaussCollection."""
        pass

    def delete_data(self, delta_list):
        """Not used - data operations handled by OpenGaussCollection."""
        pass

    def search(self, query_vector=None, limit=10, filters=None, sparse_raw_terms=None, sparse_values=None):
        """Not used - search handled by OpenGaussCollection."""
        return [], []

    def aggregate(self, filters=None):
        """Not used - aggregation handled by OpenGaussCollection."""
        return {}

    def update(self, scalar_index=None, description=None):
        """Not used - updates handled by OpenGaussCollection."""
        pass

    def close(self):
        """No-op for dummy index."""
        pass

    def drop(self):
        """No-op for dummy index."""
        pass


# ---------------------------------------------------------------------------
# ICollection implementation backed by openGauss via psycopg2
# ---------------------------------------------------------------------------
class OpenGaussCollection(ICollection):
    """A single OpenViking collection stored in an openGauss/PostgreSQL table.

    Schema design:
      - One table per collection: ``{collection_name}``
      - Column ``id`` VARCHAR(256) PRIMARY KEY
      - One column per non-vector field
      - Column ``vector`` vector(dim) for dense vectors
      - Metadata persisted in ``_ov_collection_meta`` table
      - Index metadata persisted in ``_ov_index_{collection_name}`` table
    """

    def __init__(
        self,
        conn,
        collection_name: str,
        meta: Dict[str, Any],
        dim: int,
        distance: str = "cosine",
        distributed: bool = False,
    ):
        super().__init__()
        self._conn = conn
        self._name = _validate_identifier(
            collection_name, kind="openGauss collection name"
        )
        self._meta = meta
        self._dim = dim
        self._distance = distance
        self._distributed = distributed
        self._lock = threading.RLock()
        self._field_names = {
            _validate_identifier(
                field.get("FieldName")
                or field.get("field_name")
                or field.get("name", ""),
                kind="openGauss field name",
            )
            for field in meta.get("Fields", [])
        }
        self._field_names.add("id")
        if dim > 0:
            self._field_names.add("vector")
        self._array_fields = {
            field.get("FieldName") or field.get("field_name") or field.get("name", "")
            for field in meta.get("Fields", [])
            if (field.get("FieldType") or field.get("field_type") or field.get("type"))
            in {"list<string>", "list<int64>"}
        }
        self._date_time_fields = {
            field.get("FieldName") or field.get("field_name") or field.get("name", "")
            for field in meta.get("Fields", [])
            if (field.get("FieldType") or field.get("field_type") or field.get("type"))
            == "date_time"
        }
        # Verified physical indexes and requested indexes waiting for data.
        self._indexes: Dict[str, Dict[str, Any]] = {}
        self._pending_indexes: Dict[str, Dict[str, Any]] = {}
        self._bulk_ingest_depth = 0
        self._load_index_meta()
        self._reconcile_index_metadata()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cursor(self):
        return self._conn.cursor()

    def _execute(self, sql: str, params=None, fetch: bool = False):
        with self._lock:
            cur = self._cursor()
            try:
                cur.execute(sql, params)
                if fetch:
                    rows = cur.fetchall()
                    self._conn.commit()
                    return rows
                self._conn.commit()
                return cur
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def _index_catalog_table(self) -> str:
        return _index_meta_table_name(self._name)

    def _load_index_meta(self):
        """Load persisted index metadata from the database.

        Only the undefined-table error is tolerated (no index has been
        created for this collection yet); any other backend failure must
        surface instead of silently leaving the index registry empty, which
        could trigger a spurious index rebuild.
        """
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        try:
            rows = self._execute(
                f"SELECT index_name, meta_json FROM {idx_table}",
                fetch=True,
            )
        except Exception as error:
            if _is_undefined_table_error(error):
                return
            raise
        for row in rows:
            index_meta = json.loads(row[1])
            if index_meta.get("_state") == "pending":
                self._pending_indexes[row[0]] = index_meta
            else:
                self._indexes[row[0]] = index_meta

    def _ensure_index_meta_table(self):
        """Create the per-collection index metadata table if needed.

        In distributed mode the table follows CN capabilities: a reference table
        on Citus-compatible deployments or an spq hash-distributed metadata table.
        """
        idx_table = self._index_catalog_table()
        quoted_idx_table = _quote_identifier(
            idx_table, kind="openGauss index metadata table"
        )
        self._execute(
            f"""
            CREATE TABLE IF NOT EXISTS {quoted_idx_table} (
                index_name VARCHAR(256) PRIMARY KEY,
                meta_json  TEXT NOT NULL
            )
            """
        )
        if self._distributed:
            _try_make_metadata_table_distributed(
                self._conn, idx_table, "index_name"
            )

    def _save_index_meta(self, index_name: str, meta: Dict[str, Any]):
        self._ensure_index_meta_table()
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        meta_json = json.dumps(meta)
        # Use UPDATE -> INSERT for distributed compatibility
        with self._lock:
            cur = self._cursor()
            try:
                cur.execute(
                    f"UPDATE {idx_table} SET meta_json = %s WHERE index_name = %s",
                    (meta_json, index_name),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        f"INSERT INTO {idx_table} (index_name, meta_json) VALUES (%s, %s)",
                        (index_name, meta_json),
                    )
                self._conn.commit()
            except Exception as error:
                self._conn.rollback()
                if getattr(error, "pgcode", None) != "23505":
                    raise
                retry_cursor = self._cursor()
                try:
                    retry_cursor.execute(
                        f"UPDATE {idx_table} SET meta_json = %s WHERE index_name = %s",
                        (meta_json, index_name),
                    )
                    if retry_cursor.rowcount != 1:
                        raise RuntimeError(
                            f"openGauss index metadata race recovery failed for {index_name!r}"
                        ) from error
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                finally:
                    retry_cursor.close()
            finally:
                cur.close()

    def _delete_index_meta(self, index_name: str):
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        try:
            self._execute(
                f"DELETE FROM {idx_table} WHERE index_name = %s",
                (index_name,),
            )
        except Exception as error:
            # Nothing to delete when the metadata table was never created.
            if _is_undefined_table_error(error):
                return
            raise

    def _get_all_columns(self) -> List[str]:
        """Return all non-system column names of the collection table."""
        rows = self._execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
              AND table_schema = current_schema()
            ORDER BY ordinal_position
            """,
            (self._name,),
            fetch=True,
        )
        return [row[0] for row in rows] if rows else []

    def _get_column_types(self) -> Dict[str, str]:
        """Return column name to data type mapping."""
        rows = self._execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = %s
              AND table_schema = current_schema()
            """,
            (self._name,),
            fetch=True,
        )
        return {row[0]: row[1] for row in rows} if rows else {}

    def _normalize_filter_date_times(self, filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not filters:
            return filters
        normalized = dict(filters)
        conditions = normalized.get("conds")
        if normalized.get("op") in {"and", "or"} and isinstance(conditions, list):
            normalized["conds"] = [
                self._normalize_filter_date_times(condition) for condition in conditions
            ]
            return normalized

        field = normalized.get("field")
        if field not in self._date_time_fields:
            return normalized
        if isinstance(conditions, list):
            normalized["conds"] = [_date_time_to_epoch_ms(value) for value in conditions]
        for bound in ("gt", "gte", "lt", "lte"):
            if normalized.get(bound) is not None:
                normalized[bound] = _date_time_to_epoch_ms(normalized[bound])
        return normalized

    def _select_output_columns(self, output_fields: Optional[List[str]]) -> str:
        """Build the SELECT column list from output_fields."""
        if not output_fields:
            return "*"
        allowed_fields = self._field_names | _UNSTORED_FIELDS
        unknown_fields = sorted(set(output_fields) - allowed_fields)
        if unknown_fields:
            raise ValueError(f"Unknown openGauss output fields: {unknown_fields}")
        cols = [_quote_identifier("id")]
        for field in output_fields:
            if field == "id":
                continue
            quoted = _quote_identifier(field, kind="openGauss output field")
            if field in _UNSTORED_FIELDS:
                # Schema/metadata still lists ``content``; the physical table
                # does not. Project NULL so official URI rewrite/migration
                # output_fields do not become a missing-column SQL error.
                cols.append(f"NULL AS {quoted}")
            else:
                cols.append(quoted)
        return ", ".join(cols)

    def _row_to_dict(self, row, columns: List[str]) -> Dict[str, Any]:
        record = {}
        for column, value in zip(columns, row, strict=True):
            if column == "vector":
                record[column] = _coerce_vector(value)
            elif column == "sparse_vector":
                record[column] = _coerce_sparse_vector(value)
            else:
                record[column] = value
        return record

    # ------------------------------------------------------------------
    # ICollection: collection lifecycle
    # ------------------------------------------------------------------

    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        if fields:
            self._meta.update(fields)
        if description is not None:
            self._meta["Description"] = description
        # Persist updated meta
        self._execute(
            f"""
            UPDATE "{_META_TABLE}"
            SET meta_json = %s
            WHERE table_name = %s
            """,
            (json.dumps(self._meta), self._name),
        )

    def get_meta_data(self) -> Dict[str, Any]:
        return dict(self._meta)

    def close(self):
        pass  # Connection lifecycle managed by adapter

    def drop(self):
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        self._execute(f'DROP TABLE IF EXISTS "{self._name}" CASCADE')
        self._execute(f"DROP TABLE IF EXISTS {idx_table} CASCADE")
        self._execute(
            f'DELETE FROM "{_META_TABLE}" WHERE table_name = %s',
            (self._name,),
        )
        self._indexes.clear()

    # ------------------------------------------------------------------
    # ICollection: index management
    # ------------------------------------------------------------------

    def _physical_index_definition(self, physical_index_name: str) -> Optional[str]:
        rows = self._execute(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = %s
              AND indexname = %s
            """,
            (self._name, physical_index_name),
            fetch=True,
        )
        return rows[0][0] if rows else None

    def _table_has_rows(self) -> bool:
        rows = self._execute(
            f"SELECT 1 FROM {_quote_identifier(self._name)} LIMIT 1",
            fetch=True,
        )
        return bool(rows)

    @staticmethod
    def _index_requires_data(index_meta: Dict[str, Any]) -> bool:
        if index_meta["_pg_index_type"] != "hnsw":
            return True
        build_params = index_meta.get("build_params", {})
        return bool(
            index_meta.get("_quantization")
            or build_params.get("enable_pq")
            or build_params.get("enable_rabitq")
        )

    @staticmethod
    def _format_index_option(value: Any) -> str:
        if isinstance(value, bool):
            return "on" if value else "off"
        if isinstance(value, str):
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
        return str(int(value))

    def _normalized_index_meta(
        self, index_name: str, meta_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        vector_meta = dict(meta_data.get("VectorIndex") or {})
        index_type = _normalize_index_type(vector_meta.get("IndexType", "hnsw"))
        access_method = _index_access_method(index_type)
        quantization = _index_quantization(index_type)
        distance = vector_meta.get("Distance", self._distance)
        operator_class = _VECTOR_OPS.get(distance, {}).get(access_method)
        if not operator_class:
            raise ValueError(
                f"openGauss index_type={index_type!r} does not support distance={distance!r}"
            )

        build_params = _resolve_build_params(index_type, meta_data)
        if quantization == "pq":
            build_params["enable_pq"] = True
            build_params.pop("enable_rabitq", None)
        elif quantization == "rabitq":
            build_params["enable_rabitq"] = True
            build_params.pop("enable_pq", None)

        physical_index_name = _bounded_identifier(
            f"idx_{self._name}_{index_name}_vec",
            kind="openGauss physical index name",
        )
        full_meta = dict(meta_data)
        full_meta["IndexName"] = index_name
        full_meta["build_params"] = build_params
        full_meta["search_params"] = _resolve_search_params(index_type, meta_data)
        full_meta["maintenance_work_mem_mb"] = int(
            meta_data.get(
                "maintenance_work_mem_mb",
                getattr(self, "_maintenance_work_mem_mb", 64),
            )
        )
        full_meta["_pg_index_name"] = physical_index_name
        full_meta["_pg_index_type"] = access_method
        full_meta["_logical_index_type"] = index_type
        full_meta["_quantization"] = quantization
        full_meta["_operator_class"] = operator_class
        full_meta["_distance"] = distance
        vector_meta["IndexType"] = index_type
        vector_meta["Distance"] = distance
        full_meta["VectorIndex"] = vector_meta
        return full_meta

    def _index_options(self, index_meta: Dict[str, Any]) -> list[tuple[str, Any]]:
        access_method = index_meta["_pg_index_type"]
        build_params = index_meta["build_params"]
        if access_method == "hnsw":
            options = [
                ("m", build_params.get("m", 16)),
                ("ef_construction", build_params.get("ef_construction", 64)),
            ]
        elif access_method == "ivfflat":
            options = [("lists", build_params.get("lists", 100))]
        else:
            options = [("index_size", build_params.get("index_size", 100))]

        if build_params.get("enable_pq"):
            options.extend(
                [
                    ("enable_pq", True),
                    ("pq_m", build_params.get("pq_m", 8)),
                    ("pq_ksub", build_params.get("pq_ksub", 256)),
                ]
            )
            if access_method == "ivfflat":
                options.append(("by_residual", build_params.get("by_residual", False)))
        if build_params.get("enable_rabitq"):
            options.extend(
                [
                    ("enable_rabitq", True),
                    ("rabitq_refine_type", build_params.get("rabitq_refine_type", "none")),
                ]
            )
            if "rabitq_fht" in build_params:
                options.append(("rabitq_fht", build_params["rabitq_fht"]))
        return options

    def _create_index_sql(self, index_meta: Dict[str, Any]) -> str:
        option_sql = ", ".join(
            f"{name} = {self._format_index_option(value)}"
            for name, value in self._index_options(index_meta)
        )
        return (
            f"CREATE INDEX {_quote_identifier(index_meta['_pg_index_name'])} "
            f"ON {_quote_identifier(self._name)} "
            f"USING {index_meta['_pg_index_type']} "
            f"({_quote_identifier('vector')} {index_meta['_operator_class']}) "
            f"WITH ({option_sql})"
        )

    @staticmethod
    def _normalized_indexdef(indexdef: str) -> str:
        return " ".join(indexdef.lower().replace('"', "").replace("'", "").split())

    def _index_identity_matches(self, index_meta: Dict[str, Any], indexdef: str) -> bool:
        compact_definition = self._normalized_indexdef(indexdef).replace(" ", "")
        return (
            f"using{index_meta['_pg_index_type']}" in compact_definition
            and index_meta["_operator_class"].lower().replace(" ", "") in compact_definition
        )

    def _catalog_index_options(self, indexdef: str) -> Optional[Dict[str, str]]:
        match = re.search(r"\bwith\s*\((.*)\)", self._normalized_indexdef(indexdef))
        if not match:
            return None
        options: Dict[str, str] = {}
        for part in match.group(1).split(","):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            options[name.strip()] = value.strip()
        return options

    @staticmethod
    def _default_index_option_value(option_name: str) -> Any:
        return {
            "m": 16,
            "ef_construction": 64,
            "lists": 100,
            "index_size": 100,
            "pq_m": 8,
            "pq_ksub": 256,
            "by_residual": False,
            "rabitq_refine_type": "none",
        }.get(option_name)

    def _option_is_omittable_default(self, option_name: str, option_value: Any) -> bool:
        default = self._default_index_option_value(option_name)
        if default is None:
            return False
        return (
            self._format_index_option(option_value).strip("'").lower()
            == self._format_index_option(default).strip("'").lower()
        )

    def _index_definition_matches(self, index_meta: Dict[str, Any], indexdef: str) -> bool:
        if not self._index_identity_matches(index_meta, indexdef):
            return False
        catalog_options = self._catalog_index_options(indexdef) or {}
        expected_pairs = list(self._index_options(index_meta))
        expected_options = {
            option_name: self._format_index_option(option_value).strip("'").lower()
            for option_name, option_value in expected_pairs
        }
        for option_name, option_value in expected_pairs:
            actual = catalog_options.get(option_name)
            if actual is None:
                # Catalog may omit default WITH options; non-defaults must appear.
                if not self._option_is_omittable_default(option_name, option_value):
                    return False
                continue
            if actual != expected_options[option_name]:
                return False
        # Downgrades such as hnsw-pq -> hnsw leave enable_pq/pq_m in catalog.
        # Those extras must force a rebuild; one-way expected-only checks miss them.
        for option_name, actual in catalog_options.items():
            if option_name in expected_options:
                continue
            if option_name in _QUANTIZATION_OPTION_NAMES:
                if option_name in {"enable_pq", "enable_rabitq"} and actual in {
                    "off",
                    "false",
                    "0",
                }:
                    continue
                return False
            default = self._default_index_option_value(option_name)
            if default is None:
                return False
            if actual != self._format_index_option(default).strip("'").lower():
                return False
        return True

    def _create_scalar_indexes(self, index_name: str, index_meta: Dict[str, Any]) -> None:
        for scalar_field in index_meta.get("ScalarIndex", []):
            if scalar_field not in self._field_names:
                raise ValueError(f"Unknown openGauss scalar index field: {scalar_field!r}")
            scalar_index_name = _bounded_identifier(
                f"idx_{self._name}_{index_name}_{scalar_field}",
                kind="openGauss scalar index name",
            )
            self._execute(
                f"CREATE INDEX IF NOT EXISTS {_quote_identifier(scalar_index_name)} "
                f"ON {_quote_identifier(self._name)} ({_quote_identifier(scalar_field)})"
            )

    def _persist_index_meta_and_scalar_indexes(
        self,
        index_name: str,
        index_meta: Dict[str, Any],
    ) -> None:
        scalar_fields = list(index_meta.get("ScalarIndex", []))
        unknown_fields = sorted(set(scalar_fields) - self._field_names)
        if unknown_fields:
            raise ValueError(
                f"Unknown openGauss scalar index field(s): {unknown_fields}"
            )

        self._ensure_index_meta_table()
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        meta_json = json.dumps(index_meta)

        def execute_transaction(cursor, *, allow_insert: bool) -> None:
            for scalar_field in scalar_fields:
                scalar_index_name = _bounded_identifier(
                    f"idx_{self._name}_{index_name}_{scalar_field}",
                    kind="openGauss scalar index name",
                )
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS "
                    f"{_quote_identifier(scalar_index_name)} "
                    f"ON {_quote_identifier(self._name)} "
                    f"({_quote_identifier(scalar_field)})"
                )
            cursor.execute(
                f"UPDATE {idx_table} SET meta_json = %s WHERE index_name = %s",
                (meta_json, index_name),
            )
            if cursor.rowcount == 0:
                if not allow_insert:
                    raise RuntimeError(
                        f"openGauss index metadata race recovery failed for {index_name!r}"
                    )
                cursor.execute(
                    f"INSERT INTO {idx_table} (index_name, meta_json) VALUES (%s, %s)",
                    (index_name, meta_json),
                )

        with self._lock:
            cursor = self._cursor()
            try:
                execute_transaction(cursor, allow_insert=True)
                self._conn.commit()
            except Exception as error:
                self._conn.rollback()
                if getattr(error, "pgcode", None) != "23505":
                    raise
                retry_cursor = self._cursor()
                try:
                    execute_transaction(retry_cursor, allow_insert=False)
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                finally:
                    retry_cursor.close()
            finally:
                cursor.close()

    def _materialize_index(self, index_name: str, index_meta: Dict[str, Any]) -> None:
        index_meta = dict(index_meta)
        index_meta.pop("_state", None)
        physical_index_name = index_meta["_pg_index_name"]
        existing_definition = self._physical_index_definition(physical_index_name)
        if existing_definition and not self._index_definition_matches(
            index_meta, existing_definition
        ):
            self._execute(f"DROP INDEX {_quote_identifier(physical_index_name)}")
            existing_definition = None

        self._apply_parallel_workers(index_meta)
        if not existing_definition:
            maintenance_work_mem_mb = int(index_meta["maintenance_work_mem_mb"])
            with self._lock:
                cursor = self._cursor()
                try:
                    cursor.execute(
                        "SET LOCAL maintenance_work_mem = "
                        f"'{maintenance_work_mem_mb}MB'"
                    )
                    cursor.execute(self._create_index_sql(index_meta))
                    self._conn.commit()
                except Exception as error:
                    self._conn.rollback()
                    deployment_mode = "distributed" if self._distributed else "standalone"
                    raise RuntimeError(
                        "Failed to create openGauss ANN index "
                        f"{index_meta['_logical_index_type']!r} using "
                        f"{index_meta['_pg_index_type']!r} on collection {self._name!r} "
                        f"in {deployment_mode} mode with "
                        f"maintenance_work_mem={maintenance_work_mem_mb}MB"
                    ) from error
                finally:
                    cursor.close()

        verified_definition = self._physical_index_definition(physical_index_name)
        if not verified_definition or not self._index_definition_matches(
            index_meta, verified_definition
        ):
            raise RuntimeError(
                f"openGauss created index {physical_index_name!r} but catalog verification failed"
            )

        self._persist_index_meta_and_scalar_indexes(index_name, index_meta)
        self._indexes[index_name] = index_meta
        self._pending_indexes.pop(index_name, None)

    def _apply_parallel_workers(self, index_meta: Dict[str, Any]) -> None:
        parallel_workers = int(
            index_meta.get("build_params", {}).get("parallel_workers", 0) or 0
        )
        quoted_table = _quote_identifier(self._name)
        if parallel_workers > 0:
            self._execute(
                f"ALTER TABLE {quoted_table} SET (parallel_workers = {parallel_workers})"
            )
            return
        self._execute(f"ALTER TABLE {quoted_table} RESET (parallel_workers)")

    def _materialize_pending_indexes(self) -> None:
        if self._bulk_ingest_depth > 0 or not self._pending_indexes:
            return
        if not self._table_has_rows():
            return
        for index_name, index_meta in list(self._pending_indexes.items()):
            self._materialize_index(index_name, index_meta)

    def _materialize_pending_index(self, index_name: str) -> None:
        if self._bulk_ingest_depth > 0:
            return
        index_meta = self._pending_indexes.get(index_name)
        if index_meta is None or not self._table_has_rows():
            return
        self._materialize_index(index_name, index_meta)

    def _reconcile_index_metadata(self) -> None:
        for index_name, index_meta in list(self._indexes.items()):
            physical_index_name = index_meta.get("_pg_index_name")
            if not physical_index_name:
                self._delete_index_meta_and_scalar_indexes(index_name, index_meta)
                self._indexes.pop(index_name, None)
                continue
            index_definition = self._physical_index_definition(physical_index_name)
            try:
                normalized_meta = self._normalized_index_meta(index_name, index_meta)
            except Exception:
                self._delete_index_meta_and_scalar_indexes(index_name, index_meta)
                self._indexes.pop(index_name, None)
                continue
            if not index_definition or not self._index_definition_matches(
                normalized_meta, index_definition
            ):
                self._delete_index_meta_and_scalar_indexes(index_name, index_meta)
                self._indexes.pop(index_name, None)

    def _search_param_statements(self, index_name: str) -> list[str]:
        index_meta = self._indexes.get(index_name) or self._pending_indexes.get(index_name)
        if not index_meta:
            raise RuntimeError(f"openGauss index {index_name!r} is not configured")
        index_type = index_meta.get("_logical_index_type") or _normalize_index_type(
            index_meta.get("VectorIndex", {}).get("IndexType", "hnsw")
        )
        access_method = _index_access_method(index_type)
        params = _resolve_search_params(index_type, index_meta)
        statements: list[str] = []
        if access_method == "hnsw":
            ef_search = params.get("ef_search", params.get("hnsw_ef_search"))
            earlystop = params.get(
                "earlystop_threshold", params.get("hnsw_earlystop_threshold")
            )
            if ef_search is not None:
                statements.append(f"SET LOCAL hnsw_ef_search = {int(ef_search)}")
            if earlystop is not None:
                statements.append(
                    f"SET LOCAL hnsw_earlystop_threshold = {int(earlystop)}"
                )
        elif access_method == "ivfflat":
            probes = params.get("probes", params.get("ivfflat_probes"))
            if probes is not None:
                statements.append(f"SET LOCAL ivfflat_probes = {int(probes)}")
            if params.get("ivfpq_kreorder") is not None:
                statements.append(
                    f"SET LOCAL ivfpq_kreorder = {int(params['ivfpq_kreorder'])}"
                )
        else:
            probes = params.get("probes", params.get("diskann_probes"))
            if probes is not None:
                statements.append(f"SET LOCAL diskann_probes = {int(probes)}")
        if params.get("rbq_query_bits") is not None:
            statements.append(
                f"SET LOCAL rbq_query_bits = {int(params['rbq_query_bits'])}"
            )
        if params.get("rbq_refinek") is not None:
            statements.append(f"SET LOCAL rbq_refinek = {int(params['rbq_refinek'])}")
        if statements and self._distributed:
            # spq defaults to propagate_set_commands='none', so plain SET LOCAL
            # would only change the CN session while DN shard scans keep server
            # defaults. Propagation makes the parameters reach the DN scans.
            statements.insert(0, "SET LOCAL spq.propagate_set_commands = 'local'")
        return statements

    def _apply_search_params_on_cursor(self, cursor, index_name: str) -> None:
        for statement in self._search_param_statements(index_name):
            cursor.execute(statement)

    def create_index(self, index_name: str, meta_data: Dict[str, Any]) -> IIndex:
        _validate_identifier(index_name, kind="openGauss index name")
        vector_meta = dict(meta_data.get("VectorIndex") or {})
        resolved_build_params = _resolve_build_params(
            vector_meta.get("IndexType", "hnsw"),
            meta_data,
        )
        parallel_workers = resolved_build_params.pop("parallel_workers", None)
        if parallel_workers is not None and (
            type(parallel_workers) is not int or not 0 <= parallel_workers <= 32
        ):
            raise ValueError("openGauss parallel_workers must be an integer in [0, 32]")
        runtime_config = OpenGaussConfig(
            mode="distributed"
            if getattr(self, "_distributed", False)
            else "standalone",
            index_type=vector_meta.get("IndexType", "hnsw"),
            build_params=resolved_build_params,
            search_params=_resolve_search_params(
                vector_meta.get("IndexType", "hnsw"),
                meta_data,
            ),
            parallel_workers=parallel_workers if parallel_workers is not None else 0,
        )
        validated_meta = dict(meta_data)
        validated_meta["build_params"] = dict(runtime_config.build_params)
        if parallel_workers is not None:
            validated_meta["build_params"]["parallel_workers"] = (
                runtime_config.parallel_workers
            )
        validated_meta["search_params"] = dict(runtime_config.search_params)
        validated_vector_meta = dict(vector_meta)
        validated_vector_meta["IndexType"] = runtime_config.index_type
        validated_meta["VectorIndex"] = validated_vector_meta
        distance = validated_vector_meta.get("Distance", self._distance)
        effectively_quantized = bool(
            runtime_config.quantization
            or runtime_config.build_params.get("enable_pq")
            or runtime_config.build_params.get("enable_rabitq")
        )
        if distance == "l1" and (
            runtime_config.access_method != "hnsw" or effectively_quantized
        ):
            raise ValueError(
                "openGauss distance='l1' requires plain hnsw without PQ or RabitQ"
            )
        validate_opengauss_vector_constraints(
            index_type=runtime_config.index_type,
            build_params=runtime_config.build_params,
            dimension=self._dim,
        )
        index_meta = self._normalized_index_meta(index_name, validated_meta)
        if self._index_requires_data(index_meta) and not self._table_has_rows():
            existing_definition = self._physical_index_definition(
                index_meta["_pg_index_name"]
            )
            if existing_definition and not self._index_definition_matches(
                index_meta, existing_definition
            ):
                self._execute(
                    f"DROP INDEX IF EXISTS {_quote_identifier(index_meta['_pg_index_name'])}"
                )
            index_meta["_state"] = "pending"
            self._persist_index_meta_and_scalar_indexes(index_name, index_meta)
            self._pending_indexes[index_name] = index_meta
            self._indexes.pop(index_name, None)
            return _PgIndex(index_name, index_meta)
        self._materialize_index(index_name, index_meta)
        return _PgIndex(index_name, index_meta)

    def has_index(self, index_name: str) -> bool:
        index_meta = self._indexes.get(index_name)
        if index_meta is None:
            return False
        index_definition = self._physical_index_definition(index_meta["_pg_index_name"])
        return bool(
            index_definition
            and self._index_definition_matches(index_meta, index_definition)
        )

    def get_index(self, index_name: str) -> Optional[IIndex]:
        index_meta = self._indexes.get(index_name) or self._pending_indexes.get(index_name)
        return _PgIndex(index_name, index_meta) if index_meta else None

    def list_indexes(self) -> List[str]:
        return sorted(set(self._indexes) | set(self._pending_indexes))

    def drop_index(self, index_name: str):
        index_meta = self._indexes.get(index_name) or self._pending_indexes.get(index_name)
        if not index_meta:
            return
        physical_index_name = index_meta.get("_pg_index_name")
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        with self._lock:
            cursor = self._cursor()
            try:
                if physical_index_name:
                    cursor.execute(
                        f"DROP INDEX IF EXISTS {_quote_identifier(physical_index_name)}"
                    )
                for scalar_field in index_meta.get("ScalarIndex", []):
                    scalar_index_name = _bounded_identifier(
                        f"idx_{self._name}_{index_name}_{scalar_field}",
                        kind="openGauss scalar index name",
                    )
                    cursor.execute(
                        f"DROP INDEX IF EXISTS {_quote_identifier(scalar_index_name)}"
                    )
                cursor.execute(
                    f"DELETE FROM {idx_table} WHERE index_name = %s",
                    (index_name,),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()
        self._indexes.pop(index_name, None)
        self._pending_indexes.pop(index_name, None)

    def _delete_index_meta_and_scalar_indexes(
        self,
        index_name: str,
        index_meta: Dict[str, Any],
    ) -> None:
        self._ensure_index_meta_table()
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        with self._lock:
            cursor = self._cursor()
            try:
                for scalar_field in index_meta.get("ScalarIndex", []):
                    scalar_index_name = _bounded_identifier(
                        f"idx_{self._name}_{index_name}_{scalar_field}",
                        kind="openGauss scalar index name",
                    )
                    cursor.execute(
                        f"DROP INDEX IF EXISTS {_quote_identifier(scalar_index_name)}"
                    )
                cursor.execute(
                    f"DELETE FROM {idx_table} WHERE index_name = %s",
                    (index_name,),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[Union[List[str], Dict[str, Any]]] = None,
        description: Optional[str] = None,
    ):
        index_meta = self._indexes.get(index_name) or self._pending_indexes.get(index_name)
        if not index_meta:
            return
        if scalar_index is not None:
            if not isinstance(scalar_index, list) or not all(
                isinstance(field, str) for field in scalar_index
            ):
                raise ValueError("openGauss ScalarIndex must be a list of field names")
            new_scalar_fields = list(dict.fromkeys(scalar_index))
            unknown_fields = sorted(set(new_scalar_fields) - self._field_names)
            if unknown_fields:
                raise ValueError(
                    f"Unknown openGauss scalar index field(s): {unknown_fields}"
                )
        self._ensure_index_meta_table()
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        with self._lock:
            cursor = self._cursor()
            try:
                cursor.execute(
                    f"SELECT meta_json FROM {idx_table} "
                    "WHERE index_name = %s FOR UPDATE",
                    (index_name,),
                )
                metadata_row = cursor.fetchone()
                current_meta = (
                    json.loads(metadata_row[0]) if metadata_row else dict(index_meta)
                )
                updated_meta = dict(current_meta)
                old_scalar_fields = list(current_meta.get("ScalarIndex", []))
                new_scalar_fields = old_scalar_fields
                if scalar_index is not None:
                    new_scalar_fields = list(dict.fromkeys(scalar_index))
                    updated_meta["ScalarIndex"] = new_scalar_fields
                if description is not None:
                    updated_meta["Description"] = description

                removed_fields = sorted(
                    set(old_scalar_fields) - set(new_scalar_fields)
                )
                added_fields = sorted(set(new_scalar_fields) - set(old_scalar_fields))
                for scalar_field in removed_fields:
                    scalar_index_name = _bounded_identifier(
                        f"idx_{self._name}_{index_name}_{scalar_field}",
                        kind="openGauss scalar index name",
                    )
                    cursor.execute(
                        f"DROP INDEX IF EXISTS {_quote_identifier(scalar_index_name)}"
                    )
                for scalar_field in added_fields:
                    scalar_index_name = _bounded_identifier(
                        f"idx_{self._name}_{index_name}_{scalar_field}",
                        kind="openGauss scalar index name",
                    )
                    cursor.execute(
                        f"CREATE INDEX {_quote_identifier(scalar_index_name)} "
                        f"ON {_quote_identifier(self._name)} "
                        f"({_quote_identifier(scalar_field)})"
                    )
                cursor.execute(
                    f"UPDATE {idx_table} SET meta_json = %s WHERE index_name = %s",
                    (json.dumps(updated_meta), index_name),
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        f"INSERT INTO {idx_table} (index_name, meta_json) VALUES (%s, %s)",
                        (index_name, json.dumps(updated_meta)),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

        if index_name in self._indexes:
            self._indexes[index_name] = updated_meta
        else:
            self._pending_indexes[index_name] = updated_meta

    def get_index_meta_data(self, index_name: str) -> Dict[str, Any]:
        index_meta = self._indexes.get(index_name) or self._pending_indexes.get(index_name)
        return dict(index_meta or {})

    def begin_bulk_ingest(self) -> None:
        with self._lock:
            self._bulk_ingest_depth += 1

    def end_bulk_ingest(self) -> None:
        with self._lock:
            if self._bulk_ingest_depth <= 0:
                raise RuntimeError("openGauss end_bulk_ingest called without matching begin")
            self._bulk_ingest_depth -= 1
            if self._bulk_ingest_depth == 0:
                self._materialize_pending_indexes()

    # ------------------------------------------------------------------
    # ICollection: search
    # ------------------------------------------------------------------

    def _resolve_distance_and_op(self, index_name: str) -> tuple[str, str]:
        idx_meta = self._indexes.get(index_name) or self._pending_indexes.get(index_name, {})
        distance = idx_meta.get("_distance") or idx_meta.get(
            "Distance", self._distance
        )
        op = _DISTANCE_OP.get(distance, "<=>")
        return distance, op

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
        # Sparse support is checked before the dense emptiness short-circuit:
        # a pure sparse query must fail loudly instead of returning an empty
        # result set that looks like "no recall".
        if sparse_vector:
            raise NotImplementedError(
                "openGauss backend does not support sparse or hybrid vector search"
            )

        if not dense_vector:
            return SearchResult(data=[])

        self._materialize_pending_index(index_name)
        if index_name in self._pending_indexes and not self._table_has_rows():
            return SearchResult(data=[])
        if not self.has_index(index_name):
            raise RuntimeError(
                f"openGauss physical ANN index {index_name!r} is missing or invalid"
            )
        distance_metric, op = self._resolve_distance_and_op(index_name)
        select_cols = self._select_output_columns(output_fields)

        where_frag, where_params = _build_where_clause(self._normalize_filter_date_times(filters), self._array_fields)
        where_clause = f"WHERE {where_frag}" if where_frag else ""

        normalized_vector = _validate_vector(dense_vector, self._dim, field_name="query vector")
        vector_str = "[" + ",".join(str(value) for value in normalized_vector) + "]"
        sql = f"""
            SELECT {select_cols}, vector {op} %s::vector AS _distance
            FROM "{self._name}"
            {where_clause}
            ORDER BY _distance, id
            LIMIT %s OFFSET %s
        """
        params = [vector_str] + where_params + [limit, offset]

        try:
            with self._lock:
                cur = self._cursor()
                try:
                    self._apply_search_params_on_cursor(cur, index_name)
                    cur.execute(sql, params)
                    col_names = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                finally:
                    cur.close()
        except Exception as error:
            logger.error("opengauss_adapter: search_by_vector failed: %s", error)
            raise

        if not rows:
            return SearchResult(data=[])

        items = []
        for row in rows:
            record = self._row_to_dict(row, col_names)
            distance = record.pop("_distance", 0.0)
            record_id = record.pop("id", None)
            similarity = _distance_to_similarity(distance_metric, distance)
            items.append(SearchItemResult(id=record_id, fields=record, score=similarity))
        return SearchResult(data=items)

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
        select_cols = self._select_output_columns(output_fields)
        quoted_field = _quote_identifier(field, kind="openGauss scalar sort field")
        where_frag, where_params = _build_where_clause(self._normalize_filter_date_times(filters), self._array_fields)
        where_clause = f"WHERE {where_frag}" if where_frag else ""
        sort_dir = "DESC" if (order or "desc").lower() == "desc" else "ASC"

        sql = f"""
            SELECT {select_cols}, {quoted_field} AS _scalar_val
            FROM "{self._name}"
            {where_clause}
            ORDER BY {quoted_field} {sort_dir}, id {sort_dir}
            LIMIT %s OFFSET %s
        """
        params = where_params + [limit, offset]

        try:
            with self._lock:
                cur = self._cursor()
                try:
                    cur.execute(sql, params)
                    col_names = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                finally:
                    cur.close()
        except Exception as error:
            logger.error("opengauss_adapter: search_by_scalar failed: %s", error)
            raise

        items = []
        for row in rows:
            record = self._row_to_dict(row, col_names)
            score = record.pop("_scalar_val", 0.0)
            record_id = record.pop("id", None)
            try:
                score_float = float(score) if score is not None else 0.0
            except (TypeError, ValueError):
                score_float = 0.0
            items.append(SearchItemResult(id=record_id, fields=record, score=score_float))
        return SearchResult(data=items)

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        select_cols = self._select_output_columns(output_fields)
        where_frag, where_params = _build_where_clause(self._normalize_filter_date_times(filters), self._array_fields)
        where_clause = f"WHERE {where_frag}" if where_frag else ""

        sql = f"""
            SELECT {select_cols}
            FROM "{self._name}"
            {where_clause}
            ORDER BY RANDOM()
            LIMIT %s OFFSET %s
        """
        params = where_params + [limit, offset]

        try:
            with self._lock:
                cur = self._cursor()
                try:
                    cur.execute(sql, params)
                    col_names = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                finally:
                    cur.close()
        except Exception as error:
            logger.error("opengauss_adapter: search_by_random failed: %s", error)
            raise

        items = []
        for row in rows:
            record = self._row_to_dict(row, col_names)
            record_id = record.pop("id", None)
            items.append(SearchItemResult(id=record_id, fields=record, score=0.0))
        return SearchResult(data=items)

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
        raise NotImplementedError(
            "openGauss backend does not provide OpenViking keyword/full-text search"
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
        # Fetch the source vector and use it for similarity search. Backend
        # failures propagate; only a genuinely missing id yields empty data.
        try:
            rows = self._execute(
                f'SELECT vector FROM "{self._name}" WHERE id = %s',
                (str(id),),
                fetch=True,
            )
        except Exception as error:
            logger.error(
                "opengauss_adapter: search_by_id failed to fetch source vector: %s",
                error,
            )
            raise

        if not rows or rows[0][0] is None:
            return SearchResult(data=[])

        vec = _coerce_vector(rows[0][0])
        if not isinstance(vec, list) or not vec:
            return SearchResult(data=[])

        result = self.search_by_vector(
            index_name,
            dense_vector=vec,
            limit=limit + offset + 1,
            offset=0,
            filters=filters,
            output_fields=output_fields,
        )
        candidates = [item for item in result.data if str(item.id) != str(id)]
        result.data = candidates[offset : offset + limit]
        return result

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
        raise NotImplementedError(
            "openGauss backend requires upstream multimodal embedding before vector search"
        )

    # ------------------------------------------------------------------
    # ICollection: data operations
    # ------------------------------------------------------------------

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl: int = 0):
        if not data_list:
            return

        import uuid

        all_columns = set(self._get_all_columns())
        column_types = self._get_column_types()
        prepared_records: list[tuple[str, list[str], list[str], list[Any]]] = []

        for record_index, record in enumerate(data_list):
            record_id = record.get("id") or record.get("_id") or str(uuid.uuid4())
            unknown_fields = sorted(
                set(record) - {"id", "_id", "vector"} - all_columns - _UNSTORED_FIELDS
            )
            if unknown_fields:
                raise ValueError(
                    f"openGauss record at index {record_index} contains unknown fields: "
                    f"{unknown_fields}"
                )

            extra_fields = {
                field_name: field_value
                for field_name, field_value in record.items()
                if field_name not in {"id", "_id", "vector"} | _UNSTORED_FIELDS
            }
            column_names = ["id"]
            placeholders = ["%s"]
            values: list[Any] = [str(record_id)]

            for field_name, field_value in extra_fields.items():
                _validate_identifier(field_name, kind="openGauss field name")
                column_names.append(field_name)
                column_type = column_types.get(field_name, "").lower()
                if field_name in self._date_time_fields:
                    field_value = _date_time_to_epoch_ms(field_value)
                if column_type == "jsonb":
                    placeholders.append("%s::jsonb")
                    field_value = json.dumps(field_value, ensure_ascii=False)
                else:
                    placeholders.append("%s")
                values.append(field_value)

            vector_value = record.get("vector")
            if vector_value is not None:
                if "vector" not in all_columns:
                    raise ValueError("openGauss collection does not define a vector column")
                normalized_vector = _validate_vector(
                    vector_value,
                    self._dim,
                    field_name=f"record[{record_index}].vector",
                )
                column_names.append("vector")
                placeholders.append("%s::vector")
                values.append(
                    "[" + ",".join(str(value) for value in normalized_vector) + "]"
                )

            prepared_records.append(
                (str(record_id), column_names, placeholders, values)
            )

        table_identifier = _quote_identifier(
            self._name, kind="openGauss collection name"
        )
        with self._lock:
            cursor = self._cursor()
            try:
                for record_id, column_names, placeholders, values in prepared_records:
                    update_columns = [
                        column_name for column_name in column_names if column_name != "id"
                    ]
                    update_placeholders = [
                        placeholder
                        for column_name, placeholder in zip(
                            column_names, placeholders, strict=True
                        )
                        if column_name != "id"
                    ]
                    update_set = ", ".join(
                        f"{_quote_identifier(column_name)} = {placeholder}"
                        for column_name, placeholder in zip(
                            update_columns, update_placeholders, strict=True
                        )
                    )
                    update_values = [
                        value
                        for column_name, value in zip(column_names, values, strict=True)
                        if column_name != "id"
                    ] + [record_id]

                    updated = 0
                    if update_set:
                        cursor.execute(
                            f"UPDATE {table_identifier} SET {update_set} "
                            f"WHERE {_quote_identifier('id')} = %s",
                            update_values,
                        )
                        updated = cursor.rowcount
                    if updated == 0:
                        columns_sql = ", ".join(
                            _quote_identifier(column_name) for column_name in column_names
                        )
                        insert_placeholders = ", ".join(placeholders)
                        cursor.execute("SAVEPOINT ov_upsert_insert")
                        try:
                            cursor.execute(
                                f"INSERT INTO {table_identifier} ({columns_sql}) "
                                f"VALUES ({insert_placeholders})",
                                values,
                            )
                        except Exception as error:
                            cursor.execute("ROLLBACK TO SAVEPOINT ov_upsert_insert")
                            if getattr(error, "pgcode", None) != "23505":
                                raise
                            if update_set:
                                cursor.execute(
                                    f"UPDATE {table_identifier} SET {update_set} "
                                    f"WHERE {_quote_identifier('id')} = %s",
                                    update_values,
                                )
                                if cursor.rowcount != 1:
                                    raise RuntimeError(
                                        f"openGauss concurrent upsert recovery failed for {record_id!r}"
                                    ) from error
                        finally:
                            cursor.execute("RELEASE SAVEPOINT ov_upsert_insert")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()
        try:
            self._materialize_pending_indexes()
        except Exception:
            logger.exception(
                "opengauss_adapter: data committed but pending ANN index materialization failed"
            )

    def update_data(self, data_list: List[Dict[str, Any]]):
        """Update existing records while preserving unspecified fields."""
        if not data_list:
            return []
        for record in data_list:
            if "id" not in record:
                raise ValueError("primary key 'id' is required for update")

        all_columns = set(self._get_all_columns())
        column_types = self._get_column_types()
        prepared_updates: list[tuple[str, list[str], list[Any]]] = []

        for record_index, record in enumerate(data_list):
            primary_key = str(record["id"])
            unknown_fields = sorted(
                set(record) - {"id", "_id", "vector"} - all_columns - _UNSTORED_FIELDS
            )
            if unknown_fields:
                raise ValueError(
                    f"openGauss record at index {record_index} contains unknown fields: "
                    f"{unknown_fields}"
                )

            assignments: list[str] = []
            values: list[Any] = []
            for field_name, field_value in record.items():
                if field_name in {"id", "_id"} | _UNSTORED_FIELDS:
                    continue
                _validate_identifier(field_name, kind="openGauss field name")
                if field_name == "vector":
                    if "vector" not in all_columns:
                        raise ValueError("openGauss collection does not define a vector column")
                    normalized_vector = _validate_vector(
                        field_value,
                        self._dim,
                        field_name=f"record[{record_index}].vector",
                    )
                    assignments.append(f'{_quote_identifier(field_name)} = %s::vector')
                    values.append(
                        "[" + ",".join(str(value) for value in normalized_vector) + "]"
                    )
                    continue
                if field_name in self._date_time_fields:
                    field_value = _date_time_to_epoch_ms(field_value)
                if column_types.get(field_name, "").lower() == "jsonb":
                    assignments.append(f'{_quote_identifier(field_name)} = %s::jsonb')
                    field_value = json.dumps(field_value, ensure_ascii=False)
                else:
                    assignments.append(f'{_quote_identifier(field_name)} = %s')
                values.append(field_value)
            prepared_updates.append((primary_key, assignments, values))

        table_identifier = _quote_identifier(
            self._name,
            kind="openGauss collection name",
        )
        with self._lock:
            cursor = self._cursor()
            try:
                missing_ids: list[str] = []
                for primary_key, assignments, values in prepared_updates:
                    if assignments:
                        cursor.execute(
                            f"UPDATE {table_identifier} SET {', '.join(assignments)} "
                            f"WHERE {_quote_identifier('id')} = %s",
                            values + [primary_key],
                        )
                    else:
                        cursor.execute(
                            f"UPDATE {table_identifier} "
                            f"SET {_quote_identifier('id')} = {_quote_identifier('id')} "
                            f"WHERE {_quote_identifier('id')} = %s",
                            (primary_key,),
                        )
                    if cursor.rowcount == 0:
                        missing_ids.append(primary_key)
                if missing_ids:
                    raise ValueError(
                        f"record not found for primary key(s): {missing_ids}"
                    )
                self._conn.commit()
                return [primary_key for primary_key, _, _ in prepared_updates]
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

    def fetch_data(self, primary_keys: List[Any]) -> FetchDataInCollectionResult:
        if not primary_keys:
            return FetchDataInCollectionResult(items=[], ids_not_exist=[])

        str_keys = [str(k) for k in primary_keys]
        placeholders = ", ".join(["%s"] * len(str_keys))
        sql = f'SELECT * FROM "{self._name}" WHERE id IN ({placeholders})'

        try:
            with self._lock:
                cur = self._cursor()
                try:
                    cur.execute(sql, str_keys)
                    col_names = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                finally:
                    cur.close()
        except Exception as error:
            logger.error("opengauss_adapter: fetch_data failed: %s", error)
            raise

        found_ids = set()
        items = []
        for row in rows:
            record = self._row_to_dict(row, col_names)
            record_id = record.pop("id", None)
            found_ids.add(str(record_id))
            items.append(DataItem(id=record_id, fields=record))

        ids_not_exist = [k for k in str_keys if k not in found_ids]
        return FetchDataInCollectionResult(items=items, ids_not_exist=ids_not_exist)

    def delete_data(self, primary_keys: List[Any]):
        if not primary_keys:
            return
        str_keys = [str(k) for k in primary_keys]
        placeholders = ", ".join(["%s"] * len(str_keys))
        self._execute(
            f'DELETE FROM "{self._name}" WHERE id IN ({placeholders})',
            str_keys,
        )

    def delete_all_data(self):
        self._execute(f'TRUNCATE TABLE "{self._name}"')

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        cond: Optional[Dict[str, Any]] = None,
    ) -> AggregateResult:
        where_frag, where_params = _build_where_clause(self._normalize_filter_date_times(filters), self._array_fields)
        where_clause = f"WHERE {where_frag}" if where_frag else ""

        if op == "count":
            if field:
                quoted_field = _quote_identifier(field, kind="openGauss aggregate field")
                sql = f"""
                    SELECT {quoted_field}, COUNT(*) AS cnt
                    FROM "{self._name}"
                    {where_clause}
                    GROUP BY {quoted_field}
                """
                try:
                    rows = self._execute(sql, where_params, fetch=True)
                except Exception as error:
                    logger.error("opengauss_adapter: aggregate_data (grouped) failed: %s", error)
                    raise

                agg: Dict[str, Any] = {}
                for row in rows:
                    key, cnt = row[0], row[1]
                    if cond:
                        gt = cond.get("gt")
                        gte = cond.get("gte")
                        lt = cond.get("lt")
                        lte = cond.get("lte")
                        if gt is not None and cnt <= gt:
                            continue
                        if gte is not None and cnt < gte:
                            continue
                        if lt is not None and cnt >= lt:
                            continue
                        if lte is not None and cnt > lte:
                            continue
                    agg[str(key)] = cnt
                return AggregateResult(agg=agg, op=op, field=field)
            else:
                sql = f'SELECT COUNT(*) FROM "{self._name}" {where_clause}'
                try:
                    rows = self._execute(sql, where_params, fetch=True)
                    total = rows[0][0] if rows else 0
                except Exception as error:
                    logger.error("opengauss_adapter: aggregate_data (count) failed: %s", error)
                    raise
                return AggregateResult(agg={"_total": int(total)}, op=op, field=None)

        logger.warning("opengauss_adapter: unsupported aggregate op=%r", op)
        return AggregateResult(agg={"_total": 0}, op=op, field=field)


# ---------------------------------------------------------------------------
# Distributed table helpers (openGauss spqplugin_v2; reference tables are used
# only when a Citus-compatible CN explicitly provides create_reference_table)
# ---------------------------------------------------------------------------

def _validate_distributed_environment(conn) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_extension WHERE extname IN ('spq', 'spq_plugin_v2')
            ),
            EXISTS (
                SELECT 1 FROM pg_proc WHERE proname = 'create_distributed_table'
            ),
            EXISTS (
                SELECT 1 FROM pg_proc WHERE proname = 'create_reference_table'
            )
            """
        )
        extension_exists, distributed_function_exists, reference_function_exists = (
            cursor.fetchone()
        )
        if not extension_exists:
            raise RuntimeError(
                "openGauss distributed mode requires the spq extension on the CN node"
            )
        if not distributed_function_exists:
            raise RuntimeError(
                "openGauss distributed mode requires create_distributed_table on the CN node"
            )
        if not reference_function_exists:
            logger.info(
                "opengauss_adapter: CN has no create_reference_table; metadata tables "
                "will use spq hash distribution"
            )

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM pg_dist_node
            WHERE isactive = true
              AND (noderole IS NULL OR noderole = 'primary')
            """
        )
        worker_count = int(cursor.fetchone()[0])
        if worker_count < 1:
            raise RuntimeError(
                "openGauss distributed mode requires at least one active DN worker"
            )

        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname='run_command_on_all_nodes')"
        )
        if cursor.fetchone()[0]:
            cursor.execute(
                "SELECT nodeid, success, result "
                "FROM run_command_on_all_nodes('SELECT 1', true, false)"
            )
            failed_nodes = [
                (node_id, result)
                for node_id, success, result in cursor.fetchall()
                if not success
            ]
            if failed_nodes:
                raise RuntimeError(
                    f"openGauss distributed nodes are not reachable: {failed_nodes}"
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def _distributed_table_kind(conn, table_name: str) -> Optional[str]:
    _validate_identifier(table_name, kind="openGauss distributed table name")
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT CASE partmethod
                WHEN 'n' THEN 'reference'
                ELSE 'distributed'
            END
            FROM pg_dist_partition
            WHERE logicalrelid = %s::regclass
            """,
            (table_name,),
        )
        row = cursor.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def _verify_distributed_table_kind(conn, table_name: str, expected_kind: str) -> None:
    actual_kind = _distributed_table_kind(conn, table_name)
    if actual_kind != expected_kind:
        raise RuntimeError(
            f"openGauss table {table_name!r} expected {expected_kind} catalog state, "
            f"got {actual_kind or 'local'}"
        )


def _is_table_already_distributed(conn, table_name: str) -> bool:
    """Return True if *table_name* is registered in the distribution catalog.

    Checks ``pg_dist_partition`` populated by spqplugin_v2 or a Citus-compatible
    extension.
    The function returns False gracefully when the extension is not installed
    (undefined ``pg_dist_partition`` relation); any other backend failure
    propagates to the caller.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1 FROM pg_dist_partition
            WHERE logicalrelid = %s::regclass
            """,
            (table_name,),
        )
        row = cur.fetchone()
        conn.commit()
        return row is not None
    except Exception as error:
        conn.rollback()
        if _is_undefined_table_error(error):
            return False
        raise
    finally:
        cur.close()


def _try_make_distributed_table(
    conn,
    table_name: str,
    shard_count: int = 32,
    distribution_column: str = "id",
) -> None:
    """Convert *table_name* to a distributed table via ``create_distributed_table``.

    Distributes by *distribution_column* (hash partitioning) with the given
    *shard_count*, using the openGauss spqplugin_v2 positional signature
    ``create_distributed_table(name, column, 'hash', shard_count)``.
    This is a no-op if the table is already distributed.
    Failures are raised because distributed mode must not silently degrade
    to a standalone table.
    """
    _validate_identifier(table_name, kind="openGauss distributed table name")
    _validate_identifier(
        distribution_column, kind="openGauss distribution column name"
    )
    if _is_table_already_distributed(conn, table_name):
        _verify_distributed_table_kind(conn, table_name, "distributed")
        logger.info("opengauss_adapter: table '%s' is already distributed, skipping", table_name)
        return
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT create_distributed_table(%s, %s, 'hash', {int(shard_count)})",
            (table_name, distribution_column),
        )
        conn.commit()
        _verify_distributed_table_kind(conn, table_name, "distributed")
        logger.info(
            "opengauss_adapter: distributed table '%s' created with %d shards",
            table_name,
            shard_count,
        )
    except Exception as error:
        conn.rollback()
        raise RuntimeError(
            f"Failed to distribute openGauss table {table_name!r}; "
            "ensure spq_plugin_v2 is installed on the CN node"
        ) from error
    finally:
        cur.close()


def _try_make_metadata_table_distributed(
    conn,
    table_name: str,
    distribution_column: str,
    shard_count: int = 32,
) -> None:
    """Distribute metadata according to the capabilities of the connected CN.

    Citus-compatible deployments may expose ``create_reference_table``. The
    official openGauss spqplugin_v2 API exposes hash-distributed tables only,
    so metadata falls back to hash distribution by its primary key.
    """
    _validate_identifier(table_name, kind="openGauss metadata table name")
    _validate_identifier(
        distribution_column, kind="openGauss metadata distribution column"
    )
    if _is_table_already_distributed(conn, table_name):
        actual_kind = _distributed_table_kind(conn, table_name)
        if actual_kind not in {"distributed", "reference"}:
            raise RuntimeError(
                f"openGauss metadata table {table_name!r} has invalid catalog state"
            )
        return

    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname='create_reference_table')"
        )
        supports_reference_tables = bool(cursor.fetchone()[0])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()

    if supports_reference_tables:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT create_reference_table(%s)", (table_name,))
            conn.commit()
        except Exception as error:
            conn.rollback()
            raise RuntimeError(
                f"Failed to create openGauss reference metadata table {table_name!r}"
            ) from error
        finally:
            cursor.close()
        _verify_distributed_table_kind(conn, table_name, "reference")
        return

    _try_make_distributed_table(
        conn,
        table_name,
        shard_count=shard_count,
        distribution_column=distribution_column,
    )


# ---------------------------------------------------------------------------
# Helper: create collection table
# ---------------------------------------------------------------------------

def _create_collection_table(
    conn,
    name: str,
    meta: Dict[str, Any],
    dim: int,
    distributed: bool = False,
    shard_count: int = 32,
    on_table_created=None,
) -> None:
    """Create the collection data table and optionally distribute it.

    In distributed mode the table is converted to a distributed table keyed on
    ``id`` after creation.  The caller must have already created / ensured the
    metadata tables so their distributed form is initialized first.
    """
    fields: List[Dict[str, Any]] = meta.get("Fields", [])
    col_ddls = []
    has_vector = False

    for field in fields:
        ftype = field.get("FieldType") or field.get("field_type") or field.get("type", "string")
        if ftype == "vector":
            has_vector = True
            continue
        field_name = field.get("FieldName") or field.get("field_name") or field.get("name", "")
        if field_name == "content":
            continue
        ddl = _field_to_column_ddl(field)
        if ddl:
            col_ddls.append(ddl)

    if has_vector and dim > 0:
        col_ddls.append(f"vector vector({dim})")

    col_defs = ", ".join(col_ddls) if col_ddls else ""
    sep = ", " if col_defs else ""
    quoted_name = _quote_identifier(name, kind="openGauss collection name")
    sql = f"""
        CREATE TABLE {quoted_name} (
            id VARCHAR(256) PRIMARY KEY{sep}{col_defs}
        )
    """
    cur = conn.cursor()
    try:
        cur.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    if on_table_created is not None:
        on_table_created()

    if distributed:
        _try_make_distributed_table(conn, name, shard_count)


def _ensure_meta_table(conn, distributed: bool = False):
    """Create the global collection metadata table if it doesn't exist.

    In distributed mode the table becomes a reference table when supported;
    standard spqplugin_v2 uses hash distribution by ``table_name``.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{_META_TABLE}" (
                table_name VARCHAR(256) PRIMARY KEY,
                meta_json  TEXT NOT NULL
            )
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    if distributed:
        _try_make_metadata_table_distributed(
            conn, _META_TABLE, "table_name"
        )


def _save_collection_meta(conn, name: str, meta: Dict[str, Any], distributed: bool = False):
    _ensure_meta_table(conn, distributed=distributed)
    cur = conn.cursor()
    try:
        # Use UPDATE -> INSERT for distributed compatibility
        meta_json = json.dumps(meta)
        cur.execute(
            f'UPDATE "{_META_TABLE}" SET meta_json = %s WHERE table_name = %s',
            (meta_json, name),
        )
        if cur.rowcount == 0:
            cur.execute(
                f'INSERT INTO "{_META_TABLE}" (table_name, meta_json) VALUES (%s, %s)',
                (name, meta_json),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def _load_collection_meta(conn, name: str, distributed: bool = False) -> Optional[Dict[str, Any]]:
    _ensure_meta_table(conn, distributed=distributed)
    cur = conn.cursor()
    try:
        cur.execute(
            f'SELECT meta_json FROM "{_META_TABLE}" WHERE table_name = %s',
            (name,),
        )
        row = cur.fetchone()
        conn.commit()
    except Exception:
        # A backend failure (broken connection, missing SELECT privilege,
        # catalog corruption, ...) must never masquerade as "collection does
        # not exist": the caller could then enter the creation flow and
        # rewrite metadata for a table that actually exists.
        conn.rollback()
        raise
    finally:
        cur.close()

    return json.loads(row[0]) if row else None


# ---------------------------------------------------------------------------
# CollectionAdapter
# ---------------------------------------------------------------------------

class OpenGaussCollectionAdapter(CollectionAdapter):
    """OpenViking CollectionAdapter backed by openGauss DataVec.

    Supports two deployment modes controlled by ``opengauss.mode``:

    * **standalone** (default): connects to a single-node openGauss instance.
    * **distributed**: connects to the CN node of an openGauss cluster with
      spq_plugin_v2. Collection and metadata tables use verified distributed
      catalog entries; metadata uses reference tables only when the CN supports them.

    Index knobs follow the cuVS-style config shape:
    ``index_type`` + ``build_params`` + ``search_params``.
    """

    mode = "opengauss"
    USE_CONTENT_FIELD = False

    def __init__(
        self,
        collection_name: str,
        host: str,
        port: int,
        user: str,
        password: str,
        db_name: str,
        distributed: bool = False,
        shard_count: int = 32,
        index_name: str = "default",
        distance_metric: str = "cosine",
        index_type: str = "hnsw",
        build_params: Optional[Dict[str, Any]] = None,
        search_params: Optional[Dict[str, Any]] = None,
        parallel_workers: int = 0,
        maintenance_work_mem_mb: int = 64,
        connection_pool_min_size: int = 1,
        connection_pool_max_size: int = 8,
    ):
        super().__init__(
            collection_name=_validate_identifier(
                collection_name, kind="openGauss collection name"
            ),
            index_name=index_name,
        )
        validated_config = OpenGaussConfig(
            host=host,
            port=port,
            user=user,
            password=password,
            db_name=db_name,
            mode="distributed" if distributed else "standalone",
            shard_count=shard_count,
            index_type=index_type,
            build_params=dict(build_params or {}),
            search_params=dict(search_params or {}),
            parallel_workers=parallel_workers,
            maintenance_work_mem_mb=maintenance_work_mem_mb,
            connection_pool_min_size=connection_pool_min_size,
            connection_pool_max_size=connection_pool_max_size,
        )
        self._host = validated_config.host
        self._port = validated_config.port
        self._user = validated_config.user
        self._password = validated_config.password
        self._db_name = validated_config.db_name
        self._distributed = validated_config.is_distributed
        self._shard_count = validated_config.shard_count
        self._distance_metric = (distance_metric or "cosine").lower()
        self._index_type = validated_config.index_type
        self._build_params = dict(validated_config.build_params)
        if "parallel_workers" not in self._build_params:
            self._build_params["parallel_workers"] = validated_config.parallel_workers
        self._search_params = dict(validated_config.search_params)
        self._maintenance_work_mem_mb = validated_config.maintenance_work_mem_mb
        self._connection_pool_min_size = validated_config.connection_pool_min_size
        self._connection_pool_max_size = validated_config.connection_pool_max_size
        self._pool = None
        self._conn = None
        self._connect()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect(self):
        self._pool = _create_connection_pool(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            db_name=self._db_name,
            min_size=self._connection_pool_min_size,
            max_size=self._connection_pool_max_size,
        )
        self._conn = _PooledConnectionProxy(self._pool)
        try:
            if self._distributed:
                _validate_distributed_environment(self._conn)
            _ensure_meta_table(self._conn, distributed=self._distributed)
        except Exception:
            self._conn.close()
            self._conn = None
            self._pool = None
            raise
        logger.info(
            "opengauss_adapter: connected to %s:%s db=%s (distributed=%s)",
            self._host,
            self._port,
            self._db_name,
            self._distributed,
        )

    # ------------------------------------------------------------------
    # CollectionAdapter: required overrides
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: Any) -> "OpenGaussCollectionAdapter":
        og_cfg = getattr(config, "opengauss", None)
        if og_cfg is None:
            raise ValueError("VectorDB opengauss backend requires 'opengauss' config")

        collection_name = _validate_identifier(
            config.name or "context", kind="openGauss collection name"
        )
        if hasattr(og_cfg, "is_distributed"):
            distributed = og_cfg.is_distributed
        else:
            distributed = getattr(og_cfg, "distributed", False) or (
                getattr(og_cfg, "mode", "standalone") == "distributed"
            )

        # Official knobs live on OpenGaussConfig and are already validated.
        # custom_params is not an openGauss index-parameter entry point.
        build_params = dict(getattr(og_cfg, "build_params", None) or {})
        search_params = dict(getattr(og_cfg, "search_params", None) or {})
        index_type = getattr(og_cfg, "index_type", None) or "hnsw"
        return cls(
            collection_name=collection_name,
            host=og_cfg.host,
            port=og_cfg.port,
            user=og_cfg.user,
            password=og_cfg.password,
            db_name=og_cfg.db_name,
            distributed=distributed,
            shard_count=getattr(og_cfg, "shard_count", 32),
            index_name=getattr(config, "index_name", None) or "default",
            distance_metric=getattr(config, "distance_metric", None) or "cosine",
            index_type=index_type,
            build_params=build_params,
            search_params=search_params,
            parallel_workers=getattr(og_cfg, "parallel_workers", 0) or 0,
            maintenance_work_mem_mb=getattr(og_cfg, "maintenance_work_mem_mb", 64),
            connection_pool_min_size=getattr(og_cfg, "connection_pool_min_size", 1),
            connection_pool_max_size=getattr(og_cfg, "connection_pool_max_size", 8),
        )

    def _compile_filter(self, expr: FilterExpr | Dict[str, Any] | None) -> Dict[str, Any]:
        compiled = super()._compile_filter(expr)
        # Base compile collapses empty Or to ``{}``, which this backend treats
        # as an unfiltered scan. Vacuous OR is a contradiction; keep the DSL
        # so ``_build_where_clause`` emits FALSE (including nested And/Or).
        if isinstance(expr, Or) and not compiled:
            return dict(_EMPTY_OR_FILTER)
        return compiled

    def _table_exists(self, table_name: str) -> bool:
        """Return True if *table_name* exists in the current schema.

        Backend failures propagate to the caller: reporting "does not exist"
        on a transient error would let the creation flow reuse an existing
        physical table with freshly rewritten metadata.
        """
        try:
            cur = self._conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = %s AND table_schema = current_schema()
                    """,
                    (table_name,),
                )
                row = cur.fetchone()
                self._conn.commit()
                return row is not None
            finally:
                cur.close()
        except Exception:
            self._conn.rollback()
            raise

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return

        meta = _load_collection_meta(self._conn, self._collection_name, distributed=self._distributed)
        if meta is None:
            return

        # Check the actual table exists
        if not self._table_exists(self._collection_name):
            return

        dim = meta.get("_dim", 0)
        distance = (self._distance_metric or "cosine").lower()
        if meta.get("_distance") != distance:
            meta["_distance"] = distance
            _save_collection_meta(
                self._conn,
                self._collection_name,
                meta,
                distributed=self._distributed,
            )
        og_coll = OpenGaussCollection(
            self._conn,
            self._collection_name,
            meta,
            dim,
            distance,
            distributed=self._distributed,
        )
        og_coll._maintenance_work_mem_mb = self._maintenance_work_mem_mb
        self._collection = Collection(og_coll)

        # Auto-create vector index if missing
        self._ensure_vector_index_exists(og_coll, distance)

    def create_collection(
        self,
        name: str,
        schema: Dict[str, Any],
        *,
        distance: str,
        sparse_weight: float,
        index_name: str,
    ) -> bool:
        name = _validate_identifier(name, kind="openGauss collection name")
        if self._table_exists(name) and _load_collection_meta(
            self._conn,
            name,
            distributed=self._distributed,
        ) is None:
            raise RuntimeError(
                f"openGauss table {name!r} exists without collection metadata; "
                "refusing to adopt an unverified orphan table"
            )
        try:
            return super().create_collection(
                name,
                schema,
                distance=distance,
                sparse_weight=sparse_weight,
                index_name=index_name,
            )
        except Exception:
            partial_collection = self._collection
            self._collection = None
            if partial_collection is not None:
                try:
                    partial_collection.drop()
                except Exception:
                    logger.exception(
                        "opengauss_adapter: failed to clean up partially created collection %s",
                        name,
                    )
            raise

    def delete(
        self,
        *,
        ids: Optional[list[str]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 100000,
    ) -> int:
        if ids or filter is None:
            return super().delete(ids=ids, filter=filter, limit=limit)
        if limit <= 0:
            return 0

        collection = self.get_collection()
        compiled_filter = self._compile_filter(filter)
        collection_meta = collection.get_meta_data()
        array_fields = {
            field.get("FieldName") or field.get("field_name") or field.get("name", "")
            for field in collection_meta.get("Fields", [])
            if (field.get("FieldType") or field.get("field_type") or field.get("type"))
            in {"list<string>", "list<int64>"}
        }
        date_time_fields = {
            field.get("FieldName") or field.get("field_name") or field.get("name", "")
            for field in collection_meta.get("Fields", [])
            if (field.get("FieldType") or field.get("field_type") or field.get("type"))
            == "date_time"
        }

        def normalize_date_times(condition: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not condition:
                return condition
            normalized = dict(condition)
            conditions = normalized.get("conds")
            if normalized.get("op") in {"and", "or"} and isinstance(conditions, list):
                normalized["conds"] = [normalize_date_times(item) for item in conditions]
                return normalized
            if normalized.get("field") not in date_time_fields:
                return normalized
            if isinstance(conditions, list):
                normalized["conds"] = [_date_time_to_epoch_ms(value) for value in conditions]
            for bound in ("gt", "gte", "lt", "lte"):
                if normalized.get(bound) is not None:
                    normalized[bound] = _date_time_to_epoch_ms(normalized[bound])
            return normalized

        normalized_filter = normalize_date_times(compiled_filter)
        where_fragment, where_params = _build_where_clause(
            normalized_filter,
            array_fields,
        )
        if not where_fragment:
            return 0

        cursor = self._conn.cursor()
        try:
            cursor.execute(
                f'DELETE FROM "{self._collection_name}" '
                f'WHERE id IN ('
                f'SELECT id FROM "{self._collection_name}" '
                f'WHERE {where_fragment} ORDER BY id LIMIT %s'
                f')',
                where_params + [limit],
            )
            deleted_count = cursor.rowcount
            self._conn.commit()
            return deleted_count
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cursor.close()

    def update_data(self, data_list: List[Dict[str, Any]]) -> list[str]:
        normalized_records = [
            self._normalize_record_for_write(record) for record in data_list
        ]
        result = self.get_collection().update_data(normalized_records)
        return list(result or [])

    def _existing_scalar_index_fields(self, og_coll: "OpenGaussCollection") -> list[str]:
        for source in (
            getattr(og_coll, "_indexes", {}).get(self._index_name),
            getattr(og_coll, "_pending_indexes", {}).get(self._index_name),
            getattr(og_coll, "_meta", None),
        ):
            if not isinstance(source, dict):
                continue
            fields = source.get("ScalarIndex")
            if isinstance(fields, list) and fields:
                return [str(field) for field in fields]
        return []

    def _ensure_vector_index_exists(self, og_coll: "OpenGaussCollection", distance: str = "cosine"):
        """Ensure the configured vector index exists and is registered.

        Presence of an arbitrary ANN index is not enough: reconnect must
        recover the configured name, operator class, and build options, and
        persist metadata when the physical index survived a failed catalog
        write. A matching physical index is registered in place; only a
        missing or conflicting index goes through create_index().
        """
        requested_meta = self._build_default_index_meta(
            index_name=self._index_name,
            distance=distance,
            use_sparse=False,
            sparse_weight=0.0,
            scalar_index_fields=self._existing_scalar_index_fields(og_coll),
        )
        normalized_meta = og_coll._normalized_index_meta(self._index_name, requested_meta)
        existing_definition = og_coll._physical_index_definition(
            normalized_meta["_pg_index_name"]
        )
        if existing_definition and og_coll._index_definition_matches(
            normalized_meta, existing_definition
        ):
            og_coll._apply_parallel_workers(normalized_meta)
            og_coll._persist_index_meta_and_scalar_indexes(
                self._index_name,
                normalized_meta,
            )
            og_coll._indexes[self._index_name] = normalized_meta
            og_coll._pending_indexes.pop(self._index_name, None)
            logger.info(
                "opengauss_adapter: recovered configured vector index %s",
                normalized_meta["_pg_index_name"],
            )
            return
        og_coll.create_index(self._index_name, requested_meta)
        logger.info("opengauss_adapter: vector index ensured via create_index")

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
                "openGauss backend does not support sparse or hybrid vector indexes"
            )
        index_meta: Dict[str, Any] = {
            "IndexName": index_name,
            "VectorIndex": {
                "IndexType": self._index_type,
                "Distance": distance or self._distance_metric,
            },
            "ScalarIndex": scalar_index_fields,
            "build_params": dict(self._build_params),
            "search_params": dict(self._search_params),
            "maintenance_work_mem_mb": self._maintenance_work_mem_mb,
        }
        # Keep top-level aliases for create_index consumers.
        index_meta.update(self._build_params)
        index_meta.update(self._search_params)
        return index_meta

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        fields = meta.get("Fields", [])
        dim = 0
        distance = self._distance_metric or "cosine"
        for f in fields:
            ftype = f.get("FieldType") or f.get("field_type") or f.get("type", "")
            if ftype == "vector":
                # Schema may use "Dim", "dimension", or "dim"
                dim = (
                    f.get("Dimension")
                    or f.get("dimension")
                    or f.get("Dim")
                    or f.get("dim", 0)
                )
        access_method = _index_access_method(self._index_type)
        if not _VECTOR_OPS.get(distance, {}).get(access_method):
            raise ValueError(
                f"openGauss index_type={self._index_type!r} does not support "
                f"distance={distance!r}"
            )
        if distance == "l1" and (
            _index_quantization(self._index_type) is not None
            or self._build_params.get("enable_pq")
            or self._build_params.get("enable_rabitq")
        ):
            raise ValueError(
                "openGauss distance='l1' requires plain hnsw without PQ or RabitQ"
            )
        if dim:
            validate_opengauss_vector_constraints(
                index_type=self._index_type,
                build_params=self._build_params,
                dimension=int(dim),
            )
        meta["_dim"] = dim
        meta["_distance"] = distance
        meta["_index_type"] = self._index_type
        self._collection_name = _validate_identifier(
            self._collection_name, kind="openGauss collection name"
        )

        table_created = False

        def mark_table_created() -> None:
            nonlocal table_created
            table_created = True

        try:
            _create_collection_table(
                self._conn,
                self._collection_name,
                meta,
                dim,
                distributed=self._distributed,
                shard_count=self._shard_count,
                on_table_created=mark_table_created,
            )
            _save_collection_meta(
                self._conn,
                self._collection_name,
                meta,
                distributed=self._distributed,
            )
        except Exception:
            if not table_created:
                raise
            quoted_collection_name = _quote_identifier(
                self._collection_name,
                kind="openGauss collection name",
            )
            try:
                cursor = self._conn.cursor()
                try:
                    cursor.execute(f"DROP TABLE IF EXISTS {quoted_collection_name} CASCADE")
                    cursor.execute(
                        f'DELETE FROM "{_META_TABLE}" WHERE table_name = %s',
                        (self._collection_name,),
                    )
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    logger.exception(
                        "opengauss_adapter: failed to clean up orphan collection %s",
                        self._collection_name,
                    )
                finally:
                    cursor.close()
            finally:
                raise

        og_coll = OpenGaussCollection(
            self._conn,
            self._collection_name,
            meta,
            dim,
            distance,
            distributed=self._distributed,
        )
        og_coll._maintenance_work_mem_mb = self._maintenance_work_mem_mb
        return Collection(og_coll)

    def begin_bulk_ingest(self) -> None:
        self.get_collection().begin_bulk_ingest()

    def end_bulk_ingest(self) -> None:
        self.get_collection().end_bulk_ingest()

    def close(self) -> None:
        super().close()
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
