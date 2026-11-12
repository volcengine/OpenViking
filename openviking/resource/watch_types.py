# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Watch contracts shared by storage, management and scheduling."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator

from openviking.resource.processing_mode import DEFAULT_PROCESSING_MODE, ProcessingMode
from openviking.utils.time_utils import parse_iso_datetime

# Storage-format marker for watch-task timestamps. Builds carrying this change
# persist naive UTC strings plus this key; its absence marks legacy storage
# whose naive timestamps are host-local wall time. The key is unknown to older
# builds, which ignore it (``Config.extra = "ignore"``) and keep parsing the
# naive strings they always parsed, so neither an upgrade nor a downgrade
# produces a mixed naive/aware comparison crash.
STORAGE_TIMESTAMPS_TZ_KEY = "timestamps_tz"
STORAGE_TIMESTAMPS_TZ_UTC = "utc"

WATCH_TIMESTAMP_FIELDS = ("created_at", "last_execution_time", "next_execution_time")


def serialize_storage_timestamp(value: Optional[datetime]) -> Optional[str]:
    """Serialize a watch timestamp in the upgrade-compatible storage form.

    Persisted timestamps stay naive ISO strings — byte-compatible with what
    pre-upgrade builds wrote and still parse — so an older binary reading a
    file written by this build never mixes a naive ``datetime.now()`` with an
    aware value. The dropped UTC designation is restored on load through
    ``STORAGE_TIMESTAMPS_TZ_KEY``, keeping the instant exact for readers that
    understand the marker.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None).isoformat()

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
PRIVATE_PATCH_FIELDS = frozenset(
    {"source_checkpoint", "source_checkpoint_version", "credential_version", "credential_paused"}
)
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
    source_checkpoint: Optional[Dict[str, Any]] = Field(default=None)
    source_checkpoint_version: int = Field(default=0)
    run_source_checkpoint_version: Optional[int] = Field(default=None)
    credential_version: int = Field(default=0)
    credential_paused: bool = Field(default=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Task creation time",
    )
    last_execution_time: Optional[datetime] = Field(None, description="Last execution time")
    last_task_id: Optional[str] = Field(None, description="Latest ingestion task identifier")
    last_status: Optional[str] = Field(None, description="Latest execution status")
    last_error: Optional[str] = Field(None, description="Latest sanitized execution error")
    next_execution_time: Optional[datetime] = Field(None, description="Next execution time")
    is_active: bool = Field(default=True, description="Whether the task is active")
    account_id: str = Field(default="default", description="Account ID (tenant)")
    user_id: str = Field(default="default", description="User ID who created this task")
    original_role: str = Field(default="user", description="Role used to execute this task")

    @field_validator("created_at", "last_execution_time", "next_execution_time", mode="before")
    @classmethod
    def _normalize_timestamps(cls, value: Any) -> Any:
        """Normalize watch timestamps to aware UTC.

        Watch state written by older builds holds naive wall-clock values,
        and restored or hand-edited storage can carry offset-aware strings.
        Comparing either against an aware "now" raises
        ``TypeError: can't compare offset-naive and offset-aware datetimes``
        and terminates the scheduler loop, so normalize on the way in: a naive
        value is read as host-local time, which preserves the instant a legacy
        build wrote it at. :meth:`from_dict` re-tags naive values from
        marker-carrying storage as UTC before they reach this validator.
        """
        if isinstance(value, str):
            value = parse_iso_datetime(value)
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.astimezone(timezone.utc)
        return value

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat() if v else None}
        extra = "ignore"
        validate_assignment = True

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
        # Persist naive UTC strings plus the format marker: the strings stay
        # parseable by pre-upgrade builds, and the marker keeps the UTC
        # reading unambiguous for builds that understand it.
        for name in WATCH_TIMESTAMP_FIELDS:
            data[name] = serialize_storage_timestamp(getattr(self, name))
        data[STORAGE_TIMESTAMPS_TZ_KEY] = STORAGE_TIMESTAMPS_TZ_UTC
        for name in (
            "source_checkpoint",
            "source_checkpoint_version",
            "run_source_checkpoint_version",
            "credential_version",
            "credential_paused",
        ):
            data[name] = getattr(self, name)
        if self.auth_state is not None:
            data["auth_state"] = self.auth_state
        if self.connector_states is not None:
            data["connector_states"] = self.connector_states
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WatchTask":
        """Create task from dictionary."""
        data = dict(data)
        # Feature-detect the storage generation: marker-carrying files hold
        # naive UTC strings, legacy files hold naive host-local strings.
        # Tagging marker values as UTC here lets the naive reading in the
        # field validator stay reserved for the legacy interpretation.
        naive_is_utc = data.pop(STORAGE_TIMESTAMPS_TZ_KEY, None) == STORAGE_TIMESTAMPS_TZ_UTC
        for name in WATCH_TIMESTAMP_FIELDS:
            value = data.get(name)
            if isinstance(value, str):
                parsed = parse_iso_datetime(value)
                if parsed.tzinfo is None and naive_is_utc:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                data[name] = parsed
        if data.get("processor_kwargs") is None:
            data["processor_kwargs"] = {}
        for name in ("auth_state", "connector_states", "source_checkpoint"):
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
    invalidate_source_checkpoint: bool = False
    credential_state: Optional[dict[str, Any]] = None
    expected_auth_state: Optional[dict[str, Any]] = None

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

    def resolve(self, task: WatchTask) -> dict[str, Any]:
        """Resolve private state changes against the row held by the store."""
        values = self.normalized()
        state = self.credential_state
        if state is not None:
            if "is_active" in values or "watch_interval" in values:
                raise ValueError("credential_state cannot be combined with scheduling changes")
            if (
                task.auth_state != self.expected_auth_state
                or task.account_id != state["ov_account_id"]
                or task.user_id != state["ov_user_id"]
                or state["version"] <= task.credential_version
            ):
                return {}
            values["credential_version"] = state["version"]
            if state["authorized"] and task.credential_paused:
                values.update(credential_paused=False, is_active=task.watch_interval > 0)
            elif not state["authorized"] and task.is_active:
                values.update(credential_paused=True, is_active=False)
        elif "is_active" in values:
            values["credential_paused"] = False
        if self.invalidate_source_checkpoint or any(
            name in values and values[name] != getattr(task, name)
            for name in ("path", "instruction", "processor_kwargs")
        ):
            values.update(
                source_checkpoint=None,
                source_checkpoint_version=task.source_checkpoint_version + 1,
            )
        return values


@dataclass(frozen=True)
class WatchRun:
    """One submission or an associated business Task, using this call's inputs."""

    task_id: str
    run_id: Optional[str]
    task: WatchTask
    started_at: Optional[float] = None


@dataclass(frozen=True)
class WatchCandidate:
    """Watch identity and target location, without execution parameters."""

    task_id: str
    account_id: str
    user_id: str
    to_uri: Optional[str]
    run_started_at: Optional[float] = None


@dataclass(frozen=True)
class WatchState:
    """One authoritative Watch row; a run stays occupied even when paused or deleted."""

    task: WatchTask
    current_run_id: Optional[str]
    run_started_at: Optional[float]
    deleted: bool
    now: float

    @property
    def enabled(self) -> bool:
        return self.task.is_active and not self.deleted


@dataclass(frozen=True)
class WatchRunning:
    """Run observation and settlement fields, without a full WatchTask or secrets."""

    task_id: str
    account_id: str
    user_id: str
    current_run_id: Optional[str]
    run_started_at: float
    now: float
    connector: bool
    instruction: str


@dataclass(frozen=True)
class WatchReceipt:
    watch_id: str
    task_id: str
    reused: bool


@dataclass(frozen=True)
class WatchRunResult:
    """Fields atomically settled by matching the occupying run ID."""

    status: str
    execution_task_id: Optional[str] = None
    error: Optional[str] = None
    deactivate: bool = False
    connector_states: Optional[dict[str, Any]] = None
    has_connector_states: bool = False
    source_checkpoint: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.status not in TERMINAL_RUN_STATUSES:
            raise ValueError("Watch run result must be terminal")
        if self.has_connector_states and not isinstance(self.connector_states, dict):
            raise ValueError("connector_states must be an object")
        if self.source_checkpoint is not None and not isinstance(self.source_checkpoint, dict):
            raise ValueError("source_checkpoint must be an object")
