# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Content endpoints for OpenViking HTTP Server."""

from fastapi import APIRouter, Depends, Query

from openviking.server.auth import verify_api_key
from openviking.server.dependencies import get_service
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/content", tags=["content"])


@router.get("/read")
async def read(
    uri: str = Query(..., description="Viking URI"),
    _: bool = Depends(verify_api_key),
):
    """Read file content (L2)."""
    service = get_service()
    result = await service.fs.read(uri)
    return Response(status="ok", result=result)


@router.get("/abstract")
async def abstract(
    uri: str = Query(..., description="Viking URI"),
    _: bool = Depends(verify_api_key),
):
    """Read L0 abstract."""
    service = get_service()
    result = await service.fs.abstract(uri)
    return Response(status="ok", result=result)


@router.get("/overview")
async def overview(
    uri: str = Query(..., description="Viking URI"),
    _: bool = Depends(verify_api_key),
):
    """Read L1 overview."""
    service = get_service()
    result = await service.fs.overview(uri)
    return Response(status="ok", result=result)
