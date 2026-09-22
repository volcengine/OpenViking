# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile task creation API."""

from fastapi import APIRouter, Depends, Header, Path, status

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
    idempotency_key: str | None = Header(
        None, min_length=16, max_length=128, pattern=r"^[A-Za-z0-9:._-]+$"
    ),
    x_request_id: str | None = Header(None),
):
    connection = {"api_key": ctx.api_key} if ctx.api_key else {}
    # Persist the submission's request ID for all asynchronous Runtime calls.
    if x_request_id:
        connection["request_id"] = x_request_id
    task = await get_service().compile.create(
        body,
        connection=connection,
        ctx=ctx,
        **({"idempotency_key": idempotency_key} if idempotency_key else {}),
    )
    return Response(status="ok", result=task.to_dict())


__all__ = ["router"]


@router.get("/compile/capabilities")
async def compile_capabilities(ctx: RequestContext = Depends(get_request_context)):
    return Response(status="ok", result=get_service().compile.capabilities(ctx))


@router.get("/compile/submissions/{key}")
async def get_submission(
    key: str = Path(..., min_length=16, max_length=128, pattern=r"^[A-Za-z0-9:._-]+$"),
    ctx: RequestContext = Depends(get_request_context),
):
    from openviking.service.external_task_service import submission_task_id
    from openviking.service.task_tracker import get_task_tracker
    from openviking_cli.exceptions import NotFoundError

    task_id = submission_task_id(ctx, "compile", "cmp_", key)
    task = await get_task_tracker().get(
        task_id, account_id=ctx.account_id, user_id=ctx.user.user_id
    )
    if task is None:
        raise NotFoundError(key, "submission")
    return Response(status="ok", result=task.to_dict())
