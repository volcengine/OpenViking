# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Relations endpoints for OpenViking HTTP Server."""

from typing import List, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from openviking.server.auth import verify_api_key
from openviking.server.dependencies import get_service
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/relations", tags=["relations"])


class LinkRequest(BaseModel):
    """Request model for link."""

    from_uri: str
    to_uris: Union[str, List[str]]
    reason: str = ""


class UnlinkRequest(BaseModel):
    """Request model for unlink."""

    from_uri: str
    to_uri: str


@router.get("")
async def relations(
    uri: str = Query(..., description="Viking URI"),
    _: bool = Depends(verify_api_key),
):
    """Get relations for a resource."""
    service = get_service()
    result = await service.relations.relations(uri)
    return Response(status="ok", result=result)


@router.post("/link")
async def link(
    request: LinkRequest,
    _: bool = Depends(verify_api_key),
):
    """Create link between resources."""
    service = get_service()
    await service.relations.link(request.from_uri, request.to_uris, request.reason)
    return Response(status="ok", result={"from": request.from_uri, "to": request.to_uris})


@router.delete("/link")
async def unlink(
    request: UnlinkRequest,
    _: bool = Depends(verify_api_key),
):
    """Remove link between resources."""
    service = get_service()
    await service.relations.unlink(request.from_uri, request.to_uri)
    return Response(status="ok", result={"from": request.from_uri, "to": request.to_uri})
