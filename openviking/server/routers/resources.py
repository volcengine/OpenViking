# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource endpoints for OpenViking HTTP Server."""

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_content_target_uri
from openviking.server.auth import get_request_context, get_upload_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.resource_ingest import (
    AddResourceRequest,
    ingest_resource_request,
    ingest_temp_upload,
)
from openviking.server.responses import response_from_result
from openviking.server.skill_ingest import ingest_temp_upload_skill, install_skills
from openviking.server.telemetry import run_operation
from openviking.server.temp_upload_store import TempUploadStore
from openviking.telemetry import TelemetryRequest
from openviking_cli.exceptions import InvalidArgumentError

router = APIRouter(prefix="/api/v1", tags=["resources"])


class AddSkillRequest(BaseModel):
    """Request model for add_skill.

    Attributes:
        data: Git skill URL, inline skill content, or structured skill data.
            HTTP requests do not treat strings as host filesystem paths.
        temp_file_id: Temporary upload id returned by /api/v1/resources/temp_upload.
        wait: Whether to wait for skill processing to complete.
    """

    model_config = ConfigDict(extra="forbid")

    data: Any = None
    temp_file_id: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    list_only: bool = False
    wait: bool = False
    timeout: float | None = Field(
        default=None,
        deprecated=True,
        description="Deprecated compatibility parameter; accepted and ignored.",
    )
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
    the agent never needs a second call. Tokens minted by the MCP ``add_skill`` tool install
    the upload as skills instead. The ``?token=`` query param is consumed by the auth
    dependency.
    """
    signed = getattr(request.state, "signed_upload", None)
    effective_upload_mode = upload_mode or request.app.state.config.temp_upload.default_mode

    async def _upload() -> dict[str, Any]:
        store = TempUploadStore.build(request.app.state.config)
        temp_file_id = await store.save_upload(file, effective_upload_mode, _ctx)
        if signed is None:
            return {"temp_file_id": temp_file_id}
        if signed.kind == "skill":
            return await ingest_temp_upload_skill(
                store,
                temp_file_id,
                _ctx,
                target_uri=signed.skill_target_uri,
                names=signed.skill_names,
                list_only=signed.list_only,
                source_type="mcp",
            )
        return await ingest_temp_upload(
            store,
            temp_file_id,
            _ctx,
            to=signed.to,
            parent=signed.parent,
            reason=signed.reason,
            processing_mode=signed.processing_mode,
            tags=signed.tags,
            tag_mode=signed.tag_mode,
            parse_mode=signed.parse_mode,
            acl=signed.acl,
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
    """Add resource to OpenViking.

    Native Watches require an unoccupied resolved target and keep it while paused.
    Connector Watches may share targets only with other Connector Watches; repeating
    a source and target creates another independent task. Re-importing never updates
    or resumes a Watch: use PATCH /api/v1/watches/{task_id}, or delete it first.
    URI lookups return 409 for multiple accessible Watches; address them by task_id.
    Connector Watches are visible before the initial import and held by the scheduler
    until that import records its result.
    """
    service = get_service()
    app = getattr(http_request, "app", None)
    state = getattr(app, "state", None)
    config = getattr(state, "config", None)

    async def _add() -> dict[str, Any]:
        return await ingest_resource_request(
            request,
            _ctx,
            resources=service.resources,
            config=config,
            wait=request.wait,
            internal_task=request.internal_task,
        )

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
            return await install_skills(
                data,
                _ctx,
                names=request.skills,
                list_only=request.list_only,
                wait=request.wait,
                target_uri=target_uri,
                source_metadata=source_metadata,
                allow_local_path_resolution=allow_local_path_resolution,
                source_path_hint=source_path_hint,
                telemetry=request.telemetry,
            )
        finally:
            if resolved:
                await resolved.cleanup()

    execution = await run_operation(
        operation="resources.add_skill",
        telemetry=request.telemetry,
        fn=_add,
    )
    return response_from_result(execution.result, telemetry=execution.telemetry)
