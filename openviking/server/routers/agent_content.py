# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Dedicated agent content endpoints for stable memory carriers."""

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.telemetry import run_operation
from openviking.telemetry import TelemetryRequest


class CreateAgentContentRequest(BaseModel):
    """Create/register an agent-scoped stable memory carrier."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    content: str = ""
    create_mode: str = "create_if_missing"
    wait: bool = False
    timeout: float | None = None
    telemetry: TelemetryRequest = False


class WriteAgentContentRequest(BaseModel):
    """Mutate an agent-scoped stable memory carrier."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    content: str
    mode: str = "replace"
    wait: bool = False
    timeout: float | None = None
    telemetry: TelemetryRequest = False


router = APIRouter(prefix="/api/v1/agent-content", tags=["agent-content"])


@router.post("")
async def create(
    request: CreateAgentContentRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Create/register a stable named agent memory carrier."""
    service = get_service()
    execution = await run_operation(
        operation="agent_content.create",
        telemetry=request.telemetry,
        fn=lambda: service.fs.create_agent_content(
            uri=request.uri,
            content=request.content,
            ctx=_ctx,
            create_mode=request.create_mode,
            wait=request.wait,
            timeout=request.timeout,
        ),
    )
    return Response(
        status="ok",
        result=execution.result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


@router.post("/write")
async def write(
    request: WriteAgentContentRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Write or merge a stable named agent memory carrier."""
    service = get_service()
    execution = await run_operation(
        operation="agent_content.write",
        telemetry=request.telemetry,
        fn=lambda: service.fs.write_agent_content(
            uri=request.uri,
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
