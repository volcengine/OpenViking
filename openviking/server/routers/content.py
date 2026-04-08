# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Content endpoints for OpenViking HTTP Server."""

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import ErrorInfo, Response
from openviking.server.telemetry import run_operation
from openviking.telemetry import TelemetryRequest
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

REINDEX_TASK_TYPE = "resource_reindex"


class ReindexRequest(BaseModel):
    """Request to reindex content at a URI."""

    uri: str
    regenerate: bool = False
    wait: bool = True


class WriteContentRequest(BaseModel):
    """Request to write or append text content to an existing file."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    content: str
    mode: str = "replace"
    wait: bool = False
    timeout: float | None = None
    telemetry: TelemetryRequest = False


router = APIRouter(prefix="/api/v1/content", tags=["content"])


@router.get("/read")
async def read(
    uri: str = Query(..., description="Viking URI"),
    offset: int = Query(0, description="Starting line number (0-indexed)"),
    limit: int = Query(-1, description="Number of lines to read, -1 means read to end"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Read file content (L2)."""
    service = get_service()
    result = await service.fs.read(uri, ctx=_ctx, offset=offset, limit=limit)

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
    result = await service.fs.abstract(uri, ctx=_ctx)
    return Response(status="ok", result=result)


@router.get("/overview")
async def overview(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Read L1 overview."""
    service = get_service()
    result = await service.fs.overview(uri, ctx=_ctx)
    return Response(status="ok", result=result)


@router.get("/download")
async def download(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Download file as raw bytes (for images, binaries, etc.)."""
    service = get_service()
    content = await service.fs.read_file_bytes(uri, ctx=_ctx)

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
    """Write text content to an existing file and refresh semantics/vectors."""
    service = get_service()
    execution = await run_operation(
        operation="content.write",
        telemetry=request.telemetry,
        fn=lambda: service.fs.write(
            uri=request.uri,
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
    request: ReindexRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Reindex content at a URI.

    Re-embeds existing .abstract.md/.overview.md content into the vector
    database. If regenerate=True, also regenerates L0/L1 summaries via LLM
    before re-embedding.

    Uses path locking to prevent concurrent reindexes on the same URI.
    Set wait=False to run in the background and track progress via task API.
    """
    from openviking.service.task_tracker import get_task_tracker
    from openviking.storage.viking_fs import get_viking_fs

    uri = request.uri
    viking_fs = get_viking_fs()

    # Validate URI exists
    if not await viking_fs.exists(uri, ctx=_ctx):
        return Response(
            status="error",
            error=ErrorInfo(code="NOT_FOUND", message=f"URI not found: {uri}"),
        )

    service = get_service()
    tracker = get_task_tracker()

    if request.wait:
        # Synchronous path: block until reindex completes
        if tracker.has_running(
            REINDEX_TASK_TYPE,
            uri,
            owner_account_id=_ctx.account_id,
            owner_user_id=_ctx.user.user_id,
        ):
            return Response(
                status="error",
                error=ErrorInfo(
                    code="CONFLICT",
                    message=f"URI {uri} already has a reindex in progress",
                ),
            )
        result = await _do_reindex(service, uri, request.regenerate, _ctx)
        return Response(status="ok", result=result)
    else:
        # Async path: run in background, return task_id for polling
        task = tracker.create_if_no_running(
            REINDEX_TASK_TYPE,
            uri,
            owner_account_id=_ctx.account_id,
            owner_user_id=_ctx.user.user_id,
        )
        if task is None:
            return Response(
                status="error",
                error=ErrorInfo(
                    code="CONFLICT",
                    message=f"URI {uri} already has a reindex in progress",
                ),
            )
        asyncio.create_task(
            _background_reindex_tracked(service, uri, request.regenerate, _ctx, task.task_id)
        )
        return Response(
            status="ok",
            result={
                "uri": uri,
                "status": "accepted",
                "task_id": task.task_id,
                "message": "Reindex is processing in the background",
            },
        )


async def _do_reindex(
    service,
    uri: str,
    regenerate: bool,
    ctx: RequestContext,
) -> dict:
    """Execute reindex within a lock scope."""
    from openviking.storage.transaction import LockContext, get_lock_manager

    viking_fs = service.viking_fs
    path = viking_fs._uri_to_path(uri, ctx=ctx)

    async with LockContext(get_lock_manager(), [path], lock_mode="point"):
        if regenerate:
            return await service.resources.summarize([uri], ctx=ctx)
        else:
            return await service.resources.build_index([uri], ctx=ctx)


async def _background_reindex_tracked(
    service,
    uri: str,
    regenerate: bool,
    ctx: RequestContext,
    task_id: str,
) -> None:
    """Run reindex in background with task tracking."""
    from openviking.service.task_tracker import get_task_tracker

    tracker = get_task_tracker()
    tracker.start(task_id)
    try:
        result = await _do_reindex(service, uri, regenerate, ctx)
        tracker.complete(task_id, {"uri": uri, **result})
        logger.info("Background reindex completed: uri=%s task=%s", uri, task_id)
    except Exception as exc:
        tracker.fail(task_id, str(exc))
        logger.exception("Background reindex failed: uri=%s task=%s", uri, task_id)
