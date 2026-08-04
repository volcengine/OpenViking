# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Agent Evolution endpoints."""

from fastapi import APIRouter, Depends, Query

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.service.agent_evolution_service import (
    DEFAULT_TRAJECTORY_PAGE_LIMIT,
    MAX_TRAJECTORY_PAGE_LIMIT,
)

router = APIRouter(prefix="/api/v1/agent-evolution", tags=["agent-evolution"])


@router.get("/experiences/trajectories")
async def list_experience_trajectories(
    experience_uri: str = Query(..., description="Experience file URI"),
    limit: int = Query(
        DEFAULT_TRAJECTORY_PAGE_LIMIT,
        ge=1,
        le=MAX_TRAJECTORY_PAGE_LIMIT,
    ),
    offset: int = Query(0, ge=0),
    _ctx: RequestContext = Depends(get_request_context),
):
    """List trajectories produced by commits that read an Experience."""
    service = get_service()
    result = await service.agent_evolution.list_trajectories_by_experience(
        experience_uri=experience_uri,
        ctx=_ctx,
        limit=limit,
        offset=offset,
    )
    return Response(status="ok", result=result)
