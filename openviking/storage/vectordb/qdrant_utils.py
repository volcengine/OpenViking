# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Pure Qdrant/OpenViking conversion helpers."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

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

_OPENVIKING_QDRANT_ID_NAMESPACE = uuid.UUID("4b6bb5a8-7f1f-5b1a-9d4c-b93f29b1d67c")
_URI_FIELDS = {"uri", "parent_uri"}


def _normalize_path(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.startswith("viking://"):
        stripped = stripped[len("viking://") :]
    if not stripped:
        return "/"
    normalized = "/" + stripped.lstrip("/")
    return normalized.rstrip("/") or "/"


def _path_depth(path: str) -> int:
    return len([part for part in path.split("/") if part])


def _scope_roots(path: str) -> list[str]:
    parts = [part for part in path.split("/") if part]
    roots = ["/"]
    for index in range(1, len(parts) + 1):
        roots.append("/" + "/".join(parts[:index]))
    return roots


def build_qdrant_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize an OpenViking record into Qdrant payload fields."""
    payload = dict(record)
    original_id = payload.pop("id", None)
    payload.pop("vector", None)
    payload.pop("sparse_vector", None)
    if original_id is not None:
        payload["_openviking_original_id"] = str(original_id)

    for field in _URI_FIELDS:
        value = payload.get(field)
        if isinstance(value, str):
            payload[field] = _normalize_path(value)

    uri = payload.get("uri")
    if isinstance(uri, str):
        payload["uri_depth"] = _path_depth(uri)
        payload["scope_roots"] = _scope_roots(uri)
    return payload


def to_qdrant_point_id(value: Any) -> str:
    """Return a stable UUID point ID for an arbitrary OpenViking ID."""
    return str(uuid.uuid5(_OPENVIKING_QDRANT_ID_NAMESPACE, str(value)))


def _value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _match(field: str, value: Any) -> dict[str, Any]:
    return {"key": field, "match": {"value": _value(value)}}


def _match_any(field: str, values: list[Any]) -> dict[str, Any]:
    return {"key": field, "match": {"any": [_value(value) for value in values]}}


def _legacy_depth(payload: dict[str, Any]) -> int | None:
    marker = payload.get("para")
    if not isinstance(marker, str) or not marker.startswith("-d="):
        return None
    try:
        return int(marker[3:])
    except ValueError:
        raise ValueError(f"Invalid legacy path depth: {marker!r}") from None


def _compile_legacy(payload: dict[str, Any]) -> dict[str, Any]:
    op = str(payload.get("op") or "").lower()
    if op == "and":
        return _compile(And([item for item in payload.get("conds", []) if item]))
    if op == "or":
        return _compile(Or([item for item in payload.get("conds", []) if item]))
    if op in {"must", "must_not"}:
        field = payload.get("field")
        values = list(payload.get("conds") or [])
        if not isinstance(field, str) or not values:
            return {}
        values = [_normalize_path(value) if field in _URI_FIELDS else _value(value) for value in values]
        depth = _legacy_depth(payload)
        if depth is not None and field in _URI_FIELDS:
            return _compile(PathScope(field, values[0], depth))
        condition = _match(field, values[0]) if len(values) == 1 else _match_any(field, values)
        return {op: [condition]}
    if op in {"range", "time_range"}:
        field = payload.get("field")
        bounds = {
            key: _value(payload[key])
            for key in ("gte", "gt", "lte", "lt")
            if payload.get(key) is not None
        }
        return {"must": [{"key": field, "range": bounds}]}
    if op == "range_out":
        field = payload.get("field")
        branches = []
        if payload.get("gte") is not None:
            branches.append({"must": [{"key": field, "range": {"lt": _value(payload["gte"])}}]})
        if payload.get("lte") is not None:
            branches.append({"must": [{"key": field, "range": {"gt": _value(payload["lte"])}}]})
        return _compile(Or(branches))
    if op == "prefix":
        field = payload.get("field")
        prefix = payload.get("prefix", "")
        if field in _URI_FIELDS:
            return _compile(PathScope(field, prefix, depth=-1))
        raise NotImplementedError("Qdrant adapter only supports prefix filters for URI fields")
    if op == "contains":
        raise NotImplementedError("Contains is not supported by the Qdrant adapter")
    if op:
        raise NotImplementedError(f"Unsupported legacy Qdrant filter operation: {op}")
    return payload


def _compile(expr: FilterExpr | dict[str, Any]) -> dict[str, Any]:
    if isinstance(expr, dict):
        if "op" in expr:
            return _compile_legacy(expr)
        return expr
    if isinstance(expr, RawDSL):
        return _compile(expr.payload)
    if isinstance(expr, And):
        clauses = [_compile(item) for item in expr.conds if item is not None]
        clauses = [item for item in clauses if item]
        if not clauses:
            return {}
        if len(clauses) == 1:
            return clauses[0]
        simple_keys = {"must", "must_not"}
        complex_count = 0
        for item in clauses:
            keys = [key for key, value in item.items() if value]
            if not (
                len(keys) == 1
                and keys[0] in simple_keys
                and isinstance(item[keys[0]], list)
            ):
                complex_count += 1

        result: dict[str, Any] = {}
        for item in clauses:
            keys = [key for key, value in item.items() if value]
            if (
                len(keys) == 1
                and keys[0] in simple_keys
                and isinstance(item[keys[0]], list)
            ):
                result.setdefault(keys[0], []).extend(item[keys[0]])
                continue
            if complex_count == 1:
                for key, value in item.items():
                    if not value:
                        continue
                    if key in simple_keys and isinstance(value, list):
                        result.setdefault(key, []).extend(value)
                    else:
                        result[key] = value
            else:
                result.setdefault("must", []).append(item)
        return result
    if isinstance(expr, Or):
        clauses = [_compile(item) for item in expr.conds if item is not None]
        clauses = [item for item in clauses if item]
        if not clauses:
            return {}
        if len(clauses) == 1:
            return clauses[0]
        conditions: list[Any] = []
        for item in clauses:
            keys = [key for key, value in item.items() if value]
            if (
                len(keys) == 1
                and keys[0] in {"must", "should"}
                and isinstance(item[keys[0]], list)
                and len(item[keys[0]]) == 1
            ):
                conditions.append(item[keys[0]][0])
            else:
                conditions.append(item)
        return {"should": conditions}
    if isinstance(expr, Eq):
        field = expr.field
        value = _normalize_path(expr.value) if field in _URI_FIELDS else expr.value
        return {"must": [_match(field, value)]}
    if isinstance(expr, In):
        field = expr.field
        values = [
            _normalize_path(value) if field in _URI_FIELDS else value for value in expr.values
        ]
        if len(values) == 1:
            return {"must": [_match(field, values[0])]}
        return {"must": [_match_any(field, values)]}
    if isinstance(expr, (Range, TimeRange)):
        payload: dict[str, Any] = {}
        if isinstance(expr, Range):
            for key in ("gte", "gt", "lte", "lt"):
                value = getattr(expr, key)
                if value is not None:
                    payload[key] = _value(value)
        else:
            if expr.start is not None:
                payload["gte"] = _value(expr.start)
            if expr.end is not None:
                payload["lt"] = _value(expr.end)
        return {"must": [{"key": expr.field, "range": payload}]}
    if isinstance(expr, PathScope):
        if expr.field in _URI_FIELDS and not isinstance(expr.path, str):
            raise ValueError("Qdrant URI path scope requires a string URI path")
        if expr.field != "uri":
            raise NotImplementedError(
                "Qdrant PathScope supports uri only; the field has no scope payload"
            )
        path = _normalize_path(expr.path) if expr.field in _URI_FIELDS else expr.path
        if expr.depth == 0:
            return {"must": [_match(expr.field, path)]}
        scope_match = _match("scope_roots", path)
        if expr.depth < 0:
            return {"must": [scope_match]}
        return {
            "must": [
                scope_match,
                {
                    "key": "uri_depth",
                    "range": {"lte": _path_depth(path) + expr.depth},
                },
            ]
        }
    if isinstance(expr, Contains):
        raise NotImplementedError("Contains is not supported by the Qdrant adapter")
    raise TypeError(f"Unsupported filter expression: {type(expr)!r}")


def compile_qdrant_filter(
    expr: FilterExpr | dict[str, Any] | None,
) -> dict[str, Any]:
    """Compile OpenViking's filter AST into Qdrant's filter JSON."""
    if expr is None:
        return {}
    return _compile(expr)
