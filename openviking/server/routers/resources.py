# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource endpoints for OpenViking HTTP Server."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, ConfigDict, model_validator

from openviking.core.path_variables import resolve_path_variables
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import (
    require_remote_resource_source,
)
from openviking.server.responses import response_from_result
from openviking.server.telemetry import run_operation
from openviking.server.temp_upload_store import TempUploadStore
from openviking.telemetry import TelemetryRequest
from openviking_cli.exceptions import InvalidArgumentError

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
        create_parent: Whether to automatically create the parent directory if it doesn't exist.
            Default is False.
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
    create_parent: bool = False
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


@router.post("/resources/temp_upload")
async def temp_upload(
    request: Request,
    file: UploadFile = File(...),
    telemetry: bool = Form(False),
    upload_mode: str = Form("local"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Upload a temporary file for add_resource or import_ovpack."""

    async def _upload() -> dict[str, str]:
        store = TempUploadStore.build(request.app.state.config)
        temp_file_id = await store.save_upload(file, upload_mode, _ctx)
        return {"temp_file_id": temp_file_id}

    execution = await run_operation(
        operation="resources.temp_upload",
        telemetry=telemetry,
        fn=_upload,
    )
    return response_from_result(execution.result, telemetry=execution.telemetry)


@router.post("/resources")
async def add_resource(
    http_request: Request,
    request: AddResourceRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Add resource to OpenViking."""
    service = get_service()
    if request.to and request.parent:
        raise InvalidArgumentError("Cannot specify both 'to' and 'parent' at the same time.")

    path = request.path
    allow_local_path_resolution = False
    original_filename = None
    resolved = None
    if request.temp_file_id:
        store = TempUploadStore.build(http_request.app.state.config)
        resolved = await store.resolve_for_consume(request.temp_file_id, _ctx)
        path = resolved.local_path
        original_filename = resolved.original_filename
        allow_local_path_resolution = True
    elif path is not None:
        path = require_remote_resource_source(path)
    if path is None:
        raise InvalidArgumentError("Either 'path' or 'temp_file_id' must be provided.")

    # Use original_filename from upload if source_name not explicitly provided
    source_name = request.source_name
    if source_name is None and original_filename is not None:
        source_name = original_filename

    kwargs = {
        "strict": request.strict,
        "source_name": source_name,
        "ignore_dirs": request.ignore_dirs,
        "include": request.include,
        "exclude": request.exclude,
        "directly_upload_media": request.directly_upload_media,
        "watch_interval": request.watch_interval,
        "create_parent": request.create_parent,
    }
    if request.preserve_structure is not None:
        kwargs["preserve_structure"] = request.preserve_structure

    # Resolve path variables before passing to service
    to = resolve_path_variables(request.to) if request.to else None
    parent = resolve_path_variables(request.parent) if request.parent else None

    store = TempUploadStore.build(http_request.app.state.config) if resolved else None

    async def _add() -> dict[str, Any]:
        try:
            result = await service.resources.add_resource(
                path=path,
                ctx=_ctx,
                to=to,
                parent=parent,
                reason=request.reason,
                instruction=request.instruction,
                wait=request.wait,
                timeout=request.timeout,
                allow_local_path_resolution=allow_local_path_resolution,
                enforce_public_remote_targets=True,
                **kwargs,
            )
        except Exception:
            if resolved and store:
                await store.mark_failed(resolved, _ctx)
            raise
        else:
            if resolved and store:
                await store.mark_consumed(resolved, _ctx)
            return result
        finally:
            if resolved:
                await resolved.cleanup()

    execution = await run_operation(
        operation="resources.add_resource",
        telemetry=request.telemetry,
        fn=_add,
    )
    return response_from_result(execution.result, telemetry=execution.telemetry)


@router.post("/skills")
async def add_skill(
    http_request: Request,
    request: AddSkillRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Add skill to OpenViking."""
    service = get_service()
    data = request.data
    allow_local_path_resolution = False
    resolved = None
    if request.temp_file_id:
        store = TempUploadStore.build(http_request.app.state.config)
        resolved = await store.resolve_for_consume(request.temp_file_id, _ctx)
        data = resolved.local_path
        allow_local_path_resolution = True

    store = TempUploadStore.build(http_request.app.state.config) if resolved else None

    async def _add() -> dict[str, Any]:
        try:
            result = await service.resources.add_skill(
                data=data,
                ctx=_ctx,
                wait=request.wait,
                timeout=request.timeout,
                allow_local_path_resolution=allow_local_path_resolution,
            )
        except Exception:
            if resolved and store:
                await store.mark_failed(resolved, _ctx)
            raise
        else:
            if resolved and store:
                await store.mark_consumed(resolved, _ctx)
            return result
        finally:
            if resolved:
                await resolved.cleanup()

    execution = await run_operation(
        operation="resources.add_skill",
        telemetry=request.telemetry,
        fn=_add,
    )
    return response_from_result(execution.result, telemetry=execution.telemetry)
