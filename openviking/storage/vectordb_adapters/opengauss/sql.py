# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQL identifiers, value conversion and filter compilation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from openviking_cli.utils import get_logger
from openviking_cli.utils.config.vectordb_config import (
    normalize_opengauss_index_type,
    resolve_opengauss_index_spec,
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


_META_TABLE = "_ov_collection_meta"


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


_DISTANCE_OP: Dict[str, str] = {
    "l2": "<->",
    "ip": "<#>",
    "cosine": "<=>",
    "l1": "<+>",
}


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
    return normalize_opengauss_index_type(index_type)


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


def _build_path_scope_clause(quoted_field: str, prefix: str, depth: int) -> tuple[str, list]:
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

    child_pattern = "/%" if normalized == "/" else f"{_escape_like_pattern(normalized)}/%"
    start_pos = 2 if normalized == "/" else len(normalized) + 2
    suffix = f"substring({field_expr} from {int(start_pos)})"
    relative_depth = f"(char_length({suffix}) - char_length(replace({suffix}, '/', '')) + 1)"
    if depth < 0:
        return (
            f"({field_expr} = %s OR {field_expr} LIKE %s ESCAPE '\\')",
            [normalized, child_pattern],
        )
    return (
        f"({field_expr} = %s OR ({field_expr} LIKE %s ESCAPE '\\' AND {relative_depth} <= %s))",
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
    params = meta_data.get("build_params", {})
    if not isinstance(params, dict):
        raise ValueError("openGauss build_params must be an object")
    return dict(params)


def _resolve_search_params(index_type: str, meta_data: Dict[str, Any]) -> Dict[str, Any]:
    params = meta_data.get("search_params", {})
    if not isinstance(params, dict):
        raise ValueError("openGauss search_params must be an object")
    return dict(params)


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
