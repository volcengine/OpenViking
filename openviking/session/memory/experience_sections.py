# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Structured experience section fields and deterministic Markdown rendering."""

from __future__ import annotations

from typing import Any, Mapping

EXPERIENCE_SECTION_FIELDS = ("situation", "reminder", "procedure", "anti_pattern")

_SECTION_HEADINGS = (
    ("situation", "Situation"),
    ("reminder", "Reminder"),
    ("procedure", "Procedure"),
    ("anti_pattern", "Anti-pattern"),
)


def experience_section_fields(fields: Mapping[str, Any] | None) -> dict[str, str]:
    values = dict(fields or {})
    return {name: str(values.get(name) or "").strip() for name in EXPERIENCE_SECTION_FIELDS}


def resolve_experience_section_fields(
    fields: Mapping[str, Any] | None,
    *,
    base_fields: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Return rendered section strings, applying StrPatch values against base fields."""

    values = dict(fields or {})
    base_values = dict(base_fields or {})
    return {
        name: _resolve_section_value(values.get(name), base_values.get(name))
        for name in EXPERIENCE_SECTION_FIELDS
    }


def render_experience_sections(fields: Mapping[str, Any] | None) -> str:
    values = experience_section_fields(fields)
    return "\n\n".join(
        f"## {heading}\n{values[name]}".rstrip() for name, heading in _SECTION_HEADINGS
    )


def _resolve_section_value(value: Any, current_value: Any) -> str:
    if value is None:
        return str(current_value or "").strip()

    if _is_patch_value(value):
        from openviking.session.memory.merge_op.base import FieldType
        from openviking.session.memory.merge_op.patch import PatchOp

        resolved = PatchOp(FieldType.STRING).apply(
            None if current_value is None else str(current_value),
            value,
        )
        return str(resolved or "").strip()

    return str(value or "").strip()


def _is_patch_value(value: Any) -> bool:
    if isinstance(value, dict):
        return "blocks" in value
    return value.__class__.__name__ == "StrPatch"
