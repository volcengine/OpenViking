# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Debug endpoints for OpenViking HTTP Server.

Provides debug API for system diagnostics.
- /api/v1/debug/health - Quick health check
"""

from fastapi import APIRouter, Depends

from openviking.server.auth import verify_api_key
from openviking.server.dependencies import get_service
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/debug", tags=["debug"])


@router.get("/health")
async def debug_health(
    _: bool = Depends(verify_api_key),
):
    """Quick health check."""
    service = get_service()
    is_healthy = service.debug.is_healthy()
    return Response(status="ok", result={"healthy": is_healthy})
