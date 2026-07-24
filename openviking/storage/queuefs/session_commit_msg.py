# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistent Session Phase 2 queue message."""

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional


@dataclass
class SessionCommitMsg:
    task_id: str
    session_id: str
    session_uri: str
    archive_uri: str
    user: Dict[str, str]
    actor_peer_id: Optional[str] = None
    memory_policy: Dict[str, Any] = field(default_factory=dict)
    usage_uris: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SessionCommitMsg":
        """Load a queue message while ignoring fields from newer producers."""
        if not isinstance(payload, dict):
            raise ValueError("session commit queue payload must be an object")
        known_fields = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in payload.items() if key in known_fields})
