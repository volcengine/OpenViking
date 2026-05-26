# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory extraction policy for session commits."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from openviking_cli.exceptions import InvalidArgumentError

DEFAULT_SELF_MEMORY_TYPES = {
    "profile",
    "preferences",
    "entities",
    "events",
    "cases",
    "patterns",
    "tools",
    "skills",
    "trajectories",
    "experiences",
    "identity",
    "soul",
}

DEFAULT_PEER_MEMORY_TYPES = {
    "profile",
    "preferences",
    "entities",
    "events",
}


@dataclass
class MemoryTargetPolicy:
    """Policy for one memory target."""

    enabled: bool = True
    types: Optional[set[str]] = None

    @classmethod
    def from_dict(cls, data: Any, *, default_enabled: bool) -> "MemoryTargetPolicy":
        if data is None:
            return cls(enabled=default_enabled, types=None)
        if not isinstance(data, dict):
            raise InvalidArgumentError("memory_policy target must be an object")

        raw_types = data.get("types")
        if raw_types is None:
            types = None
        elif isinstance(raw_types, list):
            types = {str(item).strip() for item in raw_types if str(item).strip()}
        else:
            raise InvalidArgumentError("memory_policy target types must be a list or null")

        return cls(enabled=bool(data.get("enabled", default_enabled)), types=types)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "types": sorted(self.types) if self.types is not None else None,
        }

    def allowed_types(self, registered_types: Iterable[str]) -> set[str]:
        registered = {str(item) for item in registered_types}
        if self.types is None:
            return registered
        return set(self.types) & registered


@dataclass
class MemoryPolicy:
    """Effective memory policy for one commit."""

    self_memory: MemoryTargetPolicy = field(
        default_factory=lambda: MemoryTargetPolicy(enabled=True)
    )
    peer_memory: MemoryTargetPolicy = field(
        default_factory=lambda: MemoryTargetPolicy(enabled=False)
    )

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
        return cls(
            self_memory=MemoryTargetPolicy.from_dict(data.get("self"), default_enabled=True),
            peer_memory=MemoryTargetPolicy.from_dict(data.get("peer"), default_enabled=False),
        )

    @classmethod
    def merge(cls, session_policy: Any = None, commit_policy: Any = None) -> "MemoryPolicy":
        """Return the policy used by a commit."""
        if commit_policy is not None:
            return cls.from_dict(commit_policy)
        if session_policy is not None:
            return cls.from_dict(session_policy)
        return cls.default()

    def to_dict(self) -> dict[str, Any]:
        return {
            "self": self.self_memory.to_dict(),
            "peer": self.peer_memory.to_dict(),
        }

    def validate_types(self, registered_types: Iterable[str]) -> None:
        registered = {str(item) for item in registered_types}
        self_allowed = registered & DEFAULT_SELF_MEMORY_TYPES
        peer_allowed = registered & DEFAULT_PEER_MEMORY_TYPES
        unknown: set[str] = set()
        if self.self_memory.types is not None:
            unknown.update(self.self_memory.types - self_allowed)
        if self.peer_memory.types is not None:
            unknown.update(self.peer_memory.types - peer_allowed)
        if unknown:
            raise InvalidArgumentError(
                f"Unsupported memory_policy types: {', '.join(sorted(unknown))}"
            )

    def self_allowed_types(self, registered_types: Iterable[str]) -> set[str]:
        registered = {str(item) for item in registered_types}
        return self.self_memory.allowed_types(registered & DEFAULT_SELF_MEMORY_TYPES)

    def peer_allowed_types(self, registered_types: Iterable[str]) -> set[str]:
        registered = {str(item) for item in registered_types}
        return self.peer_memory.allowed_types(registered & DEFAULT_PEER_MEMORY_TYPES)
