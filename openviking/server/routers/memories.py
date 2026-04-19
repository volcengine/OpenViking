# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory endpoints for OpenViking HTTP Server.

Exposes a direct create/update path for memory files that bypasses session
extraction. Lets callers control scope (user/agent), bucket, optional stable
filename, and store verbatim content. The canonical write path for memories
extracted from sessions remains the commit/extract pipeline; this endpoint
complements it for user-initiated saves (see RFC issue #1251).
"""

from __future__ import annotations

from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, Field

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.telemetry import run_operation
from openviking.telemetry import TelemetryRequest
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


VALID_SCOPES = ("user", "agent")
VALID_BUCKETS = (
    "profile",
    "preferences",
    "entities",
    "events",
    "cases",
    "patterns",
    "tools",
    "skills",
)
_SINGLETON_BUCKETS = {"profile"}


class CreateMemoryRequest(BaseModel):
    """Request to create or update a memory file at a given scope and bucket."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["user", "agent"]
    owner_id: str = Field(
        ...,
        description=(
            "Identifier for the memory owner within the scope. "
            "For scope=user this is the user id; for scope=agent this is the agent id."
        ),
        min_length=1,
    )
    bucket: Literal[
        "profile",
        "preferences",
        "entities",
        "events",
        "cases",
        "patterns",
        "tools",
        "skills",
    ]
    content: str = Field(..., min_length=1)
    filename: Optional[str] = Field(
        default=None,
        description=(
            "Optional stable filename (with or without .md suffix). "
            "Omit for a generated mem_<uuid>.md name. "
            "Must not be set for singleton buckets like 'profile'."
        ),
    )
    mode: Literal["replace", "append"] = "replace"
    wait: bool = False
    timeout: Optional[float] = None
    telemetry: TelemetryRequest = False


router = APIRouter(prefix="/api/v1/memories", tags=["memories"])


def _sanitize_owner_id(owner_id: str) -> str:
    owner_id = owner_id.strip()
    if not owner_id:
        raise InvalidArgumentError("owner_id must be non-empty")
    if "/" in owner_id or "\\" in owner_id or ".." in owner_id.split("/"):
        raise InvalidArgumentError(f"invalid owner_id: {owner_id!r}")
    return owner_id


def _sanitize_filename(filename: Optional[str], bucket: str) -> Optional[str]:
    if filename is None:
        return None
    name = filename.strip()
    if not name:
        raise InvalidArgumentError("filename must be non-empty when provided")
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise InvalidArgumentError(f"invalid filename: {filename!r}")
    if bucket in _SINGLETON_BUCKETS:
        raise InvalidArgumentError(
            f"bucket {bucket!r} is a singleton; filename must not be provided"
        )
    if not name.endswith(".md"):
        name = f"{name}.md"
    return name


def _resolve_memory_uri(*, scope: str, owner_id: str, bucket: str, filename: Optional[str]) -> str:
    if bucket == "profile":
        return f"viking://{scope}/{owner_id}/memories/profile.md"
    leaf = filename if filename else f"mem_{uuid4()}.md"
    return f"viking://{scope}/{owner_id}/memories/{bucket}/{leaf}"


@router.post("")
async def create_memory(
    request: CreateMemoryRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Create or update a memory file with verbatim content.

    Unlike session commit + extraction, this stores the supplied content
    exactly as provided at the caller-specified scope and bucket, then
    triggers the standard memory indexing path so the new memory is
    immediately discoverable via semantic retrieval.
    """
    owner_id = _sanitize_owner_id(request.owner_id)
    filename = _sanitize_filename(request.filename, request.bucket)
    uri = _resolve_memory_uri(
        scope=request.scope,
        owner_id=owner_id,
        bucket=request.bucket,
        filename=filename,
    )

    service = get_service()
    execution = await run_operation(
        operation="memories.create",
        telemetry=request.telemetry,
        fn=lambda: service.fs.create_memory(
            uri=uri,
            content=request.content,
            ctx=_ctx,
            mode=request.mode,
            wait=request.wait,
            timeout=request.timeout,
        ),
    )
    return Response(
        status="ok",
        result=execution.result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)
