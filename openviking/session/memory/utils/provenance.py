"""Helpers for persisting auditable memory extraction provenance."""

from __future__ import annotations

import json
from typing import Any, Iterable

from openviking.session.memory.dataclass import MemoryOperationSource, ResolvedOperation

_SOURCE_FIELDS = (
    "extraction_id",
    "session_id",
    "message_ids",
    "archive_uri",
    "task_id",
    "trace_id",
    "extracted_at",
)


def provenance_sources_for_operation(op: ResolvedOperation) -> list[dict[str, Any]]:
    """Return normalized source records carried by an operation."""
    fields = dict(getattr(op, "memory_fields", {}) or {})
    return _collect_sources(fields.get("provenance"), getattr(op, "source", None))


def merge_memory_provenance(*values: Any) -> dict[str, list[dict[str, Any]]] | None:
    """Merge provenance values while preserving source order and message coverage."""
    sources = _collect_sources(*values)
    if not sources:
        return None

    merged: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for source in sources:
        key = _source_key(source)
        if key not in positions:
            positions[key] = len(merged)
            merged.append(source)
            continue
        index = positions[key]
        merged[index] = _merge_source_records(merged[index], source)
    return {"sources": merged}


def _collect_sources(*values: Any) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, MemoryOperationSource):
            normalized = _normalize_source(value.model_dump(exclude_none=True))
            if normalized:
                collected.append(normalized)
            continue
        if isinstance(value, dict):
            nested = value.get("sources")
            if isinstance(nested, list):
                collected.extend(_normalize_many(nested))
            else:
                normalized = _normalize_source(value)
                if normalized:
                    collected.append(normalized)
            continue
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            collected.extend(_normalize_many(value))
    return collected


def _normalize_many(values: Iterable[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, MemoryOperationSource):
            value = value.model_dump(exclude_none=True)
        if isinstance(value, dict):
            source = _normalize_source(value)
            if source:
                normalized.append(source)
    return normalized


def _normalize_source(value: dict[str, Any]) -> dict[str, Any]:
    source: dict[str, Any] = {}
    for field in _SOURCE_FIELDS:
        raw = value.get(field)
        if field == "message_ids":
            message_ids = _normalize_message_ids(raw)
            if message_ids:
                source[field] = message_ids
        elif raw is not None and str(raw).strip():
            source[field] = str(raw)
    return source


def _normalize_message_ids(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return list(dict.fromkeys(str(item) for item in values if item is not None and str(item)))


def _source_key(source: dict[str, Any]) -> str:
    extraction_id = source.get("extraction_id")
    if extraction_id:
        return f"extraction:{extraction_id}"
    return json.dumps(source, sort_keys=True, ensure_ascii=False)


def _merge_source_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "message_ids":
            merged[key] = list(
                dict.fromkeys(
                    _normalize_message_ids(existing.get(key)) + _normalize_message_ids(value)
                )
            )
        elif value is not None and str(value).strip():
            merged[key] = value
    return merged
