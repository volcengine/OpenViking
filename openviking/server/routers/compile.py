# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile task creation API."""

from fastapi import APIRouter, Depends, status

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.service.compile_service import CompileRequest

router = APIRouter(prefix="/api/v1", tags=["compile"])


@router.post("/compile", status_code=status.HTTP_202_ACCEPTED)
async def create_compile(
    body: CompileRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    connection = {"api_key": ctx.api_key} if ctx.api_key else {}
    task = await get_service().compile.create(body, connection=connection, ctx=ctx)
    return Response(status="ok", result=task.to_dict())


__all__ = ["router"]
