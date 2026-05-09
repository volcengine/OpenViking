# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory health statistics endpoints for OpenViking HTTP Server."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import ErrorInfo, Response
from openviking.server.schemas import ExcludeNoneRoute
from openviking.server.schemas.stats import (
    MemoryStats,
    SessionExtractionStats,
    TokenStats,
)
from openviking.storage.stats_aggregator import MEMORY_CATEGORIES, StatsAggregator

router = APIRouter(
    prefix="/api/v1/stats",
    tags=["stats"],
    route_class=ExcludeNoneRoute,
)
logger = logging.getLogger(__name__)


def _get_aggregator() -> StatsAggregator:
    """Build a StatsAggregator from the current service."""
    service = get_service()
    return StatsAggregator(service.vikingdb_manager)


@router.get("/memories", response_model=Response[MemoryStats])
async def get_memory_stats(
    category: Optional[str] = Query(
        None,
        description="Filter by memory category (e.g. cases, patterns, tools)",
    ),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[MemoryStats]:
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
    return Response(status="ok", result=MemoryStats.model_validate(result))


@router.get("/sessions/{session_id}", response_model=Response[SessionExtractionStats])
async def get_session_stats(
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[SessionExtractionStats]:
    """Get extraction statistics for a specific session."""
    service = get_service()
    aggregator = _get_aggregator()
    try:
        result = await aggregator.get_session_extraction_stats(session_id, service, _ctx)
        return Response(
            status="ok",
            result=SessionExtractionStats.model_validate(result),
        )
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


@router.get("/tokens", response_model=Response[TokenStats])
async def get_token_stats(
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[TokenStats]:
    """Get aggregate token usage statistics.

    Returns total token usage across all sessions, broken down by LLM
    (prompt/completion) and embedding tokens.
    """
    service = get_service()
    aggregator = _get_aggregator()
    try:
        result = await aggregator.get_token_stats(service, _ctx)
        return Response(status="ok", result=TokenStats.model_validate(result))
    except Exception as e:
        logger.error("Failed to get token stats: %s", e)
        return Response(
            status="error",
            error=ErrorInfo(
                code="INTERNAL_ERROR",
                message=f"Failed to retrieve token stats: {type(e).__name__}",
            ),
        )
