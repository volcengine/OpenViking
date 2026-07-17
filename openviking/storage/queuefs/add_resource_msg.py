# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistent add-resource queue message (legacy ExternalParse queue payload)."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass(kw_only=True)
class AddResourceMsg:
    task_id: str
    root_uri: str
    account_id: str
    user_id: str
    role: str
    path: str = ""
    telemetry_id: Optional[str] = None
    prepared: Optional[Dict[str, Any]] = None
    lock_handoff: Optional[Dict[str, Any]] = None
    actor_peer_id: Optional[str] = None
    reason: str = ""
    instruction: str = ""
    timeout: Optional[float] = None
    build_index: bool = True
    summarize: bool = False
    strict: bool = False
    ignore_dirs: Optional[str] = None
    include: Optional[str] = None
    exclude: Optional[str] = None
    directly_upload_media: bool = True
    preserve_structure: Optional[bool] = None
    create_parent: bool = False
    allow_local_path_resolution: bool = True
    enforce_public_remote_targets: bool = False
    args: Dict[str, Any] = field(default_factory=dict)
    lock_handoff_retry: int = 0
    source_name: Optional[str] = None
    watch_interval: float = 0
    skip_watch_management: bool = True

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.prepared is not None:
            data["args"] = {}
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AddResourceMsg":
        if not isinstance(data, dict) or not data:
            raise ValueError("Data dictionary is empty")
        task_id = data.get("task_id")
        path = data.get("path")
        root_uri = data.get("root_uri")
        prepared = data.get("prepared") if isinstance(data.get("prepared"), dict) else None
        args = dict(data.get("args", {})) if isinstance(data.get("args"), dict) else {}
        legacy_retry = args.pop("_lock_handoff_retry", 0)
        try:
            lock_handoff_retry = max(0, int(data.get("lock_handoff_retry", legacy_retry) or 0))
        except (TypeError, ValueError):
            lock_handoff_retry = 0
        if prepared is not None:
            args.clear()
        if not task_id or (not path and not prepared) or not root_uri:
            missing = []
            if not task_id:
                missing.append("task_id")
            if not path and not prepared:
                missing.append("path or prepared")
            if not root_uri:
                missing.append("root_uri")
            raise ValueError(f"Missing required fields: {missing}")

        return cls(
            task_id=str(task_id),
            path=str(path or ""),
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
            timeout=float(data["timeout"]) if data.get("timeout") is not None else None,
            build_index=bool(data.get("build_index", True)),
            summarize=bool(data.get("summarize", False)),
            strict=bool(data.get("strict", False)),
            ignore_dirs=data.get("ignore_dirs"),
            include=data.get("include"),
            exclude=data.get("exclude"),
            directly_upload_media=bool(data.get("directly_upload_media", True)),
            preserve_structure=(
                bool(data["preserve_structure"])
                if data.get("preserve_structure") is not None
                else None
            ),
            create_parent=bool(data.get("create_parent", False)),
            allow_local_path_resolution=bool(data.get("allow_local_path_resolution", True)),
            enforce_public_remote_targets=bool(data.get("enforce_public_remote_targets", False)),
            args=args,
            lock_handoff_retry=lock_handoff_retry,
            source_name=data.get("source_name"),
            prepared=prepared,
            watch_interval=float(data.get("watch_interval", 0) or 0),
            skip_watch_management=bool(data.get("skip_watch_management", True)),
        )
