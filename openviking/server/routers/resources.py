# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource endpoints for OpenViking HTTP Server."""

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_content_target_uri
from openviking.resource.processing_mode import DEFAULT_PROCESSING_MODE, ProcessingMode
from openviking.server.auth import get_request_context, get_upload_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import require_remote_resource_source
from openviking.server.resource_ingest import ingest_temp_upload
from openviking.server.responses import response_from_result
from openviking.server.skill_source_metadata import persist_skill_source_metadata
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
        add_type: Explicit Connector source type (e.g. "tos", "git"). When set, the
            request routes to the Connector integration: the type must be enabled in
            connector.allowed_add_types, and args are forwarded to the source plugin
            (credentials under args.auth_config). Never degrades to the standard
            pipeline. Requires 'path' and an exact 'to' target; cannot be combined
            with 'temp_file_id' or 'parent'.
        to: Target URI for the resource (e.g., "viking://resources/my_resource").
            Required when add_type is set. Otherwise, if not specified, an
            auto-generated URI will be used.
        parent: Parent URI under which the resource will be stored.
            Cannot be used together with 'to' or 'add_type'.
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
        args: Parser-specific import options. Native HTTPS Git imports accept
            {"auth_config": {"username": "oauth2", "token": "..."}}; when
            watch_interval > 0 the credentials are stored in private watch state.
            For Feishu one-time user-token imports,
            pass {"feishu_access_token": "..."}. For Feishu user-token watches,
            pass {"feishu_access_token": "...", "feishu_refresh_token": "..."}.
        watch_interval: Watch interval in minutes for automatic resource monitoring.
            - watch_interval > 0: Creates or updates a watch task. The resource will be
              automatically re-processed at the specified interval.
            - watch_interval = 0: No watch task is created. If a watch task exists for
              this resource, it will be cancelled (deactivated).
            - watch_interval < 0: Same as watch_interval = 0, cancels any existing watch task.
            Default is 0 (no monitoring).

            Note: Re-adding the same source to the same target updates its active watch task.
            A different source targeting an active watch raises ConflictError; cancel that
            watch first with watch_interval <= 0.
    """

    model_config = ConfigDict(extra="forbid")

    path: Optional[str] = None
    temp_file_id: Optional[str] = None
    add_type: Optional[str] = None
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
    args: Dict[str, Any] = Field(default_factory=dict)
    telemetry: TelemetryRequest = False
    watch_interval: float = 0
    processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE
    tags: Optional[list[str]] = None
    tag_mode: str = "replace"

    @model_validator(mode="after")
    def check_path_or_temp_file_id(self):
        if not self.path and not self.temp_file_id:
            raise ValueError("Either 'path' or 'temp_file_id' must be provided")
        return self

    @model_validator(mode="after")
    def check_add_type(self):
        if self.add_type is not None:
            self.add_type = self.add_type.strip() or None
        if self.add_type and self.temp_file_id:
            raise ValueError("'add_type' cannot be combined with 'temp_file_id'")
        if self.add_type and not self.path:
            raise ValueError("'add_type' requires 'path'")
        if self.add_type and self.parent:
            raise ValueError("'add_type' cannot be combined with 'parent'")
        if self.add_type and not self.to:
            raise ValueError("'add_type' requires an exact 'to' target")
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
    source_metadata: Optional[Dict[str, Any]] = None
    target_uri: Optional[str] = None
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
    upload_mode: Optional[Literal["local", "shared"]] = Form(None),
    _ctx: RequestContext = Depends(get_upload_request_context),
):
    """Upload a temporary file for add_resource or import_ovpack.

    Two auth layers (see :func:`get_upload_request_context`): with an API key the file is
    stored and its ``temp_file_id`` returned (used by the CLI and ``import_ovpack``). With a
    signed ``?token=`` — minted by the MCP ``add_resource`` tool for local-file paths — the
    server additionally finishes ingestion in-request: it resolves the upload, calls
    ``add_resource`` with the token-bound ``to``/``reason``, and returns the final result, so
    the agent never needs a second call. The ``?token=`` query param is consumed by the auth
    dependency.
    """
    signed = getattr(request.state, "signed_upload", None)
    effective_upload_mode = upload_mode or request.app.state.config.temp_upload.default_mode

    async def _upload() -> dict[str, Any]:
        store = TempUploadStore.build(request.app.state.config)
        temp_file_id = await store.save_upload(file, effective_upload_mode, _ctx)
        if signed is None:
            return {"temp_file_id": temp_file_id}
        return await ingest_temp_upload(
            store,
            temp_file_id,
            _ctx,
            to=signed.to,
            reason=signed.reason,
            processing_mode=signed.processing_mode,
            tags=signed.tags,
            tag_mode=signed.tag_mode,
            parse_mode=signed.parse_mode,
        )

    try:
        execution = await run_operation(
            operation="resources.temp_upload",
            telemetry=telemetry,
            fn=_upload,
        )
    except InvalidArgumentError as exc:
        if signed is None:
            raise
        # save_upload raises InvalidArgumentError for both bad mode and oversize. The signed
        # route mapped oversize to 413 and the rest to 400 before the routes merged; preserve
        # that contract for the token path.
        msg = str(exc)
        status = 413 if "exceeds size limit" in msg else 400
        raise HTTPException(status_code=status, detail=msg) from exc
    return response_from_result(execution.result, telemetry=execution.telemetry)


@router.post("/resources")
async def add_resource(
    http_request: Request,
    request: AddResourceRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Add resource to OpenViking."""
    service = get_service()
    to_uri = resolve_path_variables(request.to).strip() if request.to else ""
    if to_uri:
        to_uri = validate_content_target_uri(to_uri, _ctx, kind="resource", field_name="to")
    parent_uri = resolve_path_variables(request.parent).strip() if request.parent else ""
    if parent_uri:
        parent_uri = validate_content_target_uri(
            parent_uri,
            _ctx,
            kind="resource",
            field_name="parent",
        )

    path = request.path
    allow_local_path_resolution = False
    original_filename = None
    resolved = None
    if request.temp_file_id:
        if request.watch_interval > 0:
            raise InvalidArgumentError(
                "watch_interval > 0 is not supported for uploaded content: an "
                "upload is a static snapshot, so the watch would re-process "
                "stale content forever. Watch a URL / "
                "sitemap / RSS source instead, or re-add the resource when the "
                "source changes."
            )
        resolved = await TempUploadStore.build(http_request.app.state.config).resolve_for_consume(
            request.temp_file_id, _ctx
        )
        path = resolved.local_path
        original_filename = resolved.original_filename
        allow_local_path_resolution = True
    elif path is not None:
        path = require_remote_resource_source(path, declared_connector_add_type=request.add_type)
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
        "processing_mode": request.processing_mode,
    }
    # Connector routing needs to distinguish an omitted create_parent from an
    # explicit false.  Standard imports still observe false when the field is
    # omitted because ResourceService reads it with kwargs.get(..., False).
    if "create_parent" in request.model_fields_set:
        kwargs["create_parent"] = request.create_parent
    if request.temp_file_id and request.watch_interval <= 0:
        kwargs["temp_file_id"] = request.temp_file_id
    if request.preserve_structure is not None:
        kwargs["preserve_structure"] = request.preserve_structure

    async def _add() -> dict[str, Any]:
        try:
            result = await service.resources.add_resource(
                path=path,
                ctx=_ctx,
                add_type=request.add_type,
                to=to_uri,
                parent=parent_uri,
                reason=request.reason,
                instruction=request.instruction,
                wait=request.wait,
                timeout=request.timeout,
                tags=request.tags,
                tag_mode=request.tag_mode,
                allow_local_path_resolution=allow_local_path_resolution,
                enforce_public_remote_targets=True,
                args=request.args,
                **kwargs,
            )
        except Exception:
            raise
        else:
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
    target_uri = resolve_path_variables(request.target_uri).strip() if request.target_uri else ""
    if target_uri:
        target_uri = validate_content_target_uri(
            target_uri,
            _ctx,
            kind="skill",
            field_name="target_uri",
        )
    data = request.data
    allow_local_path_resolution = False
    resolved = None
    source_metadata = request.source_metadata or {
        "type": "api",
        "source": "inline_content",
        "operation": "add",
    }
    if request.temp_file_id:
        store = TempUploadStore.build(http_request.app.state.config)
        resolved = await store.resolve_for_consume(request.temp_file_id, _ctx)
        data = resolved.local_path
        allow_local_path_resolution = True
        if request.source_metadata is None:
            source_metadata = {
                "type": "api",
                "source": "temp_upload",
                "operation": "add",
                "upload_mode": resolved.mode,
            }
        if resolved.original_filename and request.source_metadata is None:
            source_metadata["original_filename"] = resolved.original_filename

    source_path_hint = resolved.original_filename if resolved else None
    async def _add() -> dict[str, Any]:
        try:
            result = await service.resources.add_skill(
                data=data,
                ctx=_ctx,
                wait=request.wait,
                timeout=request.timeout,
                allow_local_path_resolution=allow_local_path_resolution,
                source_path_hint=source_path_hint,
                target_uri=target_uri,
            )
            await persist_skill_source_metadata(service, _ctx, result, source_metadata)
        except Exception:
            raise
        else:
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
