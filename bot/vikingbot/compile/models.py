"""Models and stable limits for VikingBot compile tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from vikingbot.channels.openapi_models import OpenVikingConnection

DEFAULT_COMPILE_INSTRUCTION = (
    "Follow the loaded Skill's instructions to transform the provided source materials "
    "into the outputs required by the Skill."
)
COMPILE_STAGING_ROOT = "__compile_staging__"
COMPILE_DRAFT_ROOT = f"{COMPILE_STAGING_ROOT}/drafts"
OKF_VERSION = "0.1"
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class CompileLimits(BaseModel):
    """Bound task execution, source loading and Markdown routing concurrency."""

    model_config = ConfigDict(frozen=True)

    # Soft request budget for prompt, input JSON, control schema and estimated output space.
    # Larger indivisible inputs may exceed it; this is not the model's token limit.
    merge_input_chars: int = Field(default=60_000, ge=1)
    # Source text plus repeated context per range/read batch, independent of request packing.
    source_batch_chars: int = Field(default=40_000, ge=1)
    simple_agent_iterations: int = Field(default=150, ge=1, description="Simple compile rounds.")
    # Maximum model/tool rounds for one complex pipeline file worker.
    subagent_iterations: int = Field(default=70, ge=1)
    # Per-task Map workers, also used for Resource source subagents.
    source_concurrency: int = Field(default=6, ge=1)
    # Per-task Shuffle routing workers and embedding batches.
    shuffle_concurrency: int = Field(default=6, ge=1)
    # Maximum primary Markdown drafts per routing request.
    shuffle_batch_size: int = Field(default=4, ge=1, strict=True)
    # Per-task Reduce workers, also used for consolidating same-path candidate files.
    merge_concurrency: int = Field(default=10, ge=1)
    concurrent_tasks: int = 10
    accepted_tasks: int = 40
    accepted_tasks_per_principal: int = 10
    salvage_grace_seconds: float = 120
    cleanup_grace_seconds: float = 40
    terminal_task_retention_seconds: float = 24 * 60 * 60
    terminal_task_records: int = 1000


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: list[str] = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    instruction: str | None = None
    skill: str = Field(min_length=1)
    args: dict[str, Any] | None = None
    openviking_connection: OpenVikingConnection | None = None
    _principal_scope: str = PrivateAttr(default="local")


class SanitizedCompileRequest(BaseModel):
    """Stored normalized inputs ignore unknown fields; the service validates public arguments."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    from_: list[str] = Field(alias="from")
    to: str
    instruction: str
    skill: str
    # The OV service freezes the directory plan before submitting this execution.
    changes: dict[str, Any] | None = None


def has_directory_plan(changes: dict[str, Any] | None) -> bool:
    """Identify directory-based task inputs; request normalization validates their contents."""
    return isinstance(changes, dict) and "diff_dirs" in changes and "full_dirs" in changes


class CompileErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class CompileResult(BaseModel):
    """Stored task outcomes expose only fields in the published result schema."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    from_: list[str] = Field(alias="from")
    to: str
    skill: str
    okf_version: str = OKF_VERSION
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    # Skipped target-state conflicts, each containing uri, code and message; never published.
    conflicts: list[dict[str, str]] = Field(default_factory=list)
    page_count: int = 0
    link_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    completion_status: Literal["partial"] | None = None
    recovery_proof: str | None = None


class CompileTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    principal_scope: str
    sanitized_request: SanitizedCompileRequest
    status: Literal[
        "accepted",
        "running",
        "committing",
        "cancelling",
        "completed",
        "failed",
        "cancelled",
    ]
    stage: str
    created_at: str
    updated_at: str
    meta: dict[str, Any] = Field(default_factory=dict)
    result: CompileResult | None = None
    error: CompileErrorInfo | None = None

    def public_dict(self, *, include_recovery_proof: bool = False) -> dict[str, Any]:
        data = self.model_dump(exclude={"principal_scope", "sanitized_request"}, exclude_none=True)
        if self.result is not None:
            data["result"] = self.result.model_dump(
                by_alias=True,
                exclude_none=True,
                exclude=None if include_recovery_proof else {"recovery_proof"},
            )
        return data


class CompileAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    task_id: str
    status: Literal["accepted"] = "accepted"
    to: str


class CompileSessionRequest(BaseModel):
    """Status, cancellation, or archive request scoped by authenticated caller and session ID."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    # Original task args forwarded by OpenViking; optional and unused for session lookup.
    args: dict[str, Any] | None = None


class CompileFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, stage: str):
        super().__init__(message)
        self.code = code
        self.stage = stage


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "CompileAccepted",
    "CompileErrorInfo",
    "CompileFailure",
    "CompileLimits",
    "CompileRequest",
    "CompileResult",
    "CompileSessionRequest",
    "CompileTask",
    "DEFAULT_COMPILE_INSTRUCTION",
    "OKF_VERSION",
    "SanitizedCompileRequest",
    "TERMINAL_STATUSES",
    "utc_now",
]
