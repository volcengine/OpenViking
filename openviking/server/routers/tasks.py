# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Task tracking endpoints for OpenViking HTTP Server.

Provides observability for background operations (e.g. session commit
with ``wait=false``).  Callers receive a ``task_id`` and can poll these
endpoints to check completion, results, or errors.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from openviking.server.models import Response
from openviking.service.task_tracker import get_task_tracker

router = APIRouter(prefix="/api/v1", tags=["tasks"])


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get the status of a single background task."""
    tracker = get_task_tracker()
    task = tracker.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or expired")
    return Response(status="ok", result=task.to_dict())


@router.get("/tasks")
async def list_tasks(
    task_type: Optional[str] = Query(None, description="Filter by task type (e.g. session_commit)"),
    status: Optional[str] = Query(
        None, description="Filter by status (pending/running/completed/failed)"
    ),
    resource_id: Optional[str] = Query(None, description="Filter by resource ID (e.g. session_id)"),
    limit: int = Query(50, le=200, description="Max results"),
):
    """List background tasks with optional filters."""
    tracker = get_task_tracker()
    tasks = tracker.list_tasks(
        task_type=task_type,
        status=status,
        resource_id=resource_id,
        limit=limit,
    )
    return Response(status="ok", result=[t.to_dict() for t in tasks])
