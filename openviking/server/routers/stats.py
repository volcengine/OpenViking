# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Memory health statistics endpoints for OpenViking HTTP Server."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import ErrorInfo, Response
from openviking.storage.stats_aggregator import MEMORY_CATEGORIES, StatsAggregator

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])
logger = logging.getLogger(__name__)


def _get_aggregator() -> StatsAggregator:
    """Build a StatsAggregator from the current service."""
    service = get_service()
    return StatsAggregator(service.vikingdb_manager)


@router.get("/memories")
async def get_memory_stats(
    category: Optional[str] = Query(
        None,
        description="Filter by memory category (e.g. cases, patterns, tools)",
    ),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get aggregate memory health statistics.

    Returns counts by category, hotness distribution, and staleness metrics.
    Optionally filter by a single category.
    """
    if category and category not in MEMORY_CATEGORIES:
        return Response(
            status="error",
            error=ErrorInfo(
                code="INVALID_ARGUMENT",
                message=f"Unknown category: {category}. Valid categories: {', '.join(MEMORY_CATEGORIES)}",
            ),
        )

    aggregator = _get_aggregator()
    result = await aggregator.get_memory_stats(_ctx, category=category)
    return Response(status="ok", result=result)


@router.get("/sessions/{session_id}")
async def get_session_stats(
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get extraction statistics for a specific session."""
    service = get_service()
    aggregator = _get_aggregator()
    try:
        result = await aggregator.get_session_extraction_stats(session_id, service, _ctx)
        return Response(status="ok", result=result)
    except KeyError:
        return Response(
            status="error",
            error=ErrorInfo(
                code="NOT_FOUND",
                message=f"Session not found: {session_id}",
            ),
        )
    except Exception as e:
        logger.error("Failed to get session stats for %s: %s", session_id, e)
        return Response(
            status="error",
            error=ErrorInfo(
                code="INTERNAL_ERROR",
                message=f"Failed to retrieve session stats: {type(e).__name__}",
            ),
        )
