# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Validation and resource limits for Privacy identifiers and payloads."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping, Sequence

from openviking_cli.exceptions import InvalidArgumentError

PRIVACY_IDENTIFIER_MAX_CHARS = 255
PRIVACY_FIELD_NAME_MAX_BYTES = 255
PRIVACY_MAX_FIELDS = 128
SESSION_PRIVACY_MAX_FIELDS = 1024
PRIVACY_MAX_STRING_BYTES = 64 * 1024
PRIVACY_VALUES_MAX_BYTES = 1024 * 1024
PRIVACY_VALUES_MAX_DEPTH = 16
PRIVACY_MAX_COLLECTION_ITEMS = 4096
PRIVACY_MAX_LABELS = 64
PRIVACY_LABELS_MAX_BYTES = 64 * 1024
PRIVACY_CHANGE_REASON_MAX_CHARS = 1024

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")


def validate_privacy_identifier(value: str, field: str) -> str:
    """Validate one URI path segment used by the Privacy storage layout."""
    reason = None
    if not isinstance(value, str):
        reason = "must be a string"
    elif not value:
        reason = "must not be empty"
    elif value != value.strip():
        reason = "must not contain leading or trailing whitespace"
    elif len(value) > PRIVACY_IDENTIFIER_MAX_CHARS:
        reason = f"must not exceed {PRIVACY_IDENTIFIER_MAX_CHARS} characters"
    elif value in {".", ".."}:
        reason = "must not be '.' or '..'"
    elif not value.isascii() or _IDENTIFIER_RE.fullmatch(value) is None:
        reason = "may contain only ASCII letters, digits, '_', '-', '.', and '@'"
    elif value.count("@") > 1:
        reason = "must contain at most one '@'"
    if reason is not None:
        raise InvalidArgumentError(
            f"Invalid Privacy {field}: {reason}.",
            details={
                "field": field,
                "reason": reason,
                "allowed_pattern": "[A-Za-z0-9_.@-]+",
                "max_characters": PRIVACY_IDENTIFIER_MAX_CHARS,
            },
        )
    return value


def validate_privacy_field_name(value: str, *, location: str = "values") -> str:
    """Validate a JSON object key without restricting user-defined field names."""
    if not isinstance(value, str) or not value:
        reason = "must be a non-empty string"
    elif len(value.encode("utf-8")) > PRIVACY_FIELD_NAME_MAX_BYTES:
        reason = f"must not exceed {PRIVACY_FIELD_NAME_MAX_BYTES} UTF-8 bytes"
    elif any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        reason = "must not contain control characters"
    else:
        return value
    raise InvalidArgumentError(
        f"Invalid Privacy field name in {location}: {reason}.",
        details={
            "field": location,
            "reason": reason,
            "max_bytes": PRIVACY_FIELD_NAME_MAX_BYTES,
        },
    )


def _validate_json_tree(value: Any, *, location: str, depth: int) -> None:
    if depth > PRIVACY_VALUES_MAX_DEPTH:
        raise InvalidArgumentError(
            f"Privacy {location} exceeds the maximum JSON nesting depth of "
            f"{PRIVACY_VALUES_MAX_DEPTH}.",
            details={
                "field": location,
                "reason": "maximum nesting depth exceeded",
                "max_depth": PRIVACY_VALUES_MAX_DEPTH,
            },
        )
    if isinstance(value, str):
        size = len(value.encode("utf-8"))
        if size > PRIVACY_MAX_STRING_BYTES:
            raise InvalidArgumentError(
                f"Privacy string at {location} exceeds the maximum size of "
                f"{PRIVACY_MAX_STRING_BYTES} UTF-8 bytes.",
                details={
                    "field": location,
                    "reason": "string value too large",
                    "actual_bytes": size,
                    "max_bytes": PRIVACY_MAX_STRING_BYTES,
                },
            )
        return
    if isinstance(value, Mapping):
        if len(value) > PRIVACY_MAX_COLLECTION_ITEMS:
            raise InvalidArgumentError(
                f"Privacy object at {location} contains {len(value)} entries; at most "
                f"{PRIVACY_MAX_COLLECTION_ITEMS} are allowed.",
                details={
                    "field": location,
                    "reason": "collection too large",
                    "actual_items": len(value),
                    "max_items": PRIVACY_MAX_COLLECTION_ITEMS,
                },
            )
        for key, item in value.items():
            validate_privacy_field_name(key, location=location)
            _validate_json_tree(item, location=f"{location}.{key}", depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > PRIVACY_MAX_COLLECTION_ITEMS:
            raise InvalidArgumentError(
                f"Privacy array at {location} contains {len(value)} items; at most "
                f"{PRIVACY_MAX_COLLECTION_ITEMS} are allowed.",
                details={
                    "field": location,
                    "reason": "collection too large",
                    "actual_items": len(value),
                    "max_items": PRIVACY_MAX_COLLECTION_ITEMS,
                },
            )
        for index, item in enumerate(value):
            _validate_json_tree(item, location=f"{location}[{index}]", depth=depth + 1)
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise InvalidArgumentError(
            f"Privacy value at {location} must be a finite JSON number.",
            details={"field": location, "reason": "non-finite number"},
        )
    if value is not None and not isinstance(value, (bool, int, float)):
        raise InvalidArgumentError(
            f"Privacy value at {location} must be JSON-compatible.",
            details={"field": location, "reason": "unsupported value type"},
        )


def _serialized_size(value: Mapping[str, Any]) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def validate_privacy_values(values: Mapping[str, Any], *, category: str = "") -> None:
    """Validate the complete active values snapshot before persistence."""
    if not isinstance(values, Mapping):
        raise InvalidArgumentError(
            "Privacy values must be a JSON object.",
            details={"field": "values", "reason": "expected object"},
        )
    max_fields = SESSION_PRIVACY_MAX_FIELDS if category == "session" else PRIVACY_MAX_FIELDS
    if len(values) > max_fields:
        raise InvalidArgumentError(
            f"Privacy values contain {len(values)} fields; at most {max_fields} are allowed.",
            details={
                "field": "values",
                "reason": "too many fields",
                "actual_fields": len(values),
                "max_fields": max_fields,
            },
        )
    _validate_json_tree(values, location="values", depth=1)
    size = _serialized_size(values)
    if size > PRIVACY_VALUES_MAX_BYTES:
        raise InvalidArgumentError(
            f"Privacy values serialize to {size} bytes; the maximum is "
            f"{PRIVACY_VALUES_MAX_BYTES} bytes.",
            details={
                "field": "values",
                "reason": "serialized payload too large",
                "actual_bytes": size,
                "max_bytes": PRIVACY_VALUES_MAX_BYTES,
            },
        )


def validate_privacy_metadata(
    *,
    change_reason: str,
    labels: Mapping[str, Any] | None,
) -> None:
    """Validate optional snapshot metadata without exposing values in errors."""
    if not isinstance(change_reason, str):
        raise InvalidArgumentError(
            "Privacy change_reason must be a string.",
            details={"field": "change_reason", "reason": "expected string"},
        )
    if len(change_reason) > PRIVACY_CHANGE_REASON_MAX_CHARS:
        raise InvalidArgumentError(
            f"Privacy change_reason must not exceed {PRIVACY_CHANGE_REASON_MAX_CHARS} characters.",
            details={
                "field": "change_reason",
                "reason": "value too long",
                "actual_characters": len(change_reason),
                "max_characters": PRIVACY_CHANGE_REASON_MAX_CHARS,
            },
        )
    if labels is None:
        return
    if not isinstance(labels, Mapping):
        raise InvalidArgumentError(
            "Privacy labels must be a JSON object.",
            details={"field": "labels", "reason": "expected object"},
        )
    if len(labels) > PRIVACY_MAX_LABELS:
        raise InvalidArgumentError(
            f"Privacy labels contain {len(labels)} entries; at most {PRIVACY_MAX_LABELS} are allowed.",
            details={
                "field": "labels",
                "reason": "too many labels",
                "actual_fields": len(labels),
                "max_fields": PRIVACY_MAX_LABELS,
            },
        )
    _validate_json_tree(labels, location="labels", depth=1)
    size = _serialized_size(labels)
    if size > PRIVACY_LABELS_MAX_BYTES:
        raise InvalidArgumentError(
            f"Privacy labels serialize to {size} bytes; the maximum is "
            f"{PRIVACY_LABELS_MAX_BYTES} bytes.",
            details={
                "field": "labels",
                "reason": "serialized payload too large",
                "actual_bytes": size,
                "max_bytes": PRIVACY_LABELS_MAX_BYTES,
            },
        )
