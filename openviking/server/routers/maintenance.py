# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Maintenance endpoints for OpenViking HTTP Server."""

import asyncio

from fastapi import APIRouter, Body
from pydantic import BaseModel

from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_viking_uri
from openviking.server.auth import require_role
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext, Role
from openviking.server.models import Response
from openviking.server.responses import error_response, response_from_result
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

REINDEX_TASK_TYPE = "resource_reindex"


class ReindexRequest(BaseModel):
    """Request to reindex content at a URI."""

    uri: str
    regenerate: bool = False
    wait: bool = True


router = APIRouter(prefix="/api/v1/maintenance", tags=["maintenance"])


@router.post("/reindex")
async def reindex(
    body: ReindexRequest = Body(...),
    ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
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

    uri = resolve_path_variables(body.uri)
    uri = validate_viking_uri(uri)
    viking_fs = get_viking_fs()

    # Validate URI exists
    if not await viking_fs.exists(uri, ctx=ctx):
        return error_response("NOT_FOUND", f"URI not found: {uri}")

    service = get_service()
    tracker = get_task_tracker()

    if body.wait:
        # Synchronous path: block until reindex completes
        if tracker.has_running(
            REINDEX_TASK_TYPE,
            uri,
            owner_account_id=ctx.account_id,
            owner_user_id=ctx.user.user_id,
        ):
            return error_response("CONFLICT", f"URI {uri} already has a reindex in progress")
        result = await _do_reindex(service, uri, body.regenerate, ctx)
        return response_from_result(result)
    else:
        # Async path: run in background, return task_id for polling
        task = tracker.create_if_no_running(
            REINDEX_TASK_TYPE,
            uri,
            owner_account_id=ctx.account_id,
            owner_user_id=ctx.user.user_id,
        )
        if task is None:
            return error_response("CONFLICT", f"URI {uri} already has a reindex in progress")
        asyncio.create_task(
            _background_reindex_tracked(service, uri, body.regenerate, ctx, task.task_id)
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
