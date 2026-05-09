# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Models for user privacy configuration storage."""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class UserPrivacyConfigMeta:
    """Metadata persisted in .meta.json for a privacy config target."""

    category: str = ""
    target_key: str = ""
    active_version: int = 0
    latest_version: int = 0
    created_at: str = ""
    updated_at: str = ""
    updated_by: str = ""
    last_accessed_at: str = ""
    labels: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "target_key": self.target_key,
            "active_version": self.active_version,
            "latest_version": self.latest_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "last_accessed_at": self.last_accessed_at,
            "labels": dict(self.labels),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserPrivacyConfigMeta":
        return cls(
            category=data.get("category", ""),
            target_key=data.get("target_key", ""),
            active_version=int(data.get("active_version", 0) or 0),
            latest_version=int(data.get("latest_version", 0) or 0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            updated_by=data.get("updated_by", ""),
            last_accessed_at=data.get("last_accessed_at", ""),
            labels=dict(data.get("labels", {})),
        )


@dataclass
class UserPrivacyConfigVersion:
    """Version snapshot for one privacy config target."""

    version: int = 0
    category: str = ""
    target_key: str = ""
    values: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    created_by: str = ""
    change_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "category": self.category,
            "target_key": self.target_key,
            "values": dict(self.values),
            "created_at": self.created_at,
            "created_by": self.created_by,
            "change_reason": self.change_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserPrivacyConfigVersion":
        return cls(
            version=int(data.get("version", 0) or 0),
            category=data.get("category", ""),
            target_key=data.get("target_key", ""),
            values=dict(data.get("values", {})),
            created_at=data.get("created_at", ""),
            created_by=data.get("created_by", ""),
            change_reason=data.get("change_reason", ""),
        )
