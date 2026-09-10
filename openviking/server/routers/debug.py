# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Debug endpoints for OpenViking HTTP Server.

Provides debug API for system diagnostics.
- /api/v1/debug/health - Quick health check
- /api/v1/debug/vector/scroll - Paginated vector records
- /api/v1/debug/vector/count - Count vector records
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from openviking.core.namespace import visible_roots
from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext, Role
from openviking.server.models import Response
from openviking.server.responses import error_response
from openviking.storage.expr import And, FilterExpr, Or, PathScope, RawDSL
from openviking.storage.vikingdb_manager import VikingDBManagerProxy

router = APIRouter(prefix="/api/v1/debug", tags=["debug"])


def _scope_vector_filter(
    filter_expr: Optional[dict[str, Any] | FilterExpr], ctx: RequestContext
) -> Optional[FilterExpr | dict[str, Any]]:
    """Keep USER diagnostics inside the caller's visible vector roots."""
    if ctx.role != Role.USER:
        return filter_expr

    visible_scope = Or([PathScope("uri", root, depth=-1) for root in visible_roots(ctx)])
    if not filter_expr:
        return visible_scope
    if isinstance(filter_expr, dict):
        filter_expr = RawDSL(filter_expr)
    return And([visible_scope, filter_expr])


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
    uri: Optional[str] = None,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get paginated vector records with tenant isolation."""
    service = get_service()
    if not service.vikingdb_manager:
        return error_response(
            code="NO_VECTOR_DB", message="Vector DB not initialized"
        )

    proxy = VikingDBManagerProxy(service.vikingdb_manager, _ctx)

    filter_expr = None
    if uri:
        # Resolve path variables before using URI
        uri = validate_request_viking_uri(resolve_path_variables(uri), _ctx)
        filter_expr = {"op": "must", "field": "uri", "conds": [uri]}
    filter_expr = _scope_vector_filter(filter_expr, _ctx)

    records, next_cursor = await proxy.scroll(filter=filter_expr, limit=limit, cursor=cursor)

    return Response(status="ok", result={"records": records, "next_cursor": next_cursor})


@router.get("/vector/count")
async def debug_vector_count(
    filter: Optional[str] = None,
    uri: Optional[str] = None,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get count of vector records with tenant isolation."""
    import json

    service = get_service()
    if not service.vikingdb_manager:
        return error_response(
            code="NO_VECTOR_DB", message="Vector DB not initialized"
        )

    proxy = VikingDBManagerProxy(service.vikingdb_manager, _ctx)

    filter_expr = None
    if filter:
        try:
            filter_expr = json.loads(filter)
        except json.JSONDecodeError:
            return error_response(
                code="INVALID_FILTER", message="Invalid filter JSON"
            )

    if uri:
        # Resolve path variables before using URI
        uri = validate_request_viking_uri(resolve_path_variables(uri), _ctx)
        uri_filter = {"op": "must", "field": "uri", "conds": [uri]}
        if filter_expr:
            # For combining filters, we should use And from expr, but for simplicity, let's use RawDSL for now
            if isinstance(filter_expr, dict):
                filter_expr = RawDSL(filter_expr)
            uri_filter = RawDSL(uri_filter)
            filter_expr = And([filter_expr, uri_filter])
        else:
            filter_expr = uri_filter
    filter_expr = _scope_vector_filter(filter_expr, _ctx)

    count = await proxy.count(filter=filter_expr)
    return Response(status="ok", result={"count": count})
