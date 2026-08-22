# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Canonical conversions between SKILL.md fields and training policy state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


class SkillStateAdapter:
    """Keep Skill frontmatter intact across extraction, planning, and writes."""

    _NON_FRONTMATTER_KEYS = {
        "content",
        "memory_type",
        "name",
        "skill_name",
        "source_path",
        "status",
        "version",
    }

    @classmethod
    def _copy_frontmatter(cls, fields: Mapping[str, Any] | None) -> dict[str, Any]:
        return {
            key: deepcopy(value)
            for key, value in dict(fields or {}).items()
            if key not in cls._NON_FRONTMATTER_KEYS and not key.startswith("_")
        }

    @classmethod
    def policy_metadata(cls, fields: Mapping[str, Any] | None) -> dict[str, Any]:
        metadata = cls._copy_frontmatter(fields)
        metadata["memory_type"] = "skills"
        return metadata

    @classmethod
    def merge_policy_metadata(
        cls,
        base: Mapping[str, Any] | None,
        fields: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        metadata = deepcopy(dict(base or {}))
        updates = cls.policy_metadata(fields)
        updates = {key: value for key, value in updates.items() if value is not None}
        if updates.get("description") == "":
            updates.pop("description")
        metadata.update(updates)
        return metadata

    @classmethod
    def skill_payload(
        cls,
        *,
        name: str,
        content: str | None,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        payload = cls._copy_frontmatter(metadata)
        payload["name"] = name
        payload["description"] = payload.get("description", "")
        payload["content"] = content
        payload.setdefault("allowed_tools", [])
        payload.setdefault("tags", [])
        return payload

    @classmethod
    def operation_fields(cls, policy: Any) -> dict[str, Any]:
        payload = cls.skill_payload(
            name=str(policy.name),
            content=str(policy.content),
            metadata=policy.metadata,
        )
        payload["skill_name"] = payload.pop("name")
        return payload
