# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""External task queue endpoints for OpenViking HTTP Server."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.service.open_task_queue import OpenTaskQueueService, open_task_to_dict
from openviking_cli.exceptions import FailedPreconditionError

router = APIRouter(prefix="/api/v1/task-queues/compile", tags=["task-queues"])


class CompileOpenTaskRequest(BaseModel):
    """Task payload accepted by the open compile queue."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: list[str] = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    reason: Optional[str] = None
    runtime_timeout_seconds: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )


class LeasedTaskRequest(BaseModel):
    """Task owner plus the active QueueFS lease."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    lease_id: str = Field(min_length=1)


class TaskUpdateRequest(LeasedTaskRequest):
    """Progress update from the worker that owns the active lease."""

    stage: Optional[str] = Field(default=None, min_length=1)
    progress: Optional[float] = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    message: Optional[str] = None
    details: Optional[dict[str, Any]] = None


class CompleteTaskRequest(LeasedTaskRequest):
    """Terminal success update from the worker that owns the active lease."""

    result: dict[str, Any] = Field(default_factory=dict)


class FailTaskError(BaseModel):
    """Terminal failure payload from an external worker."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class FailTaskRequest(LeasedTaskRequest):
    """Terminal failure update from the worker that owns the active lease."""

    error: FailTaskError


class AckTaskRequest(LeasedTaskRequest):
    """Worker acknowledgement for a terminal task."""


def _open_task_queue_service() -> OpenTaskQueueService:
    service = get_service()
    viking_fs = getattr(service, "viking_fs", None)
    agfs = getattr(viking_fs, "agfs", None) if viking_fs is not None else None
    if agfs is None:
        raise FailedPreconditionError("Open task queue requires initialized storage")
    return OpenTaskQueueService(agfs)


@router.post("/tasks")
async def create_compile_task(
    request: CompileOpenTaskRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Create a compile task for the caller and enqueue it to the shared queue."""
    service = _open_task_queue_service()
    task = await service.create_compile_task(
        account_id=_ctx.account_id,
        user_id=_ctx.user.user_id,
        payload=request.model_dump(by_alias=True, exclude_none=True),
    )
    return Response(status="ok", result=open_task_to_dict(task))


@router.post("/claim")
async def claim_compile_task(
    _ctx: RequestContext = Depends(get_request_context),
):
    """Claim the oldest available open compile task from the shared QueueFS queue."""
    service = _open_task_queue_service()
    task = await service.claim_compile_task(
        worker_account_id=_ctx.account_id,
        worker_user_id=_ctx.user.user_id,
    )
    return Response(
        status="ok",
        result=None if task is None else open_task_to_dict(task, include_lease_id=True),
    )


@router.patch("/tasks/{task_id}")
async def update_compile_task(
    task_id: str,
    request: TaskUpdateRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Update progress for the task lease owner and renew the lease."""
    service = _open_task_queue_service()
    updates = request.model_dump(
        exclude={"account_id", "user_id", "lease_id"},
        exclude_unset=True,
        exclude_none=True,
    )
    task = await service.update_task(
        account_id=request.account_id,
        user_id=request.user_id,
        task_id=task_id,
        lease_id=request.lease_id,
        updates=updates,
    )
    return Response(status="ok", result=open_task_to_dict(task, include_lease_id=True))


@router.post("/tasks/{task_id}/complete")
async def complete_compile_task(
    task_id: str,
    request: CompleteTaskRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Mark a leased open compile task completed."""
    service = _open_task_queue_service()
    task = await service.complete_task(
        account_id=request.account_id,
        user_id=request.user_id,
        task_id=task_id,
        lease_id=request.lease_id,
        result=request.result,
    )
    return Response(status="ok", result=open_task_to_dict(task, include_lease_id=True))


@router.post("/tasks/{task_id}/fail")
async def fail_compile_task(
    task_id: str,
    request: FailTaskRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Mark a leased open compile task failed."""
    service = _open_task_queue_service()
    task = await service.fail_task(
        account_id=request.account_id,
        user_id=request.user_id,
        task_id=task_id,
        lease_id=request.lease_id,
        error=request.error.model_dump(),
    )
    return Response(status="ok", result=open_task_to_dict(task, include_lease_id=True))


@router.post("/tasks/{task_id}/ack")
async def ack_compile_task(
    task_id: str,
    request: AckTaskRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Acknowledge a terminal open compile task without deleting its record."""
    service = _open_task_queue_service()
    task = await service.ack_task(
        account_id=request.account_id,
        user_id=request.user_id,
        task_id=task_id,
        lease_id=request.lease_id,
        ack_by_account_id=_ctx.account_id,
        ack_by_user_id=_ctx.user.user_id,
    )
    return Response(status="ok", result=open_task_to_dict(task, include_lease_id=True))
