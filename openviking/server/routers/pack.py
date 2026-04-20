# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Pack endpoints for OpenViking HTTP Server."""

import os
import tempfile

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from starlette.background import BackgroundTask

from openviking.server.auth import get_request_context, require_auth_root_or_admin
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import resolve_uploaded_temp_file_id
from openviking.server.models import Response
from openviking_cli.utils.config.open_viking_config import get_openviking_config

router = APIRouter(prefix="/api/v1/pack", tags=["pack"])


class ExportRequest(BaseModel):
    """Request model for export."""

    uri: str


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
@require_auth_root_or_admin
async def export_ovpack(
    request: Request,
    body: ExportRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """Export context as .ovpack file and stream it to client."""
    service = get_service()

    # Create temp file for export
    temp_dir = tempfile.gettempdir()
    temp_file = os.path.join(temp_dir, f"export_{os.urandom(16).hex()}.ovpack")

    try:
        # Export to temp file
        await service.pack.export_ovpack(body.uri, temp_file, ctx=ctx)

        # Determine filename from URI
        base_name = body.uri.strip().rstrip("/").split("/")[-1]
        if not base_name:
            base_name = "export"
        filename = f"{base_name}.ovpack"

        # Create background task for cleanup
        def cleanup():
            if os.path.exists(temp_file):
                os.unlink(temp_file)

        # Stream file back to client with cleanup
        return FileResponse(
            path=temp_file,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(cleanup),
        )
    except Exception:
        # Clean up temp file on error
        if os.path.exists(temp_file):
            os.unlink(temp_file)
        raise


@router.post("/import")
@require_auth_root_or_admin
async def import_ovpack(
    request: Request,
    body: ImportRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """Import .ovpack file."""
    service = get_service()

    upload_temp_dir = get_openviking_config().storage.get_upload_temp_dir()
    file_path, _ = resolve_uploaded_temp_file_id(body.temp_file_id, upload_temp_dir)

    result = await service.pack.import_ovpack(
        file_path,
        body.parent,
        ctx=ctx,
        force=body.force,
        vectorize=body.vectorize,
    )
    return Response(status="ok", result={"uri": result})
