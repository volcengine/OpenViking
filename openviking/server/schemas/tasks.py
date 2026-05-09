# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models for the /api/v1/tasks endpoints."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class TaskRecord(BaseModel):
    """Projection of ``TaskRecord.to_dict()`` emitted by the task tracker.

    ``result`` is deeply dynamic (shape depends on the task type producing
    it — session commit returns one shape, resource reindex returns
    another). Kept as ``Dict[str, Any]`` to avoid over-constraining the
    AGFS / memory subsystems; callers branch on ``task_type``.
    """

    model_config = ConfigDict(extra="allow")

    task_id: str
    task_type: str
    status: str  # "pending" | "running" | "completed" | "failed"
    created_at: float
    updated_at: float
    resource_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
