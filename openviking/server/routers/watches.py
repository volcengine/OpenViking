# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Watch management endpoints for OpenViking HTTP Server.

Implements RFC #2104 (Watch Management API) on the REST control plane.
Routes mirror WatchManager primitives with dual-key support: every
single-resource endpoint accepts either path parameter ``{task_id}`` or
query parameter ``?to_uri=``. Cross-key conflict returns 400.
"""

from typing import Optional

from fastapi import APIRouter, Body, Depends, Path, Query
from pydantic import BaseModel, ConfigDict

from openviking.resource import watch_manager as wm_mod
from openviking.resource.watch_manager import WatchManager, WatchTask
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)

router = APIRouter(prefix="/api/v1", tags=["watches"])


class UpdateWatchRequest(BaseModel):
    """Partial-update body for PATCH /watches.

    Any field left unset is preserved on the underlying task. ``is_active``
    and ``watch_interval`` are orthogonal: flip ``is_active`` to pause/resume
    without losing the configured cadence.
    """

    model_config = ConfigDict(extra="forbid")

    watch_interval: Optional[float] = None
    is_active: Optional[bool] = None
    reason: Optional[str] = None
    instruction: Optional[str] = None


def _wm() -> WatchManager:
    svc = get_service()
    scheduler = getattr(svc, "watch_scheduler", None)
    if scheduler is None or not scheduler.is_running:
        raise FailedPreconditionError("Watch scheduler not running")
    wm = scheduler.watch_manager
    if wm is None:
        raise FailedPreconditionError("Watch scheduler not running")
    return wm


def _scheduler():
    svc = get_service()
    scheduler = getattr(svc, "watch_scheduler", None)
    if scheduler is None or not scheduler.is_running:
        raise FailedPreconditionError("Watch scheduler not running")
    return scheduler


def _identity(ctx: RequestContext):
    return (ctx.account_id, ctx.user.user_id, ctx.role.value, ctx.user.agent_id)


async def _resolve_task(
    task_id: Optional[str],
    to_uri: Optional[str],
    ctx: RequestContext,
) -> WatchTask:
    """Return the task identified by either task_id (path) or to_uri (query).

    Raises:
        InvalidArgumentError: both keys supplied, or neither supplied.
        NotFoundError: no task matches (or caller lacks visibility).
    """
    if task_id and to_uri:
        raise InvalidArgumentError("Specify either path {task_id} or query ?to_uri=, not both")
    if not task_id and not to_uri:
        raise InvalidArgumentError("Either {task_id} or ?to_uri= is required")

    wm = _wm()
    account_id, user_id, role, agent_id = _identity(ctx)
    if task_id:
        task = await wm.get_task(task_id, account_id, user_id, role, agent_id)
    else:
        task = await wm.get_task_by_uri(to_uri, account_id, user_id, role, agent_id)
    if task is None:
        raise NotFoundError(task_id or to_uri or "", "watch_task")
    return task


def _translate_perm(exc: wm_mod.PermissionDeniedError, target: str) -> PermissionDeniedError:
    """Convert watch_manager's own PermissionDeniedError (plain Exception)
    into the OpenVikingError-rooted one so the global handler renders 403.
    """
    return PermissionDeniedError(str(exc) or "Permission denied", resource=target)


@router.get("/watches")
async def list_or_get_watch(
    active_only: bool = Query(False, description="Only return tasks with is_active=true"),
    to_uri: Optional[str] = Query(None, description="If set, return the single task with this URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """List watch tasks, or look one up by ``to_uri``.

    Without ``to_uri`` returns ``{tasks: [...], total: N}``. With ``to_uri``
    returns the single matching task object (404 if missing).
    """
    wm = _wm()
    account_id, user_id, role, agent_id = _identity(_ctx)
    if to_uri:
        task = await wm.get_task_by_uri(to_uri, account_id, user_id, role, agent_id)
        if task is None:
            raise NotFoundError(to_uri, "watch_task")
        return Response(status="ok", result=task.to_dict())
    tasks = await wm.get_all_tasks(
        account_id, user_id, role, active_only=active_only, agent_id=agent_id
    )
    return Response(
        status="ok", result={"tasks": [t.to_dict() for t in tasks], "total": len(tasks)}
    )


@router.get("/watches/{task_id}")
async def get_watch(
    task_id: str = Path(..., description="Watch task ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get a single watch task by ID."""
    task = await _resolve_task(task_id, None, _ctx)
    return Response(status="ok", result=task.to_dict())


async def _patch_impl(target: WatchTask, body: UpdateWatchRequest, ctx: RequestContext):
    wm = _wm()
    account_id, user_id, role, agent_id = _identity(ctx)
    try:
        updated = await wm.update_task(
            target.task_id,
            account_id,
            user_id,
            role,
            agent_id=agent_id,
            watch_interval=body.watch_interval,
            is_active=body.is_active,
            reason=body.reason,
            instruction=body.instruction,
        )
    except wm_mod.PermissionDeniedError as e:
        raise _translate_perm(e, target.to_uri or target.task_id) from e
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise NotFoundError(target.task_id, "watch_task") from e
        raise InvalidArgumentError(msg) from e
    return Response(status="ok", result=updated.to_dict())


@router.patch("/watches/{task_id}")
async def patch_watch_by_id(
    task_id: str = Path(..., description="Watch task ID"),
    body: UpdateWatchRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Partial update by task_id. Fields left null are preserved."""
    task = await _resolve_task(task_id, None, _ctx)
    return await _patch_impl(task, body, _ctx)


@router.patch("/watches")
async def patch_watch_by_uri(
    to_uri: str = Query(..., description="Target URI of the watch task"),
    body: UpdateWatchRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Partial update by to_uri (query parameter)."""
    task = await _resolve_task(None, to_uri, _ctx)
    return await _patch_impl(task, body, _ctx)


async def _delete_impl(target: WatchTask, ctx: RequestContext):
    wm = _wm()
    account_id, user_id, role, agent_id = _identity(ctx)
    try:
        ok = await wm.delete_task(target.task_id, account_id, user_id, role, agent_id)
    except wm_mod.PermissionDeniedError as e:
        raise _translate_perm(e, target.to_uri or target.task_id) from e
    if not ok:
        raise NotFoundError(target.task_id, "watch_task")
    return Response(
        status="ok",
        result={"task_id": target.task_id, "to_uri": target.to_uri, "deleted": True},
    )


@router.delete("/watches/{task_id}")
async def delete_watch_by_id(
    task_id: str = Path(..., description="Watch task ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Delete a watch task by ID."""
    task = await _resolve_task(task_id, None, _ctx)
    return await _delete_impl(task, _ctx)


@router.delete("/watches")
async def delete_watch_by_uri(
    to_uri: str = Query(..., description="Target URI of the watch task"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Delete a watch task by to_uri."""
    task = await _resolve_task(None, to_uri, _ctx)
    return await _delete_impl(task, _ctx)


async def _trigger_impl(target: WatchTask):
    scheduler = _scheduler()
    ok = await scheduler.schedule_task(target.task_id)
    return Response(
        status="ok",
        result={"task_id": target.task_id, "to_uri": target.to_uri, "scheduled": ok},
    )


@router.post("/watches/{task_id}/trigger")
async def trigger_watch_by_id(
    task_id: str = Path(..., description="Watch task ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Immediately schedule the watch task for execution.

    Returns ``scheduled=false`` if the task is already running or unknown to
    the scheduler. Does not wait for completion.
    """
    task = await _resolve_task(task_id, None, _ctx)
    return await _trigger_impl(task)


@router.post("/watches/trigger")
async def trigger_watch_by_uri(
    to_uri: str = Query(..., description="Target URI of the watch task"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Trigger by to_uri."""
    task = await _resolve_task(None, to_uri, _ctx)
    return await _trigger_impl(task)
