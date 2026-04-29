# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read-time restore helpers for skill privacy placeholders."""

import re

from openviking.privacy.skill_placeholder import build_placeholder


def get_skill_name_from_uri(uri: str) -> str | None:
    normalized = uri.strip().rstrip("/")
    marker = "/skills/"
    suffix = "/SKILL.md"
    if marker not in normalized or not normalized.endswith(suffix):
        return None
    start = normalized.rfind(marker)
    if start < 0:
        return None
    middle = normalized[start + len(marker) : -len(suffix)]
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
    placeholder_keys = _extract_placeholder_keys(content, skill_name)
    placeholder_key_set = set(placeholder_keys)

    for field_name in placeholder_keys:
        placeholder = build_placeholder(skill_name, field_name)
        raw_value = values.get(field_name)
        if raw_value is None or str(raw_value) == "":
            shown_value = "<missing>" if raw_value is None else '""'
            unresolved_entries.append(f"{field_name}={shown_value}")
            continue
        restored = restored.replace(placeholder, str(raw_value))

    extra_config_entries = [
        f"{key}={value}"
        for key, value in values.items()
        if key not in placeholder_key_set and value is not None and str(value) != ""
    ]

    if unresolved_entries or extra_config_entries:
        restored += "\n\n[Privacy Config Notice]\n"
        if unresolved_entries:
            restored += "Missing config: " + ", ".join(unresolved_entries) + "\n"
        if extra_config_entries:
            restored += (
                "Configured but not referenced in content: "
                + ", ".join(extra_config_entries)
                + "\n"
            )

    return restored
