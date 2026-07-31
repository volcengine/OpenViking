# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory extraction policy for session commits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from openviking_cli.exceptions import InvalidArgumentError

_POLICY_KEYS = {"self", "peer", "memory_types", "working_memory"}
_TARGET_KEYS = {"enabled"}


def _memory_policy_shape_error(key: str) -> str:
    if key == "working_memory":
        return "memory_policy.working_memory must be an object"
    return "memory_policy target must be an object"


def _memory_policy_keys_error(key: str) -> str:
    if key == "working_memory":
        return "memory_policy.working_memory supports only: enabled"
    return "memory_policy target supports only: enabled"


def _target_enabled(data: Any, *, default_enabled: bool, key: str = "target") -> bool:
    if data is None:
        return default_enabled
    if not isinstance(data, dict):
        raise InvalidArgumentError(_memory_policy_shape_error(key))
    extra_keys = set(data) - _TARGET_KEYS
    if extra_keys:
        raise InvalidArgumentError(_memory_policy_keys_error(key))
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
    working_memory_enabled: bool = True

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
            self_enabled=_target_enabled(data.get("self"), default_enabled=True, key="self"),
            peer_enabled=_target_enabled(data.get("peer"), default_enabled=True, key="peer"),
            memory_types=_parse_memory_types(data.get("memory_types")),
            working_memory_enabled=_target_enabled(
                data.get("working_memory"), default_enabled=True, key="working_memory"
            ),
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
        if not self.working_memory_enabled:
            data["working_memory"] = {"enabled": False}
        if self.memory_types is not None:
            data["memory_types"] = sorted(self.memory_types)
        return data
