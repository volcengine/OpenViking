# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read-time restore helpers for skill privacy placeholders."""

import re

from openviking.privacy.skill_placeholder import build_placeholder


_SKILL_PREFIX = "viking://agent/skills/"
_SKILL_SUFFIX = "/SKILL.md"


def get_skill_name_from_uri(uri: str) -> str | None:
    if not uri.startswith(_SKILL_PREFIX) or not uri.endswith(_SKILL_SUFFIX):
        return None
    middle = uri[len(_SKILL_PREFIX) : -len(_SKILL_SUFFIX)]
    if not middle or "/" in middle:
        return None
    return middle


def _extract_placeholder_keys(content: str, skill_name: str) -> list[str]:
    pattern = re.compile(r"\{\{ov_privacy:skill:" + re.escape(skill_name) + r":([^}:]+)\}\}")
    keys: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(content):
        key = match.group(1)
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def restore_skill_content(content: str, skill_name: str, values: dict[str, str]) -> str:
    restored = content
    unresolved_entries: list[str] = []

    for field_name in _extract_placeholder_keys(content, skill_name):
        placeholder = build_placeholder(skill_name, field_name)
        raw_value = values.get(field_name)
        if raw_value is None or str(raw_value) == "":
            shown_value = "<missing>" if raw_value is None else '""'
            unresolved_entries.append(f"{field_name}={shown_value}")
            continue
        restored = restored.replace(placeholder, str(raw_value))

    if unresolved_entries:
        related_entries = [
            f"{key}={value}"
            for key, value in values.items()
            if value is not None and str(value) != ""
        ]
        restored += "\n\n[OpenViking Privacy Notice]\n"
        if related_entries:
            restored += "Related configured privacy values: " + ", ".join(related_entries) + "\n"
        restored += (
            "Not replaced (missing config): " + ", ".join(unresolved_entries) + "\n"
        )

    return restored
