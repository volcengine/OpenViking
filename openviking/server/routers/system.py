# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""System endpoints for OpenViking HTTP Server."""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from openviking.server.auth import verify_api_key
from openviking.server.dependencies import get_service
from openviking.server.models import Response

router = APIRouter()


@router.get("/health", tags=["system"])
async def health_check():
    """Health check endpoint (no authentication required)."""
    return {"status": "ok"}


@router.get("/api/v1/system/status", tags=["system"])
async def system_status(
    _: bool = Depends(verify_api_key),
):
    """Get system status."""
    service = get_service()
    return Response(
        status="ok",
        result={
            "initialized": service._initialized,
            "user": service.user._user_id,
        },
    )


class WaitRequest(BaseModel):
    """Request model for wait."""

    timeout: Optional[float] = None


@router.post("/api/v1/system/wait", tags=["system"])
async def wait_processed(
    request: WaitRequest,
    _: bool = Depends(verify_api_key),
):
    """Wait for all processing to complete."""
    service = get_service()
    result = await service.resources.wait_processed(timeout=request.timeout)
    return Response(status="ok", result=result)
