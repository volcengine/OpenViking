# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Console BFF endpoints for usage and audit data."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from openviking.server.auth import require_role
from openviking.server.identity import RequestContext, Role
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/console", tags=["console"])


def _split_multi(values: Optional[list[str]]) -> list[str]:
    if not values:
        return []
    result: list[str] = []
    for value in values:
        result.extend(part.strip() for part in str(value).split(",") if part.strip())
    return result


def _runtime_service(request: Request):
    runtime = getattr(request.app.state, "usage_audit_runtime", None)
    if runtime is None:
        return None
    return runtime.api_service


def _ok_response(result):
    return Response(status="ok", result=result).model_dump(exclude_none=True)


def _disabled_response():
    return _ok_response(
        {
            "enabled": False,
            "message": "Usage/Audit is disabled or not initialized.",
        }
    )


@router.get("/dashboard/summary")
async def dashboard_summary(
    request: Request,
    _ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Return Dashboard top-card data."""
    service = _runtime_service(request)
    if service is None:
        return _disabled_response()
    return _ok_response(await service.dashboard_summary(_ctx))


@router.get("/tokens")
async def token_series(
    request: Request,
    start_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    end_date: str = Query(..., description="End date in YYYY-MM-DD format"),
    bucket: str = Query("day", pattern="^(day)$"),
    _ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Return token usage trend for a date range."""
    service = _runtime_service(request)
    if service is None:
        return _disabled_response()
    result = await service.token_series(
        ctx=_ctx,
        start_date=start_date,
        end_date=end_date,
        bucket=bucket,
    )
    return _ok_response(result)


@router.get("/context-commits")
async def context_commits(
    request: Request,
    start_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    end_date: str = Query(..., description="End date in YYYY-MM-DD format"),
    bucket: str = Query("hour", pattern="^(hour|4h)$"),
    _ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Return context write heatmap rows for a date range."""
    service = _runtime_service(request)
    if service is None:
        return _disabled_response()
    result = await service.context_commits(
        ctx=_ctx,
        start_date=start_date,
        end_date=end_date,
        bucket=bucket,
    )
    return _ok_response(result)


@router.get("/audit")
async def audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    request_id: Optional[str] = Query(None),
    status: Optional[list[str]] = Query(None),
    api_type: Optional[list[str]] = Query(None),
    _ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Return filtered request audit logs."""
    service = _runtime_service(request)
    if service is None:
        return _disabled_response()
    result = await service.audit_logs(
        ctx=_ctx,
        request_id=request_id,
        statuses=_split_multi(status),
        api_types=_split_multi(api_type),
        page=page,
        page_size=page_size,
    )
    return _ok_response(result)
