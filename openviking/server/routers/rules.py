# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Rule compilation and applicability search APIs."""

from fastapi import APIRouter, Depends, status

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.service.rule_service import RuleCompileRequest, RuleSearchRequest

router = APIRouter(prefix="/api/v1/rules", tags=["rules"])


@router.post("/compile", status_code=status.HTTP_202_ACCEPTED)
async def compile_rules(
    body: RuleCompileRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """Queue an in-process rule compilation task."""
    task = await get_service().rules.create_compile(body, ctx=ctx)
    return Response(status="ok", result=task.to_dict())


@router.post("/search")
async def search_rules(
    body: RuleSearchRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """Return complete rules independently judged applicable to the task."""
    result = await get_service().rules.search_rules(body, ctx=ctx)
    return Response(status="ok", result=result)


__all__ = ["router"]
