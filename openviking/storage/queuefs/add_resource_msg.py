# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistent messages for the resource source and post-process job phases."""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from openviking.resource.processing_mode import DEFAULT_PROCESSING_MODE, ProcessingMode


class AddResourcePhase(str, Enum):
    SOURCE = "source"
    POST_PROCESS = "post_process"


@dataclass(kw_only=True)
class AddResourceMsg:
    task_id: str
    root_uri: str
    account_id: str
    user_id: str
    group_ids: list[str] = field(default_factory=list)
    role: str
    path: str = ""
    source_path: str = ""
    telemetry_id: Optional[str] = None
    prepared: Optional[Dict[str, Any]] = None
    staged_source: Optional[Dict[str, Any]] = None
    job_phase: AddResourcePhase | str | None = None
    lock_handoff: Optional[Dict[str, Any]] = None
    actor_peer_id: Optional[str] = None
    bypass_acl: bool = False
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
    to_is_directory: Optional[bool] = None
    watch_interval: float = 0
    skip_watch_management: bool = True
    defer_target_resolution: bool = False
    understanding_response_id: Optional[str] = None
    understanding_file_id: Optional[str] = None
    processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE
    parse_mode: str = "default"
    tags: Optional[list[str]] = None
    tag_mode: str = "replace"
    internal_task: bool = False

    def __post_init__(self) -> None:
        inferred = (
            AddResourcePhase.POST_PROCESS if self.prepared is not None else AddResourcePhase.SOURCE
        )
        try:
            phase = AddResourcePhase(self.job_phase or inferred)
        except ValueError as exc:
            raise ValueError(f"Unsupported add-resource job phase: {self.job_phase}") from exc
        if phase is AddResourcePhase.POST_PROCESS and self.prepared is None:
            raise ValueError("post_process jobs require prepared data")
        if phase is AddResourcePhase.SOURCE and self.prepared is not None:
            raise ValueError("source jobs cannot contain prepared post-process data")
        if self.prepared is not None and (
            self.staged_source is not None
            or self.understanding_response_id is not None
            or self.understanding_file_id is not None
        ):
            raise ValueError("post_process jobs cannot contain source payloads")
        source_payload_count = sum(
            payload is not None
            for payload in (
                self.staged_source,
                self.understanding_response_id,
                self.understanding_file_id,
            )
        )
        if source_payload_count > 1:
            raise ValueError("source jobs cannot contain multiple source payloads")
        self.job_phase = phase

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
        staged_source = None
        if data.get("staged_source") is not None:
            from openviking.resource.staged_source import StagedSource

            staged_source = StagedSource.from_dict(data["staged_source"]).to_dict()
        if prepared is not None and staged_source is not None:
            raise ValueError("prepared and staged_source are mutually exclusive")
        job_phase = data.get("job_phase") or (
            AddResourcePhase.POST_PROCESS.value
            if prepared is not None
            else AddResourcePhase.SOURCE.value
        )
        args = dict(data.get("args", {})) if isinstance(data.get("args"), dict) else {}
        legacy_retry = args.pop("_lock_handoff_retry", 0)
        try:
            lock_handoff_retry = max(0, int(data.get("lock_handoff_retry", legacy_retry) or 0))
        except (TypeError, ValueError):
            lock_handoff_retry = 0
        if prepared is not None:
            args.clear()
        if not task_id or (not path and not prepared and not staged_source) or not root_uri:
            missing = []
            if not task_id:
                missing.append("task_id")
            if not path and not prepared and not staged_source:
                missing.append("path, prepared, or staged_source")
            if not root_uri:
                missing.append("root_uri")
            raise ValueError(f"Missing required fields: {missing}")

        return cls(
            task_id=str(task_id),
            path=str(path or ""),
            source_path=str(data.get("source_path") or path or ""),
            root_uri=str(root_uri),
            account_id=str(data.get("account_id", "default")),
            user_id=str(data.get("user_id", "default")),
            group_ids=(
                [str(group_id) for group_id in data["group_ids"]]
                if isinstance(data.get("group_ids"), list)
                else []
            ),
            role=str(data.get("role", "root")),
            actor_peer_id=data.get("actor_peer_id"),
            bypass_acl=bool(data.get("bypass_acl", False)),
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
            to_is_directory=(
                bool(data["to_is_directory"]) if data.get("to_is_directory") is not None else None
            ),
            prepared=prepared,
            staged_source=staged_source,
            job_phase=job_phase,
            watch_interval=float(data.get("watch_interval", 0) or 0),
            skip_watch_management=bool(data.get("skip_watch_management", True)),
            defer_target_resolution=bool(data.get("defer_target_resolution", False)),
            understanding_response_id=(
                data.get("understanding_response_id")
                if isinstance(data.get("understanding_response_id"), str)
                else None
            ),
            understanding_file_id=(
                data.get("understanding_file_id")
                if isinstance(data.get("understanding_file_id"), str)
                else None
            ),
            processing_mode=data.get("processing_mode", DEFAULT_PROCESSING_MODE),
            parse_mode=str(data.get("parse_mode") or "default"),
            tags=(list(data["tags"]) if isinstance(data.get("tags"), list) else None),
            tag_mode=str(data.get("tag_mode") or "replace"),
            internal_task=bool(data.get("internal_task", False)),
        )
