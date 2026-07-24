# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read-only memory consolidation planning endpoints."""

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, Field

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/memories", tags=["memories"])


class ExactDuplicatePlanRequest(BaseModel):
    """Bound one dry-run scan to one memory-type directory."""

    model_config = ConfigDict(extra="forbid")

    scope_uri: str
    memory_type: str
    node_limit: int = Field(5000, ge=1, le=10000)


@router.post("/consolidation/exact-duplicates/plan")
async def plan_exact_duplicate_memories(
    request: ExactDuplicatePlanRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Return a stable manifest without applying or deleting anything."""

    service = get_service()
    result = await service.plan_exact_memory_duplicates(
        scope_uri=request.scope_uri,
        memory_type=request.memory_type,
        ctx=_ctx,
        node_limit=request.node_limit,
    )
    return Response(status="ok", result=result)
