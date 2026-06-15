# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory extraction policy for session commits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from openviking_cli.exceptions import InvalidArgumentError

_POLICY_KEYS = {"self", "peer", "memory_types"}
_TARGET_KEYS = {"enabled"}


def _target_enabled(data: Any, *, default_enabled: bool) -> bool:
    if data is None:
        return default_enabled
    if not isinstance(data, dict):
        raise InvalidArgumentError("memory_policy target must be an object")
    extra_keys = set(data) - _TARGET_KEYS
    if extra_keys:
        raise InvalidArgumentError("memory_policy target supports only: enabled")
    return bool(data.get("enabled", default_enabled))


def _parse_memory_types(data: Any) -> Optional[set[str]]:
    if data is None:
        return None
    if not isinstance(data, list):
        raise InvalidArgumentError("memory_policy.memory_types must be a list")
    memory_types = set()
    for item in data:
        if not isinstance(item, str) or not item:
            raise InvalidArgumentError("memory_policy.memory_types must contain non-empty strings")
        memory_types.add(item)
    return memory_types


@dataclass
class MemoryPolicy:
    """Effective memory policy for one commit."""

    self_enabled: bool = True
    peer_enabled: bool = True
    memory_types: Optional[set[str]] = None

    @classmethod
    def default(cls) -> "MemoryPolicy":
        return cls()

    @classmethod
    def from_dict(cls, data: Any) -> "MemoryPolicy":
        if data is None:
            return cls.default()
        if isinstance(data, MemoryPolicy):
            return data
        if not isinstance(data, dict):
            raise InvalidArgumentError("memory_policy must be an object")
        extra_keys = set(data) - _POLICY_KEYS
        if extra_keys:
            raise InvalidArgumentError(
                "memory_policy supports only: " + ", ".join(sorted(_POLICY_KEYS))
            )
        return cls(
            self_enabled=_target_enabled(data.get("self"), default_enabled=True),
            peer_enabled=_target_enabled(data.get("peer"), default_enabled=True),
            memory_types=_parse_memory_types(data.get("memory_types")),
        )

    def validate_memory_types(self, known_memory_types: set[str]) -> None:
        if self.memory_types is None:
            return
        unknown = self.memory_types - known_memory_types
        if unknown:
            raise InvalidArgumentError(
                "Unknown memory_policy.memory_types: " + ", ".join(sorted(unknown))
            )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "self": {"enabled": self.self_enabled},
            "peer": {"enabled": self.peer_enabled},
        }
        if self.memory_types is not None:
            data["memory_types"] = sorted(self.memory_types)
        return data
