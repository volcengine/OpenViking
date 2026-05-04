# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource endpoints for OpenViking HTTP Server."""

import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, model_validator

from openviking.server.auth import get_request_context
from openviking.server.config import ServerConfig
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import (
    TEMP_FILE_ID_RE,
    _is_safe_namespace_component,
    require_remote_resource_source,
    resolve_uploaded_temp_file_id,
)
from openviking.server.responses import response_from_result
from openviking.server.telemetry import run_operation
from openviking.server.upload_token_store import UploadTokenError, upload_token_store
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


def _resolve_temp_or_path(
    *,
    path: Optional[str],
    temp_file_id: Optional[str],
    upload_temp_dir: Path,
    account_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> tuple[str, bool, Optional[str]]:
    """Resolve add_resource's source argument to a concrete path string.

    Returns (resolved_path, allow_local_path_resolution, original_filename). Raises
    InvalidArgumentError when neither argument is supplied.
    """
    if temp_file_id:
        resolved, original = resolve_uploaded_temp_file_id(
            temp_file_id,
            upload_temp_dir,
            account_id=account_id,
            user_id=user_id,
        )
        return resolved, True, original
    if path is None:
        raise InvalidArgumentError("Either 'path' or 'temp_file_id' must be provided.")
    return require_remote_resource_source(path), False, None


def _cleanup_temp_files(temp_dir: Path, max_age_hours: int = 1):
    """Clean up temporary files older than max_age_hours.

    Recurses into per-tenant subdirectories produced by the signed-upload route
    (``{temp_dir}/{account_id}/{user_id}/{temp_file_id}``) as well as the legacy
    flat layout used by ``POST /api/v1/resources/temp_upload``.
    """
    if not temp_dir.exists():
        return

    now = time.time()
    max_age_seconds = max_age_hours * 3600

    for file_path in temp_dir.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            file_age = now - file_path.stat().st_mtime
        except OSError:
            continue
        if file_age <= max_age_seconds:
            continue
        file_path.unlink(missing_ok=True)
        if not file_path.name.endswith(".ov_upload.meta"):
            meta_path = file_path.parent / f"{file_path.name}.ov_upload.meta"
            if meta_path.exists():
                meta_path.unlink(missing_ok=True)


@router.post("/resources/temp_upload")
async def temp_upload(
    file: UploadFile = File(...),
    telemetry: bool = Form(False),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Upload a temporary file for add_resource or import_ovpack."""

    async def _upload() -> dict[str, str]:
        import json

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

        # Save metadata with original filename
        if file.filename:
            meta_path = temp_dir / f"{temp_filename}.ov_upload.meta"
            meta = {
                "original_filename": file.filename,
                "upload_time": time.time(),
            }
            with open(meta_path, "w") as f:
                json.dump(meta, f)

        return {"temp_file_id": temp_filename}

    execution = await run_operation(
        operation="resources.temp_upload",
        telemetry=telemetry,
        fn=_upload,
    )
    return response_from_result(execution.result, telemetry=execution.telemetry)


_DEFAULT_UPLOAD_MAX_BYTES = 100 * 1024 * 1024


def _resolve_upload_max_bytes(request: Request) -> int:
    config = getattr(request.app.state, "config", None)
    if isinstance(config, ServerConfig):
        return config.upload_signed_max_bytes
    return _DEFAULT_UPLOAD_MAX_BYTES


@router.post("/resources/temp_upload_signed")
async def temp_upload_signed(
    request: Request,
    file: UploadFile = File(...),
    token: str = Query(..., min_length=1),
    temp_file_id: str = Query(..., min_length=1),
):
    """Upload via short-lived signed token. Used by the MCP progressive-upload flow.

    No identity headers required — the token (issued by ``add_resource`` MCP for local-file
    paths) carries the bound (account_id, user_id, temp_file_id). The token is consumed on
    first use; subsequent attempts return 401.
    """
    import json

    if not TEMP_FILE_ID_RE.match(temp_file_id):
        raise HTTPException(status_code=400, detail="invalid temp_file_id")

    max_bytes = _resolve_upload_max_bytes(request)
    content_length_hdr = request.headers.get("content-length")
    if content_length_hdr is not None:
        try:
            if int(content_length_hdr) > max_bytes:
                raise HTTPException(status_code=413, detail="upload exceeds max_bytes")
        except ValueError:
            pass  # malformed header — let the streaming check catch oversize

    try:
        account_id, user_id = upload_token_store.consume(token, temp_file_id)
    except UploadTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # Defense-in-depth: tokens currently only carry server-controlled identity, but
    # validate anyway so a future code path that mints tokens from less-trusted input
    # cannot escape the per-tenant directory.
    if not (_is_safe_namespace_component(account_id) and _is_safe_namespace_component(user_id)):
        raise HTTPException(status_code=400, detail="invalid namespace component")

    upload_temp_dir = get_openviking_config().storage.get_upload_temp_dir()
    target_dir = upload_temp_dir / account_id / user_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / temp_file_id

    bytes_written = 0
    success = False
    try:
        with open(target_path, "wb") as out:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    raise HTTPException(status_code=413, detail="upload exceeds max_bytes")
                out.write(chunk)
        success = True
    finally:
        if not success:
            target_path.unlink(missing_ok=True)

    if file.filename:
        meta_path = target_dir / f"{temp_file_id}.ov_upload.meta"
        meta = {"original_filename": file.filename, "upload_time": time.time()}
        with open(meta_path, "w") as meta_f:
            json.dump(meta, meta_f)

    # Scope cleanup to this tenant's subdir to bound the scan; the legacy flat
    # /temp_upload route still cleans the root level on its own calls.
    _cleanup_temp_files(target_dir)
    return {"temp_file_id": temp_file_id}


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
    path, allow_local_path_resolution, original_filename = _resolve_temp_or_path(
        path=request.path,
        temp_file_id=request.temp_file_id,
        upload_temp_dir=upload_temp_dir,
        account_id=_ctx.user.account_id,
        user_id=_ctx.user.user_id,
    )

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
    return response_from_result(execution.result, telemetry=execution.telemetry)


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
        data, _ = resolve_uploaded_temp_file_id(request.temp_file_id, upload_temp_dir)
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
    return response_from_result(execution.result, telemetry=execution.telemetry)
