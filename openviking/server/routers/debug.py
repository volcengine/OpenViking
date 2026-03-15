# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Debug endpoints for OpenViking HTTP Server.

Provides debug API for system diagnostics.
- /api/v1/debug/health - Quick health check
- /api/v1/debug/vector/scroll - Paginated vector records
- /api/v1/debug/vector/count - Count vector records
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import ErrorInfo, Response
from openviking.storage import VikingDBManagerProxy

router = APIRouter(prefix="/api/v1/debug", tags=["debug"])


@router.get("/health")
async def debug_health(
    _ctx: RequestContext = Depends(get_request_context),
):
    """Quick health check."""
    service = get_service()
    is_healthy = service.debug.is_healthy()
    return Response(status="ok", result={"healthy": is_healthy})


@router.get("/vector/scroll")
async def debug_vector_scroll(
    limit: int = Query(100, ge=1, le=1000),
    cursor: Optional[str] = None,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get paginated vector records with tenant isolation."""
    service = get_service()
    if not service.vikingdb_manager:
        return Response(
            status="error",
            error=ErrorInfo(code="NO_VECTOR_DB", message="Vector DB not initialized"),
        )

    proxy = VikingDBManagerProxy(service.vikingdb_manager, _ctx)
    records, next_cursor = await proxy.scroll(limit=limit, cursor=cursor)

    return Response(status="ok", result={"records": records, "next_cursor": next_cursor})


@router.get("/vector/count")
async def debug_vector_count(
    filter: Optional[str] = None,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get count of vector records with tenant isolation."""
    import json

    service = get_service()
    if not service.vikingdb_manager:
        return Response(
            status="error",
            error=ErrorInfo(code="NO_VECTOR_DB", message="Vector DB not initialized"),
        )

    proxy = VikingDBManagerProxy(service.vikingdb_manager, _ctx)

    filter_expr = None
    if filter:
        try:
            filter_expr = json.loads(filter)
        except json.JSONDecodeError:
            return Response(
                status="error",
                error=ErrorInfo(code="INVALID_FILTER", message="Invalid filter JSON"),
            )

    count = await proxy.count(filter=filter_expr)
    return Response(status="ok", result={"count": count})
