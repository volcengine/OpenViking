# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource endpoints for OpenViking HTTP Server."""

import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict, model_validator

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import (
    require_remote_resource_source,
    resolve_uploaded_temp_file_id,
)
from openviking.server.models import Response
from openviking.server.telemetry import run_operation
from openviking.telemetry import TelemetryRequest
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils.config.open_viking_config import get_openviking_config

router = APIRouter(prefix="/api/v1", tags=["resources"])


class AddResourceRequest(BaseModel):
    """Request model for add_resource.

    Attributes:
        path: Remote resource source such as an HTTP(S) URL or repository URL.
            Either path or temp_file_id must be provided.
        temp_file_id: Temporary upload id returned by /api/v1/resources/temp_upload.
            Either path or temp_file_id must be provided.
        to: Target URI for the resource (e.g., "viking://resources/my_resource").
            If not specified, an auto-generated URI will be used.
        parent: Parent URI under which the resource will be stored.
            Cannot be used together with 'to'.
        reason: Reason for adding the resource. Used for documentation and monitoring.
        instruction: Processing instruction for semantic extraction.
            Provides hints for how the resource should be processed.
        wait: Whether to wait for semantic extraction and vectorization to complete.
            Default is False (async processing).
        timeout: Timeout in seconds when wait=True. None means no timeout.
        strict: Whether to use strict mode for processing. Default is True.
        ignore_dirs: Comma-separated list of directory names to ignore during parsing.
        include: Glob pattern for files to include during parsing.
        exclude: Glob pattern for files to exclude during parsing.
        directly_upload_media: Whether to directly upload media files. Default is True.
        preserve_structure: Whether to preserve directory structure when adding directories.
        watch_interval: Watch interval in minutes for automatic resource monitoring.
            - watch_interval > 0: Creates or updates a watch task. The resource will be
              automatically re-processed at the specified interval.
            - watch_interval = 0: No watch task is created. If a watch task exists for
              this resource, it will be cancelled (deactivated).
            - watch_interval < 0: Same as watch_interval = 0, cancels any existing watch task.
            Default is 0 (no monitoring).

            Note: If the target URI already has an active watch task, a ConflictError will be
            raised. You must first cancel the existing watch (set watch_interval <= 0) before
            creating a new one.
    """

    model_config = ConfigDict(extra="forbid")

    path: Optional[str] = None
    temp_file_id: Optional[str] = None
    to: Optional[str] = None
    parent: Optional[str] = None
    reason: str = ""
    instruction: str = ""
    wait: bool = False
    timeout: Optional[float] = None
    strict: bool = False
    source_name: Optional[str] = None
    ignore_dirs: Optional[str] = None
    include: Optional[str] = None
    exclude: Optional[str] = None
    directly_upload_media: bool = True
    preserve_structure: Optional[bool] = None
    telemetry: TelemetryRequest = False
    watch_interval: float = 0

    @model_validator(mode="after")
    def check_path_or_temp_file_id(self):
        if not self.path and not self.temp_file_id:
            raise ValueError("Either 'path' or 'temp_file_id' must be provided")
        return self


class AddSkillRequest(BaseModel):
    """Request model for add_skill.

    Attributes:
        data: Inline skill content or structured skill data. HTTP requests do not treat
            string values as host filesystem paths.
        temp_file_id: Temporary upload id returned by /api/v1/resources/temp_upload.
        wait: Whether to wait for skill processing to complete.
        timeout: Timeout in seconds when wait=True.
    """

    model_config = ConfigDict(extra="forbid")

    data: Any = None
    temp_file_id: Optional[str] = None
    wait: bool = False
    timeout: Optional[float] = None
    telemetry: TelemetryRequest = False

    @model_validator(mode="after")
    def check_data_or_temp_file_id(self):
        if self.data is None and not self.temp_file_id:
            raise ValueError("Either 'data' or 'temp_file_id' must be provided")
        return self


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
    telemetry: bool = Form(False),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Upload a temporary file for add_resource or import_ovpack."""

    async def _upload() -> dict[str, str]:
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

        return {"temp_file_id": temp_filename}

    execution = await run_operation(
        operation="resources.temp_upload",
        telemetry=telemetry,
        fn=_upload,
    )
    return Response(
        status="ok",
        result=execution.result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


@router.post("/resources")
async def add_resource(
    request: AddResourceRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Add resource to OpenViking."""
    service = get_service()
    if request.to and request.parent:
        raise InvalidArgumentError("Cannot specify both 'to' and 'parent' at the same time.")

    upload_temp_dir = get_openviking_config().storage.get_upload_temp_dir()
    path = request.path
    allow_local_path_resolution = False
    if request.temp_file_id:
        path = resolve_uploaded_temp_file_id(request.temp_file_id, upload_temp_dir)
        allow_local_path_resolution = True
    elif path is not None:
        path = require_remote_resource_source(path)
    if path is None:
        raise InvalidArgumentError("Either 'path' or 'temp_file_id' must be provided.")

    kwargs = {
        "strict": request.strict,
        "source_name": request.source_name,
        "ignore_dirs": request.ignore_dirs,
        "include": request.include,
        "exclude": request.exclude,
        "directly_upload_media": request.directly_upload_media,
        "watch_interval": request.watch_interval,
    }
    if request.preserve_structure is not None:
        kwargs["preserve_structure"] = request.preserve_structure

    execution = await run_operation(
        operation="resources.add_resource",
        telemetry=request.telemetry,
        fn=lambda: service.resources.add_resource(
            path=path,
            ctx=_ctx,
            to=request.to,
            parent=request.parent,
            reason=request.reason,
            instruction=request.instruction,
            wait=request.wait,
            timeout=request.timeout,
            allow_local_path_resolution=allow_local_path_resolution,
            enforce_public_remote_targets=True,
            **kwargs,
        ),
    )
    return Response(
        status="ok",
        result=execution.result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


@router.post("/skills")
async def add_skill(
    request: AddSkillRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Add skill to OpenViking."""
    service = get_service()
    upload_temp_dir = get_openviking_config().storage.get_upload_temp_dir()
    data = request.data
    allow_local_path_resolution = False
    if request.temp_file_id:
        data = resolve_uploaded_temp_file_id(request.temp_file_id, upload_temp_dir)
        allow_local_path_resolution = True

    execution = await run_operation(
        operation="resources.add_skill",
        telemetry=request.telemetry,
        fn=lambda: service.resources.add_skill(
            data=data,
            ctx=_ctx,
            wait=request.wait,
            timeout=request.timeout,
            allow_local_path_resolution=allow_local_path_resolution,
        ),
    )
    return Response(
        status="ok",
        result=execution.result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)
