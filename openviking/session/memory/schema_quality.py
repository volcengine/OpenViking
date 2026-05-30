# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Schema-derived memory content quality helpers."""

from __future__ import annotations

import re
import textwrap
from typing import Any

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def normalize_heading(heading: str) -> str:
    return heading.strip().rstrip(":：").casefold()


def extract_markdown_headings(text: str) -> list[str]:
    headings = []
    seen = set()
    for match in _HEADING_RE.finditer(textwrap.dedent(text or "")):
        heading = match.group(1).strip().rstrip(":：")
        normalized = normalize_heading(heading)
        if heading and normalized not in seen:
            headings.append(heading)
            seen.add(normalized)
    return headings


def schema_heading_requirements(schema: Any) -> dict[str, list[str]]:
    return {
        field.name: headings
        for field in getattr(schema, "fields", []) or []
        if (headings := extract_markdown_headings(getattr(field, "description", "") or ""))
    }


def load_schema_heading_requirements(memory_types: set[str]) -> dict[str, dict[str, list[str]]]:
    """Derive field-level heading requirements from active memory schemas."""

    try:
        from openviking.session.memory.memory_type_registry import MemoryTypeRegistry

        registry = MemoryTypeRegistry(load_schemas=True)
    except Exception:
        return {}

    requirements: dict[str, dict[str, list[str]]] = {}
    for memory_type in sorted(memory_types):
        schema = registry.get(memory_type)
        if not schema:
            continue
        field_requirements = schema_heading_requirements(schema)
        if field_requirements:
            requirements[memory_type] = field_requirements
    return requirements


def missing_required_headings_in_text(text: str, required_headings: list[str]) -> list[str]:
    seen = {normalize_heading(match.group(1)) for match in _HEADING_RE.finditer(text or "")}
    return [heading for heading in required_headings if normalize_heading(heading) not in seen]


def memory_field_text(memory_file: Any, field_name: str) -> str:
    if field_name == "content":
        return memory_file.plain_content()
    value = memory_file.extra_fields.get(field_name)
    if value is None:
        return ""
    return str(value)


def missing_schema_headings(
    memory_file: Any,
    field_heading_requirements: dict[str, list[str]],
) -> dict[str, list[str]]:
    missing_by_field: dict[str, list[str]] = {}
    for field_name, required_headings in field_heading_requirements.items():
        missing = missing_required_headings_in_text(
            memory_field_text(memory_file, field_name),
            required_headings,
        )
        if missing:
            missing_by_field[field_name] = missing
    return missing_by_field
