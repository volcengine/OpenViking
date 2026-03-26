# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Pack endpoints for OpenViking HTTP Server."""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, model_validator

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import resolve_uploaded_temp_file_id
from openviking.server.models import Response
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.utils.config.open_viking_config import get_openviking_config

router = APIRouter(prefix="/api/v1/pack", tags=["pack"])


class ExportRequest(BaseModel):
    """Request model for export."""

    uri: str
    to: str


class ImportRequest(BaseModel):
    """Request model for import."""

    model_config = ConfigDict(extra="forbid")

    file_path: Optional[str] = None
    temp_file_id: Optional[str] = None
    parent: str
    force: bool = False
    vectorize: bool = True

    @model_validator(mode="after")
    def check_file_path_or_temp_file_id(self):
        if not self.file_path and not self.temp_file_id:
            raise ValueError("Either 'file_path' or 'temp_file_id' must be provided")
        return self


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

    file_path = None
    if request.temp_file_id:
        upload_temp_dir = get_openviking_config().storage.get_upload_temp_dir()
        file_path = resolve_uploaded_temp_file_id(request.temp_file_id, upload_temp_dir)
    elif request.file_path:
        raise PermissionDeniedError(
            "HTTP server only accepts temp-uploaded ovpack files; direct host filesystem "
            "paths are not allowed."
        )

    result = await service.pack.import_ovpack(
        file_path,
        request.parent,
        ctx=_ctx,
        force=request.force,
        vectorize=request.vectorize,
    )
    return Response(status="ok", result={"uri": result})
