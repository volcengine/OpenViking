# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Resource endpoints for OpenViking HTTP Server."""

import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from openviking.server.auth import verify_api_key
from openviking.server.dependencies import get_service
from openviking.server.models import Response
from openviking_cli.utils.config.open_viking_config import get_openviking_config

router = APIRouter(prefix="/api/v1", tags=["resources"])


class AddResourceRequest(BaseModel):
    """Request model for add_resource."""

    path: Optional[str] = None
    temp_path: Optional[str] = None
    target: Optional[str] = None
    reason: str = ""
    instruction: str = ""
    wait: bool = False
    timeout: Optional[float] = None


class AddSkillRequest(BaseModel):
    """Request model for add_skill."""

    data: Any
    wait: bool = False
    timeout: Optional[float] = None


def _cleanup_temp_files(temp_dir: Path, max_age_hours: int = 1):
    """Clean up temporary files older than max_age_hours."""
    if not temp_dir.exists():
        return

    now = time.time()
    max_age_seconds = max_age_hours * 3600

    for file_path in temp_dir.iterdir():
        if file_path.is_file():
            file_age = now - file_path.stat().st_mtime
            if file_age > max_age_seconds:
                file_path.unlink(missing_ok=True)


@router.post("/resources/temp_upload")
async def temp_upload(
    file: UploadFile = File(...),
    _: bool = Depends(verify_api_key),
):
    """Upload a temporary file for add_resource or import_ovpack."""
    config = get_openviking_config()
    temp_dir = config.storage.get_upload_temp_dir()

    # Clean up old temporary files
    _cleanup_temp_files(temp_dir)

    # Save the uploaded file
    file_ext = Path(file.filename).suffix if file.filename else ".tmp"
    temp_filename = f"upload_{uuid.uuid4().hex}{file_ext}"
    temp_file_path = temp_dir / temp_filename

    with open(temp_file_path, "wb") as f:
        f.write(await file.read())

    return Response(status="ok", result={"temp_path": str(temp_file_path)})


@router.post("/resources")
async def add_resource(
    request: AddResourceRequest,
    _: bool = Depends(verify_api_key),
):
    """Add resource to OpenViking."""
    service = get_service()

    path = request.path
    if request.temp_path:
        path = request.temp_path

    result = await service.resources.add_resource(
        path=path,
        target=request.target,
        reason=request.reason,
        instruction=request.instruction,
        wait=request.wait,
        timeout=request.timeout,
    )
    return Response(status="ok", result=result)


@router.post("/skills")
async def add_skill(
    request: AddSkillRequest,
    _: bool = Depends(verify_api_key),
):
    """Add skill to OpenViking."""
    service = get_service()
    result = await service.resources.add_skill(
        data=request.data,
        wait=request.wait,
        timeout=request.timeout,
    )
    return Response(status="ok", result=result)
