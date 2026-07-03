# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""UnderstandingParseMsg: External parse queue message for UnderstandingAPI."""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4


@dataclass
class UnderstandingParseMsg:
    id: str
    task_id: str
    telemetry_id: Optional[str]
    path: str
    root_uri: str
    lock_handoff: Optional[Dict[str, Any]] = None
    status: str = "pending"
    timestamp: int = int(datetime.now().timestamp())
    account_id: str = "default"
    user_id: str = "default"
    role: str = "root"
    actor_peer_id: Optional[str] = None
    reason: str = ""
    instruction: str = ""
    build_index: bool = True
    summarize: bool = False
    strict: bool = False
    ignore_dirs: Optional[str] = None
    include: Optional[str] = None
    exclude: Optional[str] = None
    directly_upload_media: bool = True
    allow_local_path_resolution: bool = True
    enforce_public_remote_targets: bool = False
    args: Dict[str, Any] = field(default_factory=dict)
    source_name: Optional[str] = None

    def __init__(
        self,
        *,
        task_id: str,
        path: str,
        root_uri: str,
        account_id: str,
        user_id: str,
        role: str,
        actor_peer_id: Optional[str] = None,
        telemetry_id: Optional[str] = None,
        lock_handoff: Optional[Dict[str, Any]] = None,
        reason: str = "",
        instruction: str = "",
        build_index: bool = True,
        summarize: bool = False,
        strict: bool = False,
        ignore_dirs: Optional[str] = None,
        include: Optional[str] = None,
        exclude: Optional[str] = None,
        directly_upload_media: bool = True,
        allow_local_path_resolution: bool = True,
        enforce_public_remote_targets: bool = False,
        args: Optional[Dict[str, Any]] = None,
        source_name: Optional[str] = None,
    ):
        self.id = str(uuid4())
        self.task_id = task_id
        self.telemetry_id = telemetry_id
        self.path = path
        self.root_uri = root_uri
        self.account_id = account_id
        self.user_id = user_id
        self.role = role
        self.actor_peer_id = actor_peer_id
        self.lock_handoff = lock_handoff
        self.reason = reason
        self.instruction = instruction
        self.build_index = build_index
        self.summarize = summarize
        self.strict = strict
        self.ignore_dirs = ignore_dirs
        self.include = include
        self.exclude = exclude
        self.directly_upload_media = directly_upload_media
        self.allow_local_path_resolution = allow_local_path_resolution
        self.enforce_public_remote_targets = enforce_public_remote_targets
        self.args = args or {}
        self.source_name = source_name

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UnderstandingParseMsg":
        if not data:
            raise ValueError("Data dictionary is empty")
        task_id = data.get("task_id")
        path = data.get("path")
        root_uri = data.get("root_uri")
        if not task_id or not path or not root_uri:
            missing = []
            if not task_id:
                missing.append("task_id")
            if not path:
                missing.append("path")
            if not root_uri:
                missing.append("root_uri")
            raise ValueError(f"Missing required fields: {missing}")

        obj = cls(
            task_id=str(task_id),
            path=str(path),
            root_uri=str(root_uri),
            account_id=str(data.get("account_id", "default")),
            user_id=str(data.get("user_id", "default")),
            role=str(data.get("role", "root")),
            actor_peer_id=data.get("actor_peer_id"),
            telemetry_id=str(data.get("telemetry_id"))
            if isinstance(data.get("telemetry_id"), str)
            else None,
            lock_handoff=data.get("lock_handoff")
            if isinstance(data.get("lock_handoff"), dict)
            else None,
            reason=str(data.get("reason", "")),
            instruction=str(data.get("instruction", "")),
            build_index=bool(data.get("build_index", True)),
            summarize=bool(data.get("summarize", False)),
            strict=bool(data.get("strict", False)),
            ignore_dirs=data.get("ignore_dirs"),
            include=data.get("include"),
            exclude=data.get("exclude"),
            directly_upload_media=bool(data.get("directly_upload_media", True)),
            allow_local_path_resolution=bool(data.get("allow_local_path_resolution", True)),
            enforce_public_remote_targets=bool(data.get("enforce_public_remote_targets", False)),
            args=data.get("args") if isinstance(data.get("args"), dict) else {},
            source_name=data.get("source_name"),
        )
        if data.get("id"):
            obj.id = str(data["id"])
        if "status" in data:
            obj.status = str(data["status"])
        if "timestamp" in data:
            obj.timestamp = int(data["timestamp"])
        return obj

    @classmethod
    def from_json(cls, json_str: str) -> "UnderstandingParseMsg":
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON string: {e}")
        return cls.from_dict(data)
