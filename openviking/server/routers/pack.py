# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Pack endpoints for OpenViking HTTP Server."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/pack", tags=["pack"])


class ExportRequest(BaseModel):
    """Request model for export."""

    uri: str
    to: str


class ImportRequest(BaseModel):
    """Request model for import."""

    file_path: str
    parent: str
    force: bool = False
    vectorize: bool = True


@router.post("/export")
async def export_ovpack(
    request: ExportRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Export context as .ovpack file."""
    service = get_service()
    result = await service.pack.export_ovpack(request.uri, request.to)
    return Response(status="ok", result={"file": result})


@router.post("/import")
async def import_ovpack(
    request: ImportRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Import .ovpack file."""
    service = get_service()
    result = await service.pack.import_ovpack(
        request.file_path,
        request.parent,
        force=request.force,
        vectorize=request.vectorize,
    )
    return Response(status="ok", result={"uri": result})
