# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Relations endpoints for OpenViking HTTP Server."""

from typing import List, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from openviking.core.path_variables import resolve_path_variables
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response


def _resolve_uri_or_uris(uri: Union[str, List[str]]) -> Union[str, List[str]]:
    """Resolve path variables in a single URI or list of URIs."""
    if isinstance(uri, list):
        return [resolve_path_variables(u) for u in uri]
    return resolve_path_variables(uri)


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
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get relations for a resource."""
    service = get_service()
    uri = resolve_path_variables(uri)
    result = await service.relations.relations(uri, ctx=_ctx)
    return Response(status="ok", result=result)


@router.post("/link")
async def link(
    request: LinkRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Create link between resources."""
    service = get_service()
    from_uri = resolve_path_variables(request.from_uri)
    to_uris = _resolve_uri_or_uris(request.to_uris)
    await service.relations.link(from_uri, to_uris, ctx=_ctx, reason=request.reason)
    return Response(status="ok", result={"from": from_uri, "to": to_uris})


@router.delete("/link")
async def unlink(
    request: UnlinkRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Remove link between resources."""
    service = get_service()
    from_uri = resolve_path_variables(request.from_uri)
    to_uri = resolve_path_variables(request.to_uri)
    await service.relations.unlink(from_uri, to_uri, ctx=_ctx)
    return Response(status="ok", result={"from": from_uri, "to": to_uri})
