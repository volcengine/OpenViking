# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory extraction policy for session commits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openviking_cli.exceptions import InvalidArgumentError

DEFAULT_LONG_TERM_MEMORY_TYPES = {
    "profile",
    "preferences",
    "entities",
    "events",
    "cases",
    "patterns",
    "tools",
    "skills",
    "identity",
    "soul",
}

DEFAULT_AGENT_MEMORY_TYPES = {
    "trajectories",
    "experiences",
}


def _target_enabled(data: Any, *, default_enabled: bool) -> bool:
    if data is None:
        return default_enabled
    if not isinstance(data, dict):
        raise InvalidArgumentError("memory_policy target must be an object")
    return bool(data.get("enabled", default_enabled))


@dataclass
class MemoryPolicy:
    """Effective memory policy for one commit."""

    self_enabled: bool = True
    peer_enabled: bool = False

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
            self_enabled=_target_enabled(data.get("self"), default_enabled=True),
            peer_enabled=_target_enabled(data.get("peer"), default_enabled=False),
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
            "self": {"enabled": self.self_enabled},
            "peer": {"enabled": self.peer_enabled},
        }
