# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Watch management endpoints for OpenViking HTTP Server.

Implements RFC #2104 (Watch Management API) on the REST control plane.
Routes mirror WatchManager primitives with dual-key support: every
single-resource endpoint accepts either path parameter ``{task_id}`` or
query parameter ``?to_uri=``. Cross-key conflict returns 400.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Body, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, field_validator

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

# Strong refs for in-flight fire-and-forget trigger tasks. Python asyncio
# only keeps weak references to tasks created via asyncio.create_task, so a
# task without an external strong reference can be garbage-collected
# mid-execution and silently aborted. We hold each task here until it
# completes and discard via done_callback. See
# https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
_BACKGROUND_TRIGGER_TASKS: set[asyncio.Task] = set()


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

    @field_validator("watch_interval")
    @classmethod
    def _interval_must_be_positive(cls, value: Optional[float]) -> Optional[float]:
        # `None` means "leave unchanged" — only validate when the caller
        # actually supplied a value. Mirrors the CLI/MCP boundary checks so
        # the three control planes agree on "non-positive is invalid".
        # Without this, PATCH would persist `watch_interval <= 0`, which then
        # makes a later `is_active=true` resume fail inside update_task with
        # ValueError and surface as 404.
        if value is not None and value <= 0:
            raise ValueError(
                "watch_interval must be > 0 (use is_active=false to pause "
                "without losing the cadence)"
            )
        return value


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
    return (ctx.account_id, ctx.user.user_id, str(ctx.role))


async def _resolve_task(
    task_id: Optional[str],
    to_uri: Optional[str],
    ctx: RequestContext,
) -> WatchTask:
    """Return the task identified by either task_id (path) or to_uri (query).

    When both keys are given they must refer to the same task — this enables
    callers to double-key cross-validate. Rejection happens only when the
    two keys disagree, not just because both are present.

    Raises:
        InvalidArgumentError: neither key supplied, or both supplied but they
            resolve to different tasks.
        NotFoundError: no task matches (or caller lacks visibility).
    """
    if not task_id and not to_uri:
        raise InvalidArgumentError("Either {task_id} or ?to_uri= is required")

    wm = _wm()
    account_id, user_id, role = _identity(ctx)
    if task_id:
        task = await wm.get_task(task_id, account_id, user_id, role)
    else:
        task = await wm.get_task_by_uri(to_uri, account_id, user_id, role)
    # `wm.get_task*` return None for both "task does not exist" and "task
    # exists but the caller fails the permission check" (watch_manager.py
    # `_check_permission`). Collapsing both into 404 is deliberate: it avoids
    # leaking the existence of another tenant's task to an unauthorized
    # caller. The trade-off is that callers see 404 instead of 403 on a
    # cross-tenant access attempt, which matches the security-first stance
    # used elsewhere in OpenViking.
    if task is None:
        raise NotFoundError(task_id or to_uri or "", "watch_task")
    if task_id and to_uri and task.to_uri != to_uri:
        raise InvalidArgumentError(
            f"task_id {task_id} maps to to_uri={task.to_uri!r}, not {to_uri!r}"
        )
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
    returns the single matching task object (404 if missing). When both
    ``to_uri`` and ``active_only=true`` are supplied, a paused task at that
    URI still 404s — the active-only filter stays consistent in both modes.
    """
    wm = _wm()
    account_id, user_id, role = _identity(_ctx)
    if to_uri:
        task = await wm.get_task_by_uri(to_uri, account_id, user_id, role)
        if task is None or (active_only and not task.is_active):
            raise NotFoundError(to_uri, "watch_task")
        return Response(status="ok", result=task.to_dict())
    tasks = await wm.get_all_tasks(account_id, user_id, role, active_only=active_only)
    return Response(
        status="ok", result={"tasks": [t.to_dict() for t in tasks], "total": len(tasks)}
    )


@router.get("/watches/{task_id}")
async def get_watch(
    task_id: str = Path(..., description="Watch task ID"),
    to_uri: Optional[str] = Query(
        None,
        description=(
            "Optional cross-key sanity check. If supplied, must equal the "
            "task's current `to_uri`; otherwise the request is rejected with "
            "400. Useful when the caller has both pieces of information and "
            "wants to guard against acting on a stale task_id."
        ),
    ),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get a single watch task by ID."""
    task = await _resolve_task(task_id, to_uri, _ctx)
    return Response(status="ok", result=task.to_dict())


async def _patch_impl(target: WatchTask, body: UpdateWatchRequest, ctx: RequestContext):
    """Forward to WatchManager.update_task.

    Exception translation: `watch_manager.PermissionDeniedError` is a plain
    Exception (not an OpenVikingError) so the global handler would not turn it
    into a 403 — we translate it explicitly. Any other watch_manager exception
    that already inherits from OpenVikingError (e.g. ConflictError) bubbles
    untouched and the global handler maps it to the right HTTP status.

    `ValueError` from update_task is currently only used for "task not found"
    after the inner lock acquires, which is a race window past the
    `_resolve_task` pre-check (e.g. another caller deleted the task). We map
    it to 404 to stay consistent with the pre-check behavior.
    """
    wm = _wm()
    account_id, user_id, role = _identity(ctx)
    try:
        updated = await wm.update_task(
            target.task_id,
            account_id,
            user_id,
            role,
            watch_interval=body.watch_interval,
            is_active=body.is_active,
            reason=body.reason,
            instruction=body.instruction,
        )
    except wm_mod.PermissionDeniedError as e:
        raise _translate_perm(e, target.to_uri or target.task_id) from e
    except ValueError as e:
        raise NotFoundError(target.task_id, "watch_task") from e
    return Response(status="ok", result=updated.to_dict())


@router.patch("/watches/{task_id}")
async def patch_watch_by_id(
    task_id: str = Path(..., description="Watch task ID"),
    to_uri: Optional[str] = Query(
        None,
        description=(
            "Optional cross-key sanity check. If supplied, must equal the "
            "task's current `to_uri`; otherwise the request is rejected with "
            "400. Useful when the caller has both pieces of information and "
            "wants to guard against acting on a stale task_id — but a wrong "
            "value here will block DELETE/PATCH/trigger on an otherwise valid "
            "task, so omit it unless the cross-check is desired."
        ),
    ),
    body: UpdateWatchRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Partial update by task_id. Fields left null are preserved."""
    task = await _resolve_task(task_id, to_uri, _ctx)
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
    account_id, user_id, role = _identity(ctx)
    try:
        ok = await wm.delete_task(target.task_id, account_id, user_id, role)
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
    to_uri: Optional[str] = Query(
        None,
        description=(
            "Optional cross-key sanity check. If supplied, must equal the "
            "task's current `to_uri`; otherwise the request is rejected with "
            "400. Useful when the caller has both pieces of information and "
            "wants to guard against acting on a stale task_id — but a wrong "
            "value here will block DELETE/PATCH/trigger on an otherwise valid "
            "task, so omit it unless the cross-check is desired."
        ),
    ),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Delete a watch task by ID."""
    task = await _resolve_task(task_id, to_uri, _ctx)
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
    """Dispatch a scheduling request without waiting for execution.

    `WatchScheduler.schedule_task` awaits `_execute_task` inline, which runs
    the full re-ingest (re-fetch + re-parse + re-embed) and can take many
    seconds. We don't want HTTP requests blocked for that long, so the call
    is fired off via `asyncio.create_task` and the response returns
    immediately with ``scheduled=true``. Actual success/failure is observable
    via subsequent `GET /watches/{task_id}` (last_execution_time updates).
    """
    scheduler = _scheduler()
    task = asyncio.create_task(scheduler.schedule_task(target.task_id))
    _BACKGROUND_TRIGGER_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TRIGGER_TASKS.discard)
    return Response(
        status="ok",
        result={"task_id": target.task_id, "to_uri": target.to_uri, "scheduled": True},
    )


@router.post("/watches/{task_id}/trigger")
async def trigger_watch_by_id(
    task_id: str = Path(..., description="Watch task ID"),
    to_uri: Optional[str] = Query(
        None,
        description=(
            "Optional cross-key sanity check. If supplied, must equal the "
            "task's current `to_uri`; otherwise the request is rejected with "
            "400. Useful when the caller has both pieces of information and "
            "wants to guard against acting on a stale task_id — but a wrong "
            "value here will block DELETE/PATCH/trigger on an otherwise valid "
            "task, so omit it unless the cross-check is desired."
        ),
    ),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Immediately schedule the watch task for execution (fire-and-forget).

    Dispatches the scheduling request asynchronously and returns right away;
    the actual re-fetch runs in the background. Poll
    ``GET /watches/{task_id}.last_execution_time`` to confirm completion.
    """
    task = await _resolve_task(task_id, to_uri, _ctx)
    return await _trigger_impl(task)


@router.post("/watches/trigger")
async def trigger_watch_by_uri(
    to_uri: str = Query(..., description="Target URI of the watch task"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Trigger by to_uri (fire-and-forget; see by-id variant for semantics)."""
    task = await _resolve_task(None, to_uri, _ctx)
    return await _trigger_impl(task)
