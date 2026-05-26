# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Privacy config endpoints for OpenViking HTTP Server."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict

from openviking.server.auth import get_request_context
from openviking_cli.exceptions import NotFoundError
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/privacy-configs", tags=["privacy-configs"])


async def _require_privacy_target(
    privacy,
    ctx: RequestContext,
    category: str,
    target_key: str,
) -> None:
    if privacy is None or not await privacy.exists(ctx, category, target_key):
        raise NotFoundError(f"{category}/{target_key}", "privacy config")


class UpsertPrivacyConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: Dict[str, Any]
    change_reason: str = ""
    labels: Optional[Dict[str, Any]] = None


class ActivatePrivacyConfigVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int


@router.get("")
async def list_privacy_categories(
    _ctx: RequestContext = Depends(get_request_context),
):
    service = get_service()
    privacy = service.privacy_configs
    result = [] if privacy is None else await privacy.list_categories(_ctx)
    return Response(status="ok", result=result)


@router.get("/{category}")
async def list_privacy_targets(
    category: str = Path(..., description="Privacy config category"),
    _ctx: RequestContext = Depends(get_request_context),
):
    service = get_service()
    privacy = service.privacy_configs
    result = [] if privacy is None else await privacy.list_targets(_ctx, category)
    return Response(status="ok", result=result)


@router.get("/{category}/{target_key}")
async def get_privacy_current(
    category: str = Path(..., description="Privacy config category"),
    target_key: str = Path(..., description="Privacy config target key"),
    _ctx: RequestContext = Depends(get_request_context),
):
    service = get_service()
    privacy = service.privacy_configs
    await _require_privacy_target(privacy, _ctx, category, target_key)
    meta = await privacy.get_meta(_ctx, category, target_key)
    current = await privacy.get_current(_ctx, category, target_key)
    return Response(
        status="ok",
        result={
            "meta": None if meta is None else meta.to_dict(),
            "current": None if current is None else current.to_dict(),
        },
    )


@router.get("/{category}/{target_key}/versions")
async def list_privacy_versions(
    category: str = Path(..., description="Privacy config category"),
    target_key: str = Path(..., description="Privacy config target key"),
    _ctx: RequestContext = Depends(get_request_context),
):
    service = get_service()
    privacy = service.privacy_configs
    await _require_privacy_target(privacy, _ctx, category, target_key)
    versions = await privacy.list_versions(_ctx, category, target_key)
    return Response(status="ok", result=versions)


@router.get("/{category}/{target_key}/versions/{version}")
async def get_privacy_version(
    category: str = Path(..., description="Privacy config category"),
    target_key: str = Path(..., description="Privacy config target key"),
    version: int = Path(..., description="Privacy config version"),
    _ctx: RequestContext = Depends(get_request_context),
):
    service = get_service()
    privacy = service.privacy_configs
    await _require_privacy_target(privacy, _ctx, category, target_key)
    snapshot = await privacy.get_version(_ctx, category, target_key, version)
    if snapshot is None:
        raise NotFoundError(f"{category}/{target_key}/versions/{version}", "privacy config")
    return Response(status="ok", result=snapshot.to_dict())


@router.post("/{category}/{target_key}")
async def upsert_privacy_config(
    request: UpsertPrivacyConfigRequest,
    category: str = Path(..., description="Privacy config category"),
    target_key: str = Path(..., description="Privacy config target key"),
    _ctx: RequestContext = Depends(get_request_context),
):
    service = get_service()
    privacy = service.privacy_configs
    result = None
    if privacy is not None:
        result = await privacy.upsert(
            ctx=_ctx,
            category=category,
            target_key=target_key,
            values=request.values,
            updated_by=_ctx.user.user_id,
            change_reason=request.change_reason,
            labels=request.labels,
        )
    return Response(status="ok", result=None if result is None else result.to_dict())


@router.post("/{category}/{target_key}/activate")
async def activate_privacy_version(
    request: ActivatePrivacyConfigVersionRequest,
    category: str = Path(..., description="Privacy config category"),
    target_key: str = Path(..., description="Privacy config target key"),
    _ctx: RequestContext = Depends(get_request_context),
):
    service = get_service()
    privacy = service.privacy_configs
    await _require_privacy_target(privacy, _ctx, category, target_key)
    result = await privacy.activate_version(
        ctx=_ctx,
        category=category,
        target_key=target_key,
        version=request.version,
        updated_by=_ctx.user.user_id,
    )
    return Response(status="ok", result=result.to_dict())
