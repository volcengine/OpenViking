# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Maintenance endpoints for OpenViking HTTP Server."""

import asyncio
from typing import List, Optional

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field

from openviking.maintenance.memory_consolidator import DEFAULT_CANARY_LIMIT
from openviking.server.auth import require_role
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext, Role
from openviking.server.models import ErrorInfo, Response
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

    uri = body.uri
    viking_fs = get_viking_fs()

    # Validate URI exists
    if not await viking_fs.exists(uri, ctx=ctx):
        return Response(
            status="error",
            error=ErrorInfo(code="NOT_FOUND", message=f"URI not found: {uri}"),
        )

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
            return Response(
                status="error",
                error=ErrorInfo(
                    code="CONFLICT",
                    message=f"URI {uri} already has a reindex in progress",
                ),
            )
        result = await _do_reindex(service, uri, body.regenerate, ctx)
        return Response(status="ok", result=result)
    else:
        # Async path: run in background, return task_id for polling
        task = tracker.create_if_no_running(
            REINDEX_TASK_TYPE,
            uri,
            owner_account_id=ctx.account_id,
            owner_user_id=ctx.user.user_id,
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


async def _do_reindex_locked(
    service,
    uri: str,
    regenerate: bool,
    ctx: RequestContext,
) -> dict:
    """Execute reindex assuming the path lock is already held by the caller.

    Callers that already hold a LockContext on the URI's path (e.g.
    MemoryConsolidator under its own scope lock) should call this directly
    to avoid deadlocking on a non-reentrant LockContext re-acquire.
    """
    if regenerate:
        return await service.resources.summarize([uri], ctx=ctx)
    return await service.resources.build_index([uri], ctx=ctx)


async def _do_reindex(
    service,
    uri: str,
    regenerate: bool,
    ctx: RequestContext,
) -> dict:
    """Acquire a point lock on the URI's path, then run reindex."""
    from openviking.storage.transaction import LockContext, get_lock_manager

    viking_fs = service.viking_fs
    path = viking_fs._uri_to_path(uri, ctx=ctx)

    async with LockContext(get_lock_manager(), [path], lock_mode="point"):
        return await _do_reindex_locked(service, uri, regenerate, ctx)


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


# ---------- Memory consolidation (Phase C + D) ----------

CONSOLIDATE_TASK_TYPE = "memory_consolidation"


class CanarySpec(BaseModel):
    """One canary entry on the consolidate request.

    top_n is the per-canary sensitivity knob. Set to 1 for strict
    canaries that must remain at position 0 post-consolidation; larger
    values allow the expected URI to live anywhere in top-N.
    """

    query: str
    expected_top_uri: str
    top_n: int = Field(default=DEFAULT_CANARY_LIMIT, ge=1)


class ConsolidateRequest(BaseModel):
    """Request to consolidate memories under a scope URI."""

    uri: str
    dry_run: bool = False
    wait: bool = True
    canaries: Optional[List[CanarySpec]] = None


def _build_consolidator(service, ctx: RequestContext):
    """Construct a MemoryConsolidator wired to the live service."""
    from openviking.maintenance import MemoryConsolidator
    from openviking.session.memory_archiver import MemoryArchiver
    from openviking.session.memory_deduplicator import MemoryDeduplicator
    from openviking.storage import VikingDBManagerProxy

    viking_fs = service.viking_fs
    vikingdb = VikingDBManagerProxy(service.vikingdb_manager, ctx)
    dedup = MemoryDeduplicator(vikingdb)
    archiver = MemoryArchiver(viking_fs=viking_fs, storage=vikingdb)
    return MemoryConsolidator(
        vikingdb=vikingdb,
        viking_fs=viking_fs,
        dedup=dedup,
        archiver=archiver,
        service=service,
    )


@router.post("/consolidate")
async def consolidate(
    request: ConsolidateRequest = Body(...),
    _ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Consolidate memories under a scope URI.

    Runs the dream-style janitor pass: cluster duplicates, LLM-merge,
    archive cold entries, refresh overview. dry_run=true returns the
    plan without writes. wait=false enqueues and returns a task_id for
    polling via the task API. Optional canaries run pre/post and set
    canary_failed=true on hard regression.
    """
    from openviking.service.task_tracker import get_task_tracker
    from openviking.storage.viking_fs import get_viking_fs

    uri = request.uri
    viking_fs = get_viking_fs()

    if not await viking_fs.exists(uri, ctx=_ctx):
        return Response(
            status="error",
            error=ErrorInfo(code="NOT_FOUND", message=f"URI not found: {uri}"),
        )

    service = get_service()
    tracker = get_task_tracker()

    if request.wait:
        if tracker.has_running(
            CONSOLIDATE_TASK_TYPE,
            uri,
            owner_account_id=_ctx.account_id,
            owner_user_id=_ctx.user.user_id,
        ):
            return Response(
                status="error",
                error=ErrorInfo(
                    code="CONFLICT",
                    message=f"URI {uri} already has a consolidation in progress",
                ),
            )
        consolidator = _build_consolidator(service, _ctx)
        result = await consolidator.run(
            uri,
            _ctx,
            dry_run=request.dry_run,
            canaries=_canaries_from_request(request.canaries),
        )
        return Response(status="ok", result=_consolidation_payload(result))

    task = tracker.create_if_no_running(
        CONSOLIDATE_TASK_TYPE,
        uri,
        owner_account_id=_ctx.account_id,
        owner_user_id=_ctx.user.user_id,
    )
    if task is None:
        return Response(
            status="error",
            error=ErrorInfo(
                code="CONFLICT",
                message=f"URI {uri} already has a consolidation in progress",
            ),
        )
    asyncio.create_task(
        _background_consolidate_tracked(
            service,
            uri,
            request.dry_run,
            _ctx,
            task.task_id,
            _canaries_from_request(request.canaries),
        )
    )
    return Response(
        status="ok",
        result={
            "uri": uri,
            "status": "accepted",
            "task_id": task.task_id,
            "message": "Consolidation is processing in the background",
            "dry_run": request.dry_run,
        },
    )


@router.get("/consolidate/runs")
async def list_consolidate_runs(
    scope: str,
    limit: int = 20,
    _ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """List recent consolidation audit records for a scope.

    Audit records live at
    viking://agent/<account>/maintenance/consolidation_runs/<scope_hash>/<iso>.json
    written by MemoryConsolidator._record. Returned in reverse
    chronological order, capped at 100.
    """
    from openviking.maintenance import MemoryConsolidator
    from openviking.storage.viking_fs import get_viking_fs

    viking_fs = get_viking_fs()
    audit_dir = MemoryConsolidator.audit_dir_for(_ctx, scope)

    try:
        entries = await viking_fs.ls(audit_dir, ctx=_ctx)
    except Exception:
        return Response(status="ok", result={"scope": scope, "runs": []})

    # viking_fs.ls returns List[Dict] with a 'uri' key per entry, not bare
    # strings. Extract the URI and filter to .json audit files.
    file_uris = []
    for entry in entries:
        if isinstance(entry, dict):
            uri = entry.get("uri", "")
            is_dir = entry.get("isDir", False)
        else:
            uri = str(entry)
            is_dir = False
        if not uri or is_dir or not uri.endswith(".json"):
            continue
        file_uris.append(uri)

    file_uris.sort(reverse=True)
    capped_limit = min(max(0, limit), 100)
    file_uris = file_uris[:capped_limit]

    runs = []
    for run_uri in file_uris:
        try:
            body_text = await viking_fs.read(run_uri, ctx=_ctx)
            if isinstance(body_text, bytes):
                body_text = body_text.decode("utf-8", errors="replace")
            runs.append({"uri": run_uri, "body": body_text})
        except Exception as e:
            runs.append({"uri": run_uri, "error": str(e)})

    return Response(status="ok", result={"scope": scope, "runs": runs})


async def _background_consolidate_tracked(
    service,
    uri: str,
    dry_run: bool,
    ctx: RequestContext,
    task_id: str,
    canaries=None,
) -> None:
    """Run consolidation in background with task tracking."""
    from openviking.service.task_tracker import get_task_tracker

    tracker = get_task_tracker()
    tracker.start(task_id)
    try:
        consolidator = _build_consolidator(service, ctx)
        result = await consolidator.run(uri, ctx, dry_run=dry_run, canaries=canaries)
        tracker.complete(task_id, _consolidation_payload(result))
        logger.info("Background consolidation completed: uri=%s task=%s", uri, task_id)
    except Exception as exc:
        tracker.fail(task_id, str(exc))
        logger.exception("Background consolidation failed: uri=%s task=%s", uri, task_id)


def _consolidation_payload(result) -> dict:
    """Project ConsolidationResult into a JSON-safe dict for HTTP."""
    from dataclasses import asdict

    return asdict(result)


def _canaries_from_request(specs):
    """Translate request CanarySpec entries into Canary domain objects.

    CanarySpec.top_n is already validated (ge=1) by Pydantic at the
    HTTP boundary, so no defensive clamping needed here.
    """
    if not specs:
        return None
    from openviking.maintenance import Canary

    return [
        Canary(
            query=s.query,
            expected_top_uri=s.expected_top_uri,
            top_n=s.top_n,
        )
        for s in specs
    ]
