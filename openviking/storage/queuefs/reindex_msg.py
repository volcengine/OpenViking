# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable root request for asynchronous reindex work."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass(kw_only=True)
class ReindexMsg:
    task_id: str
    uri: str
    object_type: str
    mode: str
    account_id: str
    user_id: str
    role: str
    force: bool = False
    recursive: bool = True
    tags: Optional[list[str]] = None
    tag_mode: str = "replace"
    group_ids: list[str] = field(default_factory=list)
    actor_peer_id: Optional[str] = None
    bypass_acl: bool = False
    telemetry_id: Optional[str] = None
    lock_handoff: Optional[Dict[str, Any]] = None
    lock_handoff_retry: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReindexMsg":
        required = ("task_id", "uri", "object_type", "mode", "account_id", "user_id")
        missing = [name for name in required if not data.get(name)]
        if missing:
            raise ValueError(f"Missing required reindex fields: {missing}")
        try:
            lock_handoff_retry = max(0, int(data.get("lock_handoff_retry", 0) or 0))
        except (TypeError, ValueError):
            lock_handoff_retry = 0
        return cls(
            task_id=str(data["task_id"]),
            uri=str(data["uri"]),
            object_type=str(data["object_type"]),
            mode=str(data["mode"]),
            account_id=str(data["account_id"]),
            user_id=str(data["user_id"]),
            role=str(data.get("role") or "root"),
            force=bool(data.get("force", False)),
            recursive=bool(data.get("recursive", True)),
            tags=list(data["tags"]) if isinstance(data.get("tags"), list) else None,
            tag_mode=str(data.get("tag_mode") or "replace"),
            group_ids=[str(value) for value in data.get("group_ids", [])],
            actor_peer_id=data.get("actor_peer_id")
            if isinstance(data.get("actor_peer_id"), str)
            else None,
            bypass_acl=bool(data.get("bypass_acl", False)),
            telemetry_id=data.get("telemetry_id")
            if isinstance(data.get("telemetry_id"), str)
            else None,
            lock_handoff=data.get("lock_handoff")
            if isinstance(data.get("lock_handoff"), dict)
            else None,
            lock_handoff_retry=lock_handoff_retry,
        )
