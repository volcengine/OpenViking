# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Usage reporter data models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class UsageContext:
    account_id: str
    user_id: str
    session_id: str
    archive_uri: str
    task_id: Optional[str] = None


@dataclass(frozen=True)
class UsageEvent:
    event_type: str
    resource_uri: str
    resource_type: str
    account_id: str
    user_id: str
    session_id: str
    occurred_at: str
    schema_version: str = "v1"
    task_id: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    attributes: Dict[str, Any] = field(default_factory=dict)
    event_id: str = ""

    def __post_init__(self) -> None:
        if self.event_id:
            return
        identity = [
            self.schema_version,
            self.event_type,
            self.account_id,
            self.user_id,
            self.session_id,
            str(self.evidence.get("message_id") or ""),
            str(self.evidence.get("tool_call_id") or ""),
            self.resource_uri,
        ]
        encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        object.__setattr__(self, "event_id", f"ue_{digest}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
