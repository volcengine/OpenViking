# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable root request for content-write task finalization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass(kw_only=True)
class ContentWriteMsg:
    task_id: str
    operation: str
    resource_id: str
    result: Dict[str, Any]
    refresh: Dict[str, Any]
    account_id: str
    user_id: str
    role: str
    _task_work_id: str = ""
    group_ids: list[str] = field(default_factory=list)
    actor_peer_id: Optional[str] = None
    bypass_acl: bool = False
    telemetry_id: Optional[str] = None
    lock_handoff: Optional[Dict[str, Any]] = None
    lock_scope: str = "descendants"
    lock_handoff_retry: int = 0
    failure: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContentWriteMsg":
        required = ("task_id", "operation", "resource_id", "account_id", "user_id")
        missing = [name for name in required if not data.get(name)]
        result = data.get("result")
        refresh = data.get("refresh")
        if not isinstance(result, dict):
            missing.append("result")
        if not isinstance(refresh, dict):
            missing.append("refresh")
        if missing:
            raise ValueError(f"Missing required content-write fields: {missing}")
        try:
            lock_handoff_retry = max(0, int(data.get("lock_handoff_retry", 0) or 0))
        except (TypeError, ValueError):
            lock_handoff_retry = 0
        lock_scope = str(data.get("lock_scope") or "descendants")
        if lock_scope not in {"dispatch", "descendants", "semantic_handoff"}:
            lock_scope = "descendants"
        return cls(
            task_id=str(data["task_id"]),
            _task_work_id=str(data.get("_task_work_id") or ""),
            operation=str(data["operation"]),
            resource_id=str(data["resource_id"]),
            result=dict(result),
            refresh=dict(refresh),
            account_id=str(data["account_id"]),
            user_id=str(data["user_id"]),
            role=str(data.get("role") or "root"),
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
            lock_scope=lock_scope,
            lock_handoff_retry=lock_handoff_retry,
            failure=data.get("failure") if isinstance(data.get("failure"), dict) else None,
        )
