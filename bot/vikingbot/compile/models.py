"""Models and stable limits for VikingBot compile tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from openviking.session.memory.dataclass import WikiLink
from vikingbot.channels.openapi_models import OpenVikingConnection

DEFAULT_COMPILE_REASON = (
    "Follow the loaded Skill's instructions to organize the provided source materials "
    "and generate or update the target Wiki."
)
OKF_VERSION = "0.1"
TERMINAL_STATUSES = frozenset({"completed", "failed"})


class CompileLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_roots: int = 16
    skill_files: int = 128
    skill_file_bytes: int = 8 * 1024 * 1024
    skill_total_bytes: int = 32 * 1024 * 1024
    target_catalog_pages: int = 2000
    initial_prompt_chars: int = 200_000
    tool_uri_count: int = 32
    tool_result_bytes: int = 1024 * 1024
    tool_total_result_bytes: int = 8 * 1024 * 1024
    output_pages: int = 64
    output_total_bytes: int = 4 * 1024 * 1024
    concurrent_tasks: int = 2
    task_runtime_seconds: float = 30 * 60


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: list[str] = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    reason: str | None = None
    skill: str = Field(min_length=1)
    openviking_connection: OpenVikingConnection | None = None
    _principal_scope: str = PrivateAttr(default="local")


class SanitizedCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: list[str] = Field(alias="from")
    to: str
    reason: str
    skill: str


class WikiPageDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: int
    title: str
    page_type: str
    summary: str
    body_markdown: str
    source_ids: list[str]
    tags: list[str] = Field(default_factory=list)
    path_hint: str | None = None
    update_uri: str | None = None


class WikiBundleDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pages: list[WikiPageDraft]
    links: list[WikiLink] = Field(default_factory=list)


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
    status: Literal["accepted", "running", "committing", "completed", "failed"]
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
    "WikiPageDraft",
    "utc_now",
]
