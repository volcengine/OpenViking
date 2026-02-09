# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Resource endpoints for OpenViking HTTP Server."""

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from openviking.server.auth import verify_api_key
from openviking.server.dependencies import get_service
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1", tags=["resources"])


class AddResourceRequest(BaseModel):
    """Request model for add_resource."""

    path: str
    target: Optional[str] = None
    reason: str = ""
    instruction: str = ""
    wait: bool = False
    timeout: Optional[float] = None


class AddSkillRequest(BaseModel):
    """Request model for add_skill."""

    data: Any
    wait: bool = False
    timeout: Optional[float] = None


@router.post("/resources")
async def add_resource(
    request: AddResourceRequest,
    _: bool = Depends(verify_api_key),
):
    """Add resource to OpenViking."""
    service = get_service()
    result = await service.resources.add_resource(
        path=request.path,
        target=request.target,
        reason=request.reason,
        instruction=request.instruction,
        wait=request.wait,
        timeout=request.timeout,
    )
    return Response(status="ok", result=result)


@router.post("/skills")
async def add_skill(
    request: AddSkillRequest,
    _: bool = Depends(verify_api_key),
):
    """Add skill to OpenViking."""
    service = get_service()
    result = await service.resources.add_skill(
        data=request.data,
        wait=request.wait,
        timeout=request.timeout,
    )
    return Response(status="ok", result=result)
