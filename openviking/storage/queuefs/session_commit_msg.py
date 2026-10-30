# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistent Session Phase 2 queue message."""

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List


@dataclass
class SessionCommitMsg:
    task_id: str
    session_id: str
    session_uri: str
    archive_uri: str
    user: Dict[str, str]
    _task_work_id: str = ""
    memory_policy: Dict[str, Any] = field(default_factory=dict)
    # When True, Phase 2's final meta merge also clears the auto-commit error
    # fields and stamps last_auto_commit_at. Defaults keep old producers working.
    record_auto_commit_success: bool = False
    # Resolved custom scalar tags to attach to event memories extracted in this
    # commit. Already normalized by the producer; empty means "no tags".
    event_search_tags: List[str] = field(default_factory=list)
    auto_commit_policy: Dict[str, Any] = field(default_factory=dict)
    session_window_context: int = 128_000
    summary_token_ratio: float = 0.25
    reasoning_effort: str | None = None
    # Version 1 snapshots explicitly encode WM. Version 0 (before default-off)
    # omitted true; only queued work, never saved user intent, inherits that value.
    memory_policy_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SessionCommitMsg":
        """Load a queue message while ignoring fields from newer producers."""
        if not isinstance(payload, dict):
            raise ValueError("session commit queue payload must be an object")
        known_fields = {item.name for item in fields(cls)}
        data = {key: value for key, value in payload.items() if key in known_fields}
        if payload.get("memory_policy_version", 0) == 0:
            policy = dict(data.get("memory_policy") or {})
            policy.setdefault("working_memory", {"enabled": True})
            data["memory_policy"] = policy
        return cls(**data)
