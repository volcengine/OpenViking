"""Models and stable limits for VikingBot compile tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from openviking.session.memory.dataclass import WikiLink
from vikingbot.channels.openapi_models import OpenVikingConnection

DEFAULT_COMPILE_REASON = (
    "Follow the loaded Skill's instructions to transform the provided source materials "
    "into the outputs required by the Skill."
)
COMPILE_STAGING_ROOT = "__compile_staging__"
COMPILE_TARGET_CHECKOUT_ROOT = f"{COMPILE_STAGING_ROOT}/target_checkout"
COMPILE_MATERIALIZED_ROOT = "compile_resources"
COMPILE_MANIFEST_NAME = "_manifest.tsv"
OKF_VERSION = "0.1"
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
WikiLanguage = Literal["en", "zh-CN"]


class CompileLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_roots: int = 16
    source_catalog_entries: int = 200
    source_files: int = 5000
    source_total_bytes: int = 1024 * 1024 * 1024
    target_total_bytes: int = 1024 * 1024 * 1024
    skill_files: int = 128
    skill_file_bytes: int = 8 * 1024 * 1024
    skill_total_bytes: int = 32 * 1024 * 1024
    target_inventory_entries: int = 2000
    target_catalog_pages: int = 10
    initial_prompt_chars: int = 300_000
    agent_context_chars: int = 240_000
    agent_iterations: int = 60
    tool_uri_count: int = 32
    tool_result_bytes: int = 1024 * 1024
    tool_total_result_bytes: int = 8 * 1024 * 1024
    output_pages: int = 128
    output_files: int = 128
    output_operations: int = 256
    output_total_bytes: int = 4 * 1024 * 1024
    concurrent_tasks: int = 10
    accepted_tasks: int = 40
    accepted_tasks_per_principal: int = 10
    queue_wait_seconds: float = 60 * 60
    task_runtime_seconds: float = 60 * 60
    salvage_grace_seconds: float = 120
    cleanup_grace_seconds: float = 40
    terminal_task_retention_seconds: float = 24 * 60 * 60
    terminal_task_records: int = 1000


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: list[str] = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    reason: str | None = None
    skill: str = Field(min_length=1)
    runtime_timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )
    openviking_connection: OpenVikingConnection | None = None
    _principal_scope: str = PrivateAttr(default="local")


class SanitizedCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: list[str] = Field(alias="from")
    to: str
    reason: str
    reason_provided: bool = False
    skill: str
    runtime_timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )


class WikiPageDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: int
    title: str
    page_type: str
    summary: str
    body_markdown: str | None = Field(
        default=None,
        description=(
            "Inline Markdown body for an actual Wiki page. Link relevant known source "
            "URIs with ordinary Markdown links; never invent link targets. For very large "
            "bodies, write the Markdown to the workspace with write_file and submit "
            "body_workspace_path instead."
        ),
    )
    body_workspace_path: str | None = Field(
        default=None,
        description=(
            f"Relative path under {COMPILE_TARGET_CHECKOUT_ROOT}/ for an editable "
            "UTF-8 Markdown Wiki page in the target checkout."
        ),
    )
    source_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Identifiers of supplied source roots that support this Wiki page. Omit to "
            "cite every supplied source root."
        ),
    )
    tags: list[str] = Field(default_factory=list)
    path_hint: str | None = Field(
        default=None,
        description=(
            "Optional relative path for a new Wiki page. Omit by default so its filename "
            "derives from title; use only for Skill-required paths and avoid generic basenames."
        ),
    )
    update_uri: str | None = None

    @model_validator(mode="after")
    def validate_body(self) -> "WikiPageDraft":
        if (self.body_markdown is None) == (self.body_workspace_path is None):
            raise ValueError("exactly one of body_markdown or body_workspace_path is required")
        return self


class CompileFileDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = Field(
        default=None,
        description="Relative target path for a new file under the Compile target.",
    )
    update_uri: str | None = Field(
        default=None,
        description="Catalog URI of an existing target file to replace.",
    )
    content: str | None = Field(
        default=None,
        description=(
            "Exact UTF-8 text file content, including any required frontmatter. For very "
            "large files, write the file to the workspace with write_file and submit "
            "workspace_path instead."
        ),
    )
    workspace_path: str | None = Field(
        default=None,
        description=(
            "Explicit relative path of a file already generated in the task workspace; "
            "its bytes are preserved exactly."
        ),
    )

    @model_validator(mode="after")
    def validate_shape(self) -> "CompileFileDraft":
        if (self.path is None) == (self.update_uri is None):
            raise ValueError("exactly one of path or update_uri is required")
        if (self.content is None) == (self.workspace_path is None):
            raise ValueError("exactly one of content or workspace_path is required")
        return self


class WikiBundleDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pages: list[WikiPageDraft] = Field(
        description=("Actual Wiki pages only; do not place Skill-prescribed artifact files here.")
    )
    files: list[CompileFileDraft] = Field(
        default_factory=list,
        description=(
            "Skill-prescribed exact-path artifacts, including Markdown, YAML, JSON, "
            "and binary files; preserve every required path and format."
        ),
    )
    links: list[WikiLink] = Field(
        default_factory=list,
        description=(
            "Useful non-self relationships between generated Wiki pages only; omit "
            "them when no valid related page exists."
        ),
    )


class CompileErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class CompileResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: list[str] = Field(alias="from")
    to: str
    skill: str
    okf_version: str = OKF_VERSION
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    page_count: int = 0
    link_count: int = 0
    warnings: list[str] = Field(default_factory=list)


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
    result: CompileResult | None = None
    error: CompileErrorInfo | None = None

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump(exclude={"principal_scope", "sanitized_request"}, exclude_none=True)
        if self.result is not None:
            data["result"] = self.result.model_dump(by_alias=True)
        return data


class CompileAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: Literal["accepted"] = "accepted"
    to: str


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
    "CompileFileDraft",
    "CompileFailure",
    "CompileLimits",
    "CompileRequest",
    "CompileResult",
    "CompileTask",
    "DEFAULT_COMPILE_REASON",
    "OKF_VERSION",
    "SanitizedCompileRequest",
    "TERMINAL_STATUSES",
    "WikiBundleDraft",
    "WikiLanguage",
    "WikiPageDraft",
    "utc_now",
]
