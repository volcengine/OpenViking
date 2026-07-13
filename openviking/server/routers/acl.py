"""ACL endpoints."""

from typing import Literal

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, ConfigDict

from openviking.core.path_variables import resolve_path_variables
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/acl", tags=["acl"])


class AclEntryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    level: Literal["viewer", "editor", "manager"]


class SetAclRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    entries: list[AclEntryRequest]


class GrantAclRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    user_id: str
    level: Literal["viewer", "editor", "manager"]


class RevokeAclRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    user_id: str


@router.get("")
async def get_acl(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    result = await get_service().fs.get_acl(resolve_path_variables(uri), ctx=_ctx)
    return Response(status="ok", result=result)


@router.put("")
async def set_acl(
    request: SetAclRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    result = await get_service().fs.set_acl(
        resolve_path_variables(request.uri),
        [entry.model_dump() for entry in request.entries],
        ctx=_ctx,
    )
    return Response(status="ok", result=result)


@router.delete("")
async def delete_acl(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    result = await get_service().fs.delete_acl(resolve_path_variables(uri), ctx=_ctx)
    return Response(status="ok", result=result)


@router.post("/grant")
async def grant_acl(
    request: GrantAclRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    result = await get_service().fs.grant_acl(
        resolve_path_variables(request.uri),
        request.user_id,
        request.level,
        ctx=_ctx,
    )
    return Response(status="ok", result=result)


@router.post("/revoke")
async def revoke_acl(
    request: RevokeAclRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    result = await get_service().fs.revoke_acl(
        resolve_path_variables(request.uri), request.user_id, ctx=_ctx
    )
    return Response(status="ok", result=result)
