# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource attributes, including ACL management."""

from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, ConfigDict

from openviking.core.namespace import context_type_for_uri
from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.pyagfs.exceptions import AGFSClientError, AGFSNotFoundError
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.error_mapping import map_exception
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.routers.content import SetTagsRequest
from openviking.server.routers.content import set_tags as content_set_tags
from openviking.storage.acl import AclLevel, AclSpec, is_acl_uri
from openviking.storage.expr import And, Eq, In
from openviking.storage.vikingdb_manager import VikingDBManagerProxy
from openviking.utils.tags import normalize_search_tags
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError, PermissionDeniedError

router = APIRouter(prefix="/api/v1/fs/attrs", tags=["filesystem"])


class SetAclRequest(AclSpec):
    uri: str


class ResetAclRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uri: str


class RevokeAclRequest(ResetAclRequest):
    principal: str


class GrantAclRequest(RevokeAclRequest):
    level: AclLevel


_ATTR_INDEX_FIELDS = ["level", "search_tags"]


def _clean_memory_attrs(raw: str) -> dict[str, Any]:
    from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

    mf = MemoryFileUtils.read(raw)
    attrs: dict[str, Any] = mf.to_metadata()
    attrs.pop("content", None)
    return attrs


async def _tags_attr(service: Any, uri: str, ctx: RequestContext, *, is_dir: bool) -> list[str]:
    vikingdb_manager = getattr(service, "vikingdb_manager", None)
    if not vikingdb_manager:
        return []

    # Tags are written per level (see ContentWriteCoordinator.set_tags): a
    # directory carries them on its L0/L1 summary records, while a file carries
    # them on its L2 content record. Mirror that here so the exact node's tags
    # are read back instead of unrelated records. Eq("uri", ...) compiles to an
    # exact path match (-d=0), avoiding prefix/subtree matches over the path field.
    levels = [0, 1] if is_dir else [2]
    proxy = VikingDBManagerProxy(vikingdb_manager, ctx)
    records = await proxy.filter(
        filter=And([Eq("uri", uri), In("level", levels)]),
        limit=10,
        output_fields=_ATTR_INDEX_FIELDS,
    )
    records = sorted(records, key=lambda item: item.get("level", 99))
    tags: list[str] = []
    for record in records:
        for tag in normalize_search_tags(record.get("search_tags"), discard_invalid=True):
            if tag not in tags:
                tags.append(tag)
    return tags


@router.get("")
async def attrs(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
    key: str | None = None,
):
    """Get logical extended attributes for a URI."""
    service = get_service()
    uri = validate_request_viking_uri(resolve_path_variables(uri), _ctx)
    selected = key.removeprefix("attrs.").split(".") if key else None
    if selected and selected[0] not in {"acl", "tags", "memory"}:
        raise InvalidArgumentError(f"Unknown attribute: {key}")
    if selected and selected[0] == "acl":
        report = await service.fs.get_acl(uri, ctx=_ctx)
        return Response(
            status="ok",
            result={
                "uri": uri,
                "context_type": context_type_for_uri(uri),
                "attrs": {"acl": report},
            },
        )
    try:
        stat_result = await service.fs.stat(uri, ctx=_ctx, skip_count=True)
        result = {
            "uri": uri,
            "context_type": context_type_for_uri(uri),
            "attrs": {
                "tags": await _tags_attr(
                    service, uri, _ctx, is_dir=stat_result.get("isDir", False)
                ),
            },
        }
        if result["context_type"] == "memory" and not stat_result.get("isDir", False):
            result["attrs"]["memory"] = _clean_memory_attrs(await service.fs.read(uri, ctx=_ctx))
        if selected:
            if selected[0] not in result["attrs"]:
                raise InvalidArgumentError(f"Attribute not found: {key}")
            result["attrs"] = {selected[0]: result["attrs"][selected[0]]}
        elif is_acl_uri(uri):
            try:
                result["attrs"]["acl"] = await service.fs.get_acl(uri, ctx=_ctx)
            except PermissionDeniedError:
                pass
        return Response(status="ok", result=result)
    except AGFSNotFoundError:
        raise NotFoundError(uri, "file")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=uri, resource_type="file")
        if mapped is not None:
            raise mapped from e
        raise
    except Exception as exc:
        mapped = map_exception(exc, resource=uri)
        if mapped is not None:
            raise mapped from exc
        raise


@router.post("/set_tags")
async def attrs_set_tags(
    request: SetTagsRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Set explicit k=v retrieval tags metadata for a file or directory."""
    return await content_set_tags(request, _ctx)


@router.post("/set_acl")
async def set_acl(
    request: SetAclRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    uri = validate_request_viking_uri(resolve_path_variables(request.uri), _ctx)
    result = await get_service().fs.set_acl(
        uri,
        ([entry.to_dict() for entry in request.entries] if request.entries is not None else None),
        acl_mode=request.acl_mode,
        ctx=_ctx,
    )
    return Response(status="ok", result=result)


@router.post("/reset_acl")
async def reset_acl(
    request: ResetAclRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    uri = validate_request_viking_uri(resolve_path_variables(request.uri), _ctx)
    result = await get_service().fs.delete_acl(uri, ctx=_ctx)
    return Response(status="ok", result=result)


@router.post("/grant_acl")
async def grant_acl(
    request: GrantAclRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    uri = validate_request_viking_uri(resolve_path_variables(request.uri), _ctx)
    result = await get_service().fs.grant_acl(
        uri,
        request.principal,
        request.level,
        ctx=_ctx,
    )
    return Response(status="ok", result=result)


@router.post("/revoke_acl")
async def revoke_acl(
    request: RevokeAclRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    uri = validate_request_viking_uri(resolve_path_variables(request.uri), _ctx)
    result = await get_service().fs.revoke_acl(uri, request.principal, ctx=_ctx)
    return Response(status="ok", result=result)
