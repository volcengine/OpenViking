# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Content endpoints for OpenViking HTTP Server."""

from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict

from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_viking_uri
from openviking.pyagfs.exceptions import AGFSClientError, AGFSNotFoundError
from openviking.server.auth import (
    get_request_context,
    require_role,
)
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext, Role
from openviking.server.models import Response
from openviking.server.telemetry import run_operation
from openviking.telemetry import TelemetryRequest
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class WriteContentRequest(BaseModel):
    """Request to write, append, or create text content to a file."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    content: str
    mode: str = "replace"
    wait: bool = False
    timeout: float | None = None
    telemetry: TelemetryRequest = False


class ReindexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    mode: str = "vectors_only"
    wait: bool = True


router = APIRouter(prefix="/api/v1/content", tags=["content"])


def _validate_reindex_uri(uri: str) -> str:
    raw_uri = uri.strip() if isinstance(uri, str) else ""
    if raw_uri.startswith("viking://"):
        return raw_uri
    return validate_viking_uri(raw_uri)


@router.get("/read")
async def read(
    uri: str = Query(..., description="Viking URI"),
    offset: int = Query(0, description="Starting line number (0-indexed)"),
    limit: int = Query(-1, description="Number of lines to read, -1 means read to end"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Read file content (L2)."""
    service = get_service()
    uri = resolve_path_variables(uri)
    try:
        result = await service.fs.read(uri, ctx=_ctx, offset=offset, limit=limit)
    except AGFSNotFoundError:
        raise NotFoundError(uri, "file")
    except AGFSClientError as e:
        # Fallback for older versions without typed exceptions
        err_msg = str(e).lower()
        if "not found" in err_msg or "no such file or directory" in err_msg:
            raise NotFoundError(uri, "file")
        raise

    # 清理MEMORY_FIELDS隐藏注释（v2记忆加工过程中的临时内部数据，不暴露给外部用户）
    if isinstance(result, bytes):
        text = result.decode("utf-8")
    elif isinstance(result, str):
        text = result
    else:
        text = None

    if text:
        from openviking.session.memory.utils.content import deserialize_content

        result = deserialize_content(text)

    return Response(status="ok", result=result)


@router.get("/abstract")
async def abstract(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Read L0 abstract."""
    service = get_service()
    uri = resolve_path_variables(uri)
    try:
        result = await service.fs.abstract(uri, ctx=_ctx)
    except AGFSNotFoundError:
        raise NotFoundError(uri, "file")
    except AGFSClientError as e:
        # Fallback for older versions without typed exceptions
        err_msg = str(e).lower()
        if "not found" in err_msg or "no such file or directory" in err_msg:
            raise NotFoundError(uri, "file")
        raise
    return Response(status="ok", result=result)


@router.get("/overview")
async def overview(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Read L1 overview."""
    service = get_service()
    uri = resolve_path_variables(uri)
    try:
        result = await service.fs.overview(uri, ctx=_ctx)
    except AGFSNotFoundError:
        raise NotFoundError(uri, "file")
    except AGFSClientError as e:
        # Fallback for older versions without typed exceptions
        err_msg = str(e).lower()
        if "not found" in err_msg or "no such file or directory" in err_msg:
            raise NotFoundError(uri, "file")
        raise
    return Response(status="ok", result=result)


@router.get("/download")
async def download(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Download file as raw bytes (for images, binaries, etc.)."""
    service = get_service()
    uri = resolve_path_variables(uri)
    try:
        content = await service.fs.read_file_bytes(uri, ctx=_ctx)
    except AGFSNotFoundError:
        raise NotFoundError(uri, "file")
    except AGFSClientError as e:
        # Fallback for older versions without typed exceptions
        err_msg = str(e).lower()
        if "not found" in err_msg or "no such file or directory" in err_msg:
            raise NotFoundError(uri, "file")
        raise

    # Try to get filename from stat
    filename = "download"
    try:
        stat = await service.fs.stat(uri, ctx=_ctx)
        if stat and "name" in stat:
            filename = stat["name"]
    except Exception:
        pass
    filename = quote(filename)
    return FastAPIResponse(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/write")
async def write(
    request: WriteContentRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Write text content to a file (replace, append, or create) and refresh semantics/vectors."""
    service = get_service()
    uri = resolve_path_variables(request.uri)
    execution = await run_operation(
        operation="content.write",
        telemetry=request.telemetry,
        fn=lambda: service.fs.write(
            uri=uri,
            content=request.content,
            ctx=_ctx,
            mode=request.mode,
            wait=request.wait,
            timeout=request.timeout,
        ),
    )
    return Response(
        status="ok",
        result=execution.result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


@router.post("/reindex")
async def reindex(
    body: ReindexRequest = Body(...),
    ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Reindex semantic/vector artifacts for a URI-scoped maintenance target."""
    uri = resolve_path_variables(body.uri)
    uri = _validate_reindex_uri(uri)
    service = get_service()
    result = await service.reindex(
        uri=uri,
        mode=body.mode,
        wait=body.wait,
        ctx=ctx,
    )
    return Response(status="ok", result=result)
