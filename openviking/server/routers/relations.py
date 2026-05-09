# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Relations endpoints for OpenViking HTTP Server."""

from typing import List, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.schemas import ExcludeNoneRoute
from openviking.server.schemas.filesystem import FromTo
from openviking.server.schemas.relations import LinkResult, RelationEntry

router = APIRouter(
    prefix="/api/v1/relations",
    tags=["relations"],
    route_class=ExcludeNoneRoute,
)


class LinkRequest(BaseModel):
    """Request model for link."""

    from_uri: str
    to_uris: Union[str, List[str]]
    reason: str = ""


class UnlinkRequest(BaseModel):
    """Request model for unlink."""

    from_uri: str
    to_uri: str


@router.get("", response_model=Response[List[RelationEntry]])
async def relations(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[List[RelationEntry]]:
    """Get relations for a resource."""
    service = get_service()
    result = await service.relations.relations(uri, ctx=_ctx)
    return Response(
        status="ok",
        result=[RelationEntry.model_validate(item) for item in result],
    )


@router.post("/link", response_model=Response[LinkResult])
async def link(
    request: LinkRequest,
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[LinkResult]:
    """Create link between resources."""
    service = get_service()
    await service.relations.link(request.from_uri, request.to_uris, ctx=_ctx, reason=request.reason)
    return Response(
        status="ok",
        result=LinkResult(from_=request.from_uri, to=request.to_uris),
    )


@router.delete("/link", response_model=Response[FromTo])
async def unlink(
    request: UnlinkRequest,
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[FromTo]:
    """Remove link between resources."""
    service = get_service()
    await service.relations.unlink(request.from_uri, request.to_uri, ctx=_ctx)
    return Response(
        status="ok",
        result=FromTo(from_=request.from_uri, to=request.to_uri),
    )
