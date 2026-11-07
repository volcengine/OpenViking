# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Per-user server settings."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict

from openviking.server.auth import get_request_context
from openviking.server.config import AddTargetsConfig, UserSettingsPatch
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.user_config import (
    patch_user_config,
    read_user_config,
    resolve_add_targets,
    resolve_user_add_targets,
)
from openviking_cli.exceptions import NotInitializedError

router = APIRouter(prefix="/api/v1/user-settings", tags=["user-settings"])


class PatchAddLocationsRequest(AddTargetsConfig):
    model_config = ConfigDict(extra="forbid")


def _viking_fs():
    viking_fs = get_service().viking_fs
    if viking_fs is None:
        raise NotInitializedError("VikingFS")
    return viking_fs


async def _response(request: Request, ctx: RequestContext) -> dict[str, Any]:
    viking_fs = _viking_fs()
    user_config = await read_user_config(viking_fs, ctx)
    effective = resolve_add_targets(
        resolve_user_add_targets(user_config, request.app.state.config),
        ctx=ctx,
    )
    return {
        "override": user_config.add_targets.model_dump(),
        "effective": {
            "resource_uri": effective.resource_uri,
            "skill_uri": effective.skill_uri,
        },
    }


@router.get("/add-locations", deprecated=True)
async def get_add_locations(
    request: Request,
    _ctx: RequestContext = Depends(get_request_context),
):
    return Response(status="ok", result=await _response(request, _ctx)).model_dump(
        exclude_none=True
    )


@router.patch("/add-locations", deprecated=True)
async def patch_add_locations(
    request: Request,
    body: PatchAddLocationsRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    await patch_user_config(
        _viking_fs(), _ctx, UserSettingsPatch(add_targets=body)
    )
    return Response(status="ok", result=await _response(request, _ctx)).model_dump(
        exclude_none=True
    )


@router.delete("/add-locations", deprecated=True)
async def delete_add_locations(
    request: Request,
    _ctx: RequestContext = Depends(get_request_context),
):
    await patch_user_config(_viking_fs(), _ctx, UserSettingsPatch(add_targets=None))
    return Response(status="ok", result=await _response(request, _ctx)).model_dump(
        exclude_none=True
    )
