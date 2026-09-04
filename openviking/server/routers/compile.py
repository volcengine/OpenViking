# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile task creation API."""

from fastapi import APIRouter, Depends, Request, status

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.openviking_connection import attach_openviking_connection
from openviking.service.compile_service import CompileRequest

router = APIRouter(prefix="/api/v1", tags=["compile"])


@router.post("/compile", status_code=status.HTTP_202_ACCEPTED)
async def create_compile(
    request: Request,
    body: CompileRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    connection = attach_openviking_connection(
        {},
        request,
        ctx,
        include_legacy_user_id=False,
    ).get("openviking_connection", {})
    task = await get_service().compile.create(body, connection=connection, ctx=ctx)
    return Response(status="ok", result=task.to_dict())


__all__ = ["router"]
