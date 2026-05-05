# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Agent-facing OpenViking tools."""

from fastapi import APIRouter, Depends

from openviking.server.agent_tools import RememberRequest
from openviking.server.agent_tools import remember as agent_remember
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@router.post("/remember")
async def remember(
    request: RememberRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Explicitly remember text or messages through the session memory pipeline."""
    service = get_service()
    result = await agent_remember(service, _ctx, request)
    return Response(status="ok", result=result).model_dump(exclude_none=True)
