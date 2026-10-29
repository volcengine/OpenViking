# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Watch contracts shared by storage, management and scheduling."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from openviking.resource.processing_mode import DEFAULT_PROCESSING_MODE, ProcessingMode

MUTABLE_FIELDS = frozenset(
    {
        "path",
        "source_type",
        "reason",
        "instruction",
        "watch_interval",
        "build_index",
        "summarize",
        "processing_mode",
        "processor_kwargs",
        "auth_state",
        "connector_states",
        "is_active",
    }
)
PAYLOAD_FIELDS = MUTABLE_FIELDS - {
    "watch_interval",
    "is_active",
    "auth_state",
    "connector_states",
}
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "error"})


def uri_matches_prefix(uri: Optional[str], prefix: str) -> bool:
    """Return whether *uri* is equal to or below *prefix* on a path boundary."""
    if not uri:
        return False
    uri, prefix = uri.rstrip("/"), prefix.rstrip("/")
    return uri == prefix or uri.startswith(f"{prefix}/")


class WatchTask(BaseModel):
    """Resource monitoring task data model."""

    task_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="Unique task identifier"
    )
    path: str = Field(..., description="Resource path to monitor")
    source_type: Optional[str] = Field(None, description="Original resource source type")
    to_uri: Optional[str] = Field(None, description="Target URI")
    to_is_directory: Optional[bool] = Field(
        None,
        description="Whether the target URI is a directory destination",
    )
    parent_uri: Optional[str] = Field(None, description="Parent URI")
    reason: str = Field(default="", description="Reason for monitoring")
    instruction: str = Field(default="", description="Monitoring instruction")
    watch_interval: float = Field(default=60.0, description="Monitoring interval in minutes")
    build_index: bool = Field(default=True, description="Whether to build vector index")
    summarize: bool = Field(default=False, description="Whether to generate summary")
    processing_mode: ProcessingMode = Field(
        default=DEFAULT_PROCESSING_MODE,
        description="Post-ingest processing mode for scheduled add_resource runs",
    )
    processor_kwargs: Dict[str, Any] = Field(
        default_factory=dict, description="Extra kwargs forwarded to processor"
    )
    auth_state: Optional[Dict[str, Any]] = Field(
        default=None, description="Private authentication state for scheduled re-processing"
    )
    connector_states: Optional[Dict[str, Any]] = Field(
        default=None, description="Private external Connector stream states"
    )
    created_at: datetime = Field(default_factory=datetime.now, description="Task creation time")
    last_execution_time: Optional[datetime] = Field(None, description="Last execution time")
    last_task_id: Optional[str] = Field(None, description="Latest ingestion task identifier")
    last_status: Optional[str] = Field(None, description="Latest execution status")
    last_error: Optional[str] = Field(None, description="Latest sanitized execution error")
    next_execution_time: Optional[datetime] = Field(None, description="Next execution time")
    is_active: bool = Field(default=True, description="Whether the task is active")
    account_id: str = Field(default="default", description="Account ID (tenant)")
    user_id: str = Field(default="default", description="User ID who created this task")
    original_role: str = Field(default="user", description="Role used to execute this task")

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat() if v else None}
        extra = "ignore"

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to public dictionary."""
        return {
            "task_id": self.task_id,
            "path": self.path,
            "source_type": self.source_type,
            "to_uri": self.to_uri,
            "parent_uri": self.parent_uri,
            "reason": self.reason,
            "instruction": self.instruction,
            "watch_interval": self.watch_interval,
            "build_index": self.build_index,
            "summarize": self.summarize,
            "processing_mode": self.processing_mode,
            "processor_kwargs": self.processor_kwargs,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_execution_time": self.last_execution_time.isoformat()
            if self.last_execution_time
            else None,
            "last_task_id": self.last_task_id,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "next_execution_time": self.next_execution_time.isoformat()
            if self.next_execution_time
            else None,
            "is_active": self.is_active,
            "account_id": self.account_id,
            "user_id": self.user_id,
            "original_role": self.original_role,
        }

    def to_storage_dict(self) -> Dict[str, Any]:
        """Convert task to a dictionary including private persisted fields."""
        data = self.to_dict()
        data["to_is_directory"] = self.to_is_directory
        if self.auth_state is not None:
            data["auth_state"] = self.auth_state
        if self.connector_states is not None:
            data["connector_states"] = self.connector_states
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WatchTask":
        """Create task from dictionary."""
        data = dict(data)
        for name in ("created_at", "last_execution_time", "next_execution_time"):
            if isinstance(data.get(name), str):
                data[name] = datetime.fromisoformat(data[name])
        if data.get("processor_kwargs") is None:
            data["processor_kwargs"] = {}
        for name in ("auth_state", "connector_states"):
            if data.get(name) is not None and not isinstance(data.get(name), dict):
                data[name] = None
        return cls(**data)

    def calculate_next_execution_time(self) -> datetime:
        """Calculate next execution time based on interval."""
        base_time = self.last_execution_time or self.created_at
        return base_time + timedelta(minutes=self.watch_interval)


class PermissionDeniedError(Exception):
    """Permission denied error for watch operations."""


@dataclass(frozen=True)
class WatchAuthority:
    account_id: str = "default"
    user_id: str = "default"
    role: str = "root"

    @property
    def root(self) -> bool:
        return (self.role or "").lower() == "root"


@dataclass(frozen=True)
class WatchPatch:
    """Explicit field updates; omitted fields are never replaced."""

    values: dict[str, Any]

    def normalized(self) -> dict[str, Any]:
        unknown = self.values.keys() - MUTABLE_FIELDS
        if unknown:
            raise ValueError(f"Unsupported Watch update fields: {sorted(unknown)}")
        values = dict(self.values)
        if "path" in values and not values["path"]:
            raise ValueError("path cannot be cleared")
        if "watch_interval" in values:
            interval = values["watch_interval"]
            if interval is None or interval <= 0:
                raise ValueError("watch_interval must be > 0")
        for name in ("auth_state", "connector_states", "processor_kwargs"):
            if name in values and values[name] is not None and not isinstance(values[name], dict):
                raise ValueError(f"{name} must be an object")
        defaults = {
            "reason": "",
            "instruction": "",
            "build_index": False,
            "summarize": False,
            "processing_mode": DEFAULT_PROCESSING_MODE,
            "processor_kwargs": {},
        }
        for name, default in defaults.items():
            if name in values and values[name] is None:
                values[name] = default
        return values


@dataclass(frozen=True)
class WatchRun:
    """The occupying run of one Watch. The run ID is also the ingestion Task ID."""

    task_id: str
    run_id: str
    task: WatchTask


@dataclass(frozen=True)
class WatchState:
    """One authoritative Watch row; a run stays occupied even when paused or deleted."""

    task: WatchTask
    current_run_id: Optional[str]
    run_started_at: Optional[float]
    deleted: bool
    now: float
    run_fenced: bool = False

    @property
    def enabled(self) -> bool:
        return self.task.is_active and not self.deleted


@dataclass(frozen=True)
class WatchRunResult:
    """Fields atomically settled by matching the occupying run ID."""

    status: str
    execution_task_id: Optional[str] = None
    error: Optional[str] = None
    deactivate: bool = False
    connector_states: Optional[dict[str, Any]] = None
    has_connector_states: bool = False

    def __post_init__(self) -> None:
        if self.status not in TERMINAL_RUN_STATUSES:
            raise ValueError("Watch run result must be terminal")
        if self.has_connector_states and not isinstance(self.connector_states, dict):
            raise ValueError("connector_states must be an object")
