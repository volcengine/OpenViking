# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Agent Evolution endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from openviking.core.uri_validation import validate_request_viking_uri
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import ErrorResponse, OffsetPage, PageResponse, Response
from openviking.server.pagination import LargeOffsetPagination, large_offset_pagination

router = APIRouter(prefix="/api/v1/agent-evolution", tags=["agent-evolution"])


class ExperienceTrajectoryItem(BaseModel):
    uri: str
    name: str | None = None
    description: str | None = None
    created_at: Any | None = None
    updated_at: Any | None = None


class ExperienceTrajectoryPage(OffsetPage[ExperienceTrajectoryItem]):
    experience_uri: str


@router.get(
    "/experiences/trajectories",
    response_model=PageResponse[ExperienceTrajectoryPage],
    responses={400: {"model": ErrorResponse}},
)
async def list_experience_trajectories(
    pagination: Annotated[LargeOffsetPagination, Depends(large_offset_pagination)],
    experience_uri: str = Query(..., description="Experience file URI"),
    start_date: str | None = Query(None, description="UTC start date, inclusive (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="UTC end date, inclusive (YYYY-MM-DD)"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """List trajectories produced by commits that read an Experience."""
    service = get_service()
    experience_uri = validate_request_viking_uri(experience_uri, _ctx)
    result = await service.agent_evolution.list_trajectories_by_experience(
        experience_uri=experience_uri,
        ctx=_ctx,
        limit=pagination.limit,
        offset=pagination.offset,
        start_date=start_date,
        end_date=end_date,
    )
    return PageResponse(result=ExperienceTrajectoryPage(**result))


@router.get("/experiences/outcomes")
async def get_experience_outcome_distribution(
    experience_uri: str = Query(..., description="Experience file URI"),
    start_date: str | None = Query(None, description="UTC start date, inclusive (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="UTC end date, inclusive (YYYY-MM-DD)"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Count application trajectories by outcome for an Experience."""
    service = get_service()
    experience_uri = validate_request_viking_uri(experience_uri, _ctx)
    result = await service.agent_evolution.get_experience_outcome_distribution(
        experience_uri=experience_uri,
        ctx=_ctx,
        start_date=start_date,
        end_date=end_date,
    )
    return Response(status="ok", result=result)
