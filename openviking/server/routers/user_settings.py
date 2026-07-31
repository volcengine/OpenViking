# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Per-user server settings."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from openviking.server.auth import get_request_context
from openviking.server.config import AddTargetsConfig
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.user_config import (
    ResolvedAddTargets,
    delete_user_add_targets,
    effective_resource_add_target,
    effective_skill_add_target,
    public_add_targets,
    read_user_add_targets,
    write_user_add_targets,
)
from openviking_cli.exceptions import InvalidArgumentError, NotInitializedError

router = APIRouter(prefix="/api/v1/user-settings", tags=["user-settings"])


class PatchAddLocationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_uri: Optional[str] = None
    skill_uri: Optional[str] = None


def _viking_fs():
    viking_fs = get_service().viking_fs
    if viking_fs is None:
        raise NotInitializedError("VikingFS")
    return viking_fs


async def _response(request: Request, ctx: RequestContext) -> dict[str, Any]:
    viking_fs = _viking_fs()
    override = await read_user_add_targets(viking_fs, ctx)
    effective = ResolvedAddTargets(
        resource_uri=await effective_resource_add_target(
            viking_fs=viking_fs,
            ctx=ctx,
            server_config=request.app.state.config,
        ),
        skill_uri=await effective_skill_add_target(
            viking_fs=viking_fs,
            ctx=ctx,
            server_config=request.app.state.config,
        ),
    )
    return {
        "override": public_add_targets(override),
        "effective": {
            "resource_uri": effective.resource_uri,
            "skill_uri": effective.skill_uri,
        },
    }


@router.get("/add-locations")
async def get_add_locations(
    request: Request,
    _ctx: RequestContext = Depends(get_request_context),
):
    return Response(status="ok", result=await _response(request, _ctx)).model_dump(
        exclude_none=True
    )


@router.patch("/add-locations")
async def patch_add_locations(
    request: Request,
    body: PatchAddLocationsRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    viking_fs = _viking_fs()
    current = await read_user_add_targets(viking_fs, _ctx)
    data = current.model_dump(exclude_none=True)
    for field in body.model_fields_set:
        value = getattr(body, field)
        if value is None:
            data.pop(field, None)
        else:
            data[field] = value
    if data:
        try:
            settings = AddTargetsConfig.model_validate(data)
        except Exception as exc:
            raise InvalidArgumentError(str(exc)) from exc
        await write_user_add_targets(
            viking_fs,
            _ctx,
            settings,
        )
    else:
        await delete_user_add_targets(viking_fs, _ctx)
    return Response(status="ok", result=await _response(request, _ctx)).model_dump(
        exclude_none=True
    )


@router.delete("/add-locations")
async def delete_add_locations(
    request: Request,
    _ctx: RequestContext = Depends(get_request_context),
):
    await delete_user_add_targets(_viking_fs(), _ctx)
    return Response(status="ok", result=await _response(request, _ctx)).model_dump(
        exclude_none=True
    )
