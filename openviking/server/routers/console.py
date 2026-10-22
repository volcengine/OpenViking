# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Console BFF endpoints for usage and audit data."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from openviking.server.auth import require_role
from openviking.server.identity import RequestContext, Role
from openviking.server.models import ErrorResponse, OffsetPage, PageResponse, Response
from openviking.server.pagination import (
    AuditOffsetPagination,
    audit_offset_pagination,
    reject_legacy_pagination_params,
)

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
    timezone: Optional[str] = Query(
        None,
        description="IANA viewer timezone (e.g. Asia/Shanghai). Defaults to server tz.",
    ),
    _ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN, Role.USER),
):
    """Return Dashboard top-card data."""
    service = _runtime_service(request)
    if service is None:
        return _disabled_response()
    return _ok_response(await service.dashboard_summary(_ctx, timezone_name=timezone))


@router.get("/tokens")
async def token_series(
    request: Request,
    start_date: str = Query(..., description="Start date (viewer-local) in YYYY-MM-DD"),
    end_date: str = Query(..., description="End date (viewer-local) in YYYY-MM-DD"),
    bucket: str = Query("day", pattern="^(day)$"),
    timezone: Optional[str] = Query(
        None,
        description="IANA viewer timezone (e.g. Asia/Shanghai). Defaults to server tz.",
    ),
    _ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN, Role.USER),
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
        timezone_name=timezone,
    )
    return _ok_response(result)


@router.get("/context-commits")
async def context_commits(
    request: Request,
    start_date: str = Query(..., description="Start date (viewer-local) in YYYY-MM-DD"),
    end_date: str = Query(..., description="End date (viewer-local) in YYYY-MM-DD"),
    bucket: str = Query("hour", pattern="^(hour|4h)$"),
    timezone: Optional[str] = Query(
        None,
        description="IANA viewer timezone (e.g. Asia/Shanghai). Defaults to server tz.",
    ),
    _ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN, Role.USER),
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
        timezone_name=timezone,
    )
    return _ok_response(result)


class AuditLogItem(BaseModel):
    request_id: str
    account_id: str
    user_id: str | None = None
    method: str
    route: str
    api_type: str
    status_code: int
    duration_ms: float
    error_code: str | None = None
    error_message: str | None = None
    error_details: dict[str, Any] | None = None
    created_at: str


class AuditPage(OffsetPage[AuditLogItem]):
    success_rate: float


@router.get(
    "/audit",
    response_model=PageResponse[AuditPage],
    responses={400: {"model": ErrorResponse}},
)
async def audit_logs(
    request: Request,
    pagination: Annotated[AuditOffsetPagination, Depends(audit_offset_pagination)],
    request_id: Optional[str] = Query(None),
    status: Optional[list[str]] = Query(None),
    api_type: Optional[list[str]] = Query(None),
    _ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN, Role.USER),
):
    """Return filtered request audit logs."""
    reject_legacy_pagination_params(
        request,
        {"page": "offset", "page_size": "limit"},
    )
    service = _runtime_service(request)
    if service is None:
        return PageResponse(
            result=AuditPage(
                items=[],
                offset=pagination.offset,
                limit=pagination.limit,
                has_more=False,
                total=0,
                success_rate=0.0,
            )
        )
    result = await service.audit_logs(
        ctx=_ctx,
        request_id=request_id,
        statuses=_split_multi(status),
        api_types=_split_multi(api_type),
        offset=pagination.offset,
        limit=pagination.limit,
    )
    return PageResponse(result=AuditPage(**result))
