# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Pack endpoints for OpenViking HTTP Server."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import resolve_uploaded_temp_file_id
from openviking.server.models import Response
from openviking_cli.utils.config.open_viking_config import get_openviking_config

router = APIRouter(prefix="/api/v1/pack", tags=["pack"])


class ExportRequest(BaseModel):
    """Request model for export."""

    uri: str
    to: str


class ImportRequest(BaseModel):
    """Request model for import.

    Attributes:
        temp_file_id: Temporary upload id returned by /api/v1/resources/temp_upload.
        parent: Parent URI under which the imported pack will be placed.
        force: Whether to overwrite existing content if needed.
        vectorize: Whether to build vectors for imported content.
    """

    model_config = ConfigDict(extra="forbid")

    temp_file_id: str
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
    result = await service.pack.export_ovpack(request.uri, request.to, ctx=_ctx)
    return Response(status="ok", result={"file": result})


@router.post("/import")
async def import_ovpack(
    request: ImportRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Import .ovpack file."""
    service = get_service()

    upload_temp_dir = get_openviking_config().storage.get_upload_temp_dir()
    file_path = resolve_uploaded_temp_file_id(request.temp_file_id, upload_temp_dir)

    result = await service.pack.import_ovpack(
        file_path,
        request.parent,
        ctx=_ctx,
        force=request.force,
        vectorize=request.vectorize,
    )
    return Response(status="ok", result={"uri": result})
