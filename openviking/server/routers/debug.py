# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Debug endpoints for OpenViking HTTP Server.

Provides debug API for system diagnostics.
- /api/v1/debug/health - Quick health check
"""

from fastapi import APIRouter, Depends

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/debug", tags=["debug"])


@router.get("/health")
async def debug_health(
    _ctx: RequestContext = Depends(get_request_context),
):
    """Quick health check."""
    service = get_service()
    is_healthy = service.debug.is_healthy()
    return Response(status="ok", result={"healthy": is_healthy})


@router.get("/storage/stats")
async def storage_stats(
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get storage backend statistics (backend type, record counts, etc.)."""
    service = get_service()
    vikingdb = service.vikingdb_manager
    if vikingdb is None:
        return Response(status="ok", result={"error": "storage not initialized"})
    stats = await vikingdb.get_stats()
    return Response(status="ok", result=stats)


@router.get("/storage/list")
async def storage_list(
    collection: str = "context",
    limit: int = 10,
    offset: int = 0,
    _ctx: RequestContext = Depends(get_request_context),
):
    """List records from a collection (for dashboard overview)."""
    service = get_service()
    vikingdb = service.vikingdb_manager
    if vikingdb is None:
        return Response(status="ok", result={"items": []})
    try:
        from openviking_cli.utils.config import get_openviking_config
        coll_name = get_openviking_config().storage.vectordb.name or collection
        items = await vikingdb.filter(
            coll_name,
            filter={},
            limit=limit,
            offset=offset,
            order_by="created_at",
            order_desc=True,
        )
        return Response(status="ok", result={"items": items, "total": len(items)})
    except Exception as e:
        return Response(status="ok", result={"items": [], "error": str(e)})
