# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared implementation helpers for active policy training gates."""

from __future__ import annotations

import re
from typing import Any

from .models import GateTarget


def _preview_text(text: str, *, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _experience_constraint_and_trigger(
    content: str,
    target: GateTarget,
) -> tuple[str, str]:
    fields: dict[str, Any] = {}
    if target.gradient is not None:
        fields = dict(getattr(target.gradient.after_file, "extra_fields", {}) or {})
    elif target.plan_item is not None and isinstance(target.plan_item.metadata, dict):
        for key in ("merge_memory_fields", "patch_metadata"):
            value = target.plan_item.metadata.get(key)
            if isinstance(value, dict):
                fields.update(value)
    rendered_fields = _rendered_experience_trigger_fields(content)
    for key, value in rendered_fields.items():
        fields.setdefault(key, value)
    trigger = str(fields.get("trigger_code") or "").strip()
    return str(fields.get("constraint") or fields.get("content") or content or "").strip(), trigger


def _rendered_experience_trigger_fields(content: str) -> dict[str, str]:
    """Parse trigger fields from rendered experience markdown as VikingBot does."""

    text = str(content or "")
    section_match = re.search(
        r"(?ims)^#{1,6}\s*Experience\s+Trigger\s*\n(?P<section>.*?)(?=^#{1,6}\s+|\Z)",
        text,
    )
    if not section_match:
        return {}
    section = section_match.group("section")
    parsed: dict[str, str] = {}
    name_match = re.search(r"(?im)^\s*-?\s*experience_name\s*:\s*(?P<name>[^\n]+)", section)
    if name_match:
        parsed["experience_name"] = name_match.group("name").strip().strip("` ")
    trigger_match = re.search(
        r"(?is)trigger_code\s*:\s*```(?:python)?\s*(?P<code>.*?)\s*```",
        section,
    )
    if trigger_match:
        parsed["trigger_code"] = trigger_match.group("code").strip()
    constraint = (text[: section_match.start()] + text[section_match.end() :]).strip()
    if constraint:
        parsed["constraint"] = constraint
    return parsed


def _markdown_section(content: str, heading: str) -> str:
    pattern = re.compile(rf"(?ims)^##\s+{re.escape(heading)}\s*\n(?P<body>.*?)(?=^##\s+|\Z)")
    match = pattern.search(content or "")
    return match.group("body").strip() if match else ""


def _field_from_section(section: str, field_name: str) -> str:
    return _first_match(section, rf"(?mi)^\s*-\s*{re.escape(field_name)}:\s*(.+)$")


def _first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text or "")
    return match.group(1).strip() if match else ""
