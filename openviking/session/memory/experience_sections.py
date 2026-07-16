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


def render_experience_sections(fields: Mapping[str, Any] | None) -> str:
    values = experience_section_fields(fields)
    return "\n\n".join(
        f"## {heading}\n{values[name]}".rstrip() for name, heading in _SECTION_HEADINGS
    )
