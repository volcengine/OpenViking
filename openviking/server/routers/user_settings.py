# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Per-user server settings."""

from dataclasses import asdict
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from openviking.server.auth import get_request_context
from openviking.server.config import AddTargetsConfig, PrivacyConfig, PrivacyRuleConfig
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.user_config import (
    ResolvedAddTargets,
    delete_user_add_targets,
    effective_resource_add_target,
    effective_skill_add_target,
    patch_user_config,
    public_add_targets,
    read_user_add_targets,
    read_user_privacy,
    resolve_user_privacy,
    write_user_add_targets,
)
from openviking_cli.exceptions import InvalidArgumentError, NotInitializedError

router = APIRouter(prefix="/api/v1/user-settings", tags=["user-settings"])


class PatchAddLocationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_uri: Optional[str] = None
    skill_uri: Optional[str] = None


class PatchPrivacyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Optional[Literal["off", "registered", "auto", "auto_strict"]] = None
    restore: Optional[StrictBool] = None
    custom_rules: Optional[list[PrivacyRuleConfig]] = None
    never_mask: Optional[list[StrictStr]] = Field(default=None, max_length=1024)


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


def _privacy_response(request: Request, override: PrivacyConfig) -> dict[str, Any]:
    effective = resolve_user_privacy(
        override, request.app.state.config.user_config_defaults.privacy
    )
    return {
        "override": override.model_dump(exclude_none=True),
        "effective": asdict(effective),
    }


@router.get("/privacy")
async def get_privacy(
    request: Request,
    _ctx: RequestContext = Depends(get_request_context),
):
    override = await read_user_privacy(_viking_fs(), _ctx)
    return Response(status="ok", result=_privacy_response(request, override)).model_dump(
        exclude_none=True
    )


@router.patch("/privacy")
async def patch_privacy(
    request: Request,
    body: PatchPrivacyRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    config = await patch_user_config(
        _viking_fs(), _ctx, {"privacy": body.model_dump(exclude_unset=True)}
    )
    return Response(status="ok", result=_privacy_response(request, config.privacy)).model_dump(
        exclude_none=True
    )


@router.delete("/privacy")
async def delete_privacy(
    request: Request,
    _ctx: RequestContext = Depends(get_request_context),
):
    config = await patch_user_config(_viking_fs(), _ctx, {"privacy": None})
    return Response(status="ok", result=_privacy_response(request, config.privacy)).model_dump(
        exclude_none=True
    )
