# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Privacy config endpoints for OpenViking HTTP Server."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from openviking.privacy.operations import (
    build_privacy_tree,
    require_privacy_owner,
    resolve_privacy_field,
)
from openviking.privacy.reference import (
    build_privacy_field_reference,
    parse_privacy_field_selector,
    parse_privacy_target_selector,
)
from openviking.privacy.sanitizer import PrivacySanitizer
from openviking.privacy.scan import PRIVACY_SCAN_MAX_NODES, scan_privacy_content
from openviking.privacy.validation import validate_privacy_field_name
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError

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


class PatchPrivacyConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: Dict[str, Any]
    change_reason: str = ""
    labels: Optional[Dict[str, Any]] = None


class ActivatePrivacyConfigVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int


class ScanPrivacyContentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str = Field(..., min_length=1, max_length=2048)
    fix: bool = False
    node_limit: int = Field(default=PRIVACY_SCAN_MAX_NODES, ge=1, le=PRIVACY_SCAN_MAX_NODES)


class PrivacySelectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selector: str = Field(..., min_length=1, max_length=2048)


@router.get("")
async def list_privacy_categories(
    _ctx: RequestContext = Depends(get_request_context),
):
    service = get_service()
    privacy = service.privacy_configs
    result = [] if privacy is None else await privacy.list_categories(_ctx)
    return Response(status="ok", result=result)


@router.post("/scan")
async def scan_privacy_configs(
    request: ScanPrivacyContentRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    service = get_service()
    privacy = service.privacy_configs
    if privacy is None:
        return Response(status="ok", result={"matches": [], "matched_files": 0})
    result = await scan_privacy_content(
        service=service,
        ctx=_ctx,
        sanitizer=service.privacy_sanitizer or PrivacySanitizer(privacy),
        uri=request.uri,
        fix=request.fix,
        node_limit=request.node_limit,
    )
    return Response(status="ok", result=result)


@router.post("/resolve")
async def resolve_privacy_reference(
    request: PrivacySelectorRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    return Response(
        status="ok",
        result=await resolve_privacy_field(get_service().privacy_configs, _ctx, request.selector),
    )


@router.delete("/selector")
async def delete_privacy_selector(
    request: PrivacySelectorRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    service = get_service()
    privacy = service.privacy_configs
    field_reference = parse_privacy_field_selector(
        request.selector,
        current_user_id=_ctx.user.user_id,
    )
    if field_reference is not None:
        require_privacy_owner(field_reference.owner_user_id, _ctx)
        outcome = await privacy.delete_field(
            _ctx,
            field_reference.category,
            field_reference.target_key,
            field_reference.field,
            updated_by=_ctx.user.user_id,
        )
        if outcome is None:
            raise NotFoundError(request.selector, "privacy config field")
        return Response(
            status="ok",
            result={
                "owner_user_id": field_reference.owner_user_id,
                "category": field_reference.category,
                "target_key": field_reference.target_key,
                "field": field_reference.field,
                "deleted": True,
                "scope": "field",
                "target_deleted": outcome.target_deleted,
                "active_version": outcome.active_version,
            },
        )

    target_reference = parse_privacy_target_selector(
        request.selector,
        current_user_id=_ctx.user.user_id,
    )
    if target_reference is None:
        raise InvalidArgumentError(
            "selector must be a Privacy target URI, field URI, or owner-bound reference"
        )
    require_privacy_owner(target_reference.owner_user_id, _ctx)
    deleted = await privacy.delete(
        _ctx,
        target_reference.category,
        target_reference.target_key,
    )
    return Response(
        status="ok",
        result={
            "owner_user_id": target_reference.owner_user_id,
            "category": target_reference.category,
            "target_key": target_reference.target_key,
            "deleted": deleted,
            "scope": "target",
        },
    )


@router.get("/tree")
async def list_privacy_tree(
    category: Optional[str] = Query(None, description="Limit the tree to one category"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """List the caller's active Privacy targets and field metadata without values."""
    return Response(
        status="ok",
        result=await build_privacy_tree(get_service().privacy_configs, _ctx, category),
    )


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


@router.get("/{category}/{target_key}/fields")
async def list_privacy_fields(
    category: str = Path(..., description="Privacy config category"),
    target_key: str = Path(..., description="Privacy config target key"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """List active field metadata without returning plaintext values."""
    service = get_service()
    privacy = service.privacy_configs
    await _require_privacy_target(privacy, _ctx, category, target_key)
    current = await privacy.get_current(_ctx, category, target_key)
    if current is None:
        raise NotFoundError(f"{category}/{target_key}", "privacy config")

    fields = []
    for field in sorted(current.values):
        value = current.values[field]
        fields.append(
            {
                "field": field,
                "configured": value is not None and str(value) != "",
                "reference": build_privacy_field_reference(
                    _ctx.user.user_id,
                    category,
                    target_key,
                    field,
                ),
            }
        )

    return Response(
        status="ok",
        result={
            "owner_user_id": _ctx.user.user_id,
            "category": category,
            "target_key": target_key,
            "version": current.version,
            "fields": fields,
        },
    )


@router.get("/{category}/{target_key}/field")
async def get_privacy_field(
    category: str = Path(..., description="Privacy config category"),
    target_key: str = Path(..., description="Privacy config target key"),
    field: str = Query(..., min_length=1, description="Field in current values"),
    reveal: bool = Query(False, description="Include the active field value"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Return one active field and its owner-bound reference.

    The safe default omits the value. Callers must opt in with ``reveal=true``
    when the plaintext is required for immediate use.
    """
    service = get_service()
    privacy = service.privacy_configs
    await _require_privacy_target(privacy, _ctx, category, target_key)
    current = await privacy.get_current(_ctx, category, target_key)
    if current is None or field not in current.values:
        raise NotFoundError(
            f"{category}/{target_key}/fields/{field}",
            "privacy config field",
        )

    value = current.values[field]
    result = {
        "owner_user_id": _ctx.user.user_id,
        "category": category,
        "target_key": target_key,
        "field": field,
        "version": current.version,
        "configured": value is not None and str(value) != "",
        "reference": build_privacy_field_reference(
            _ctx.user.user_id,
            category,
            target_key,
            field,
        ),
    }
    if reveal:
        result["value"] = value
    return Response(status="ok", result=result)


@router.delete("/{category}/{target_key}/field")
async def delete_privacy_field(
    category: str = Path(..., description="Privacy config category"),
    target_key: str = Path(..., description="Privacy config target key"),
    field: str = Query(..., min_length=1, description="Field to remove from active values"),
    _ctx: RequestContext = Depends(get_request_context),
):
    validate_privacy_field_name(field, location="field")
    service = get_service()
    privacy = service.privacy_configs
    await _require_privacy_target(privacy, _ctx, category, target_key)
    outcome = await privacy.delete_field(
        _ctx,
        category,
        target_key,
        field,
        updated_by=_ctx.user.user_id,
    )
    if outcome is None:
        raise NotFoundError(f"{category}/{target_key}/fields/{field}", "privacy config field")
    return Response(
        status="ok",
        result={
            "owner_user_id": _ctx.user.user_id,
            "category": category,
            "target_key": target_key,
            "field": field,
            "deleted": True,
            "scope": "field",
            "target_deleted": outcome.target_deleted,
            "active_version": outcome.active_version,
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


@router.patch("/{category}/{target_key}")
async def patch_privacy_config(
    request: PatchPrivacyConfigRequest,
    category: str = Path(..., description="Privacy config category"),
    target_key: str = Path(..., description="Privacy config target key"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Atomically merge fields into current values, creating the target if absent."""
    service = get_service()
    privacy = service.privacy_configs
    result = None
    if privacy is not None:
        result = await privacy.patch_values(
            ctx=_ctx,
            category=category,
            target_key=target_key,
            updates=request.values,
            updated_by=_ctx.user.user_id,
            change_reason=request.change_reason,
            labels=request.labels,
        )
    return Response(status="ok", result=None if result is None else result.to_dict())


@router.delete("/{category}/{target_key}")
async def delete_privacy_config(
    category: str = Path(..., description="Privacy config category"),
    target_key: str = Path(..., description="Privacy config target key"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Delete a target's current value, metadata, and complete version history."""
    service = get_service()
    privacy = service.privacy_configs
    deleted = False
    if privacy is not None:
        deleted = await privacy.delete(_ctx, category, target_key)
    return Response(
        status="ok",
        result={
            "category": category,
            "target_key": target_key,
            "deleted": deleted,
        },
    )


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
