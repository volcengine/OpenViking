# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Filesystem endpoints for OpenViking HTTP Server."""

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel

from openviking.core.namespace import NamespaceShapeError, canonicalize_uri, context_type_for_uri
from openviking.core.path_variables import resolve_path_variables
from openviking.pyagfs.exceptions import AGFSClientError, AGFSNotFoundError
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.error_mapping import map_exception
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.routers.content import SetTagsRequest, set_tags as content_set_tags
from openviking.storage import VikingDBManagerProxy
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError

router = APIRouter(prefix="/api/v1/fs", tags=["filesystem"])


_ATTR_INDEX_FIELDS = ["level", "search_tags"]


def _clean_memory_attrs(raw: str) -> dict[str, Any]:
    from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

    mf = MemoryFileUtils.read(raw)
    attrs: dict[str, Any] = mf.to_metadata()
    attrs.pop("content", None)
    return attrs


async def _tags_attr(service: Any, uri: str, ctx: RequestContext) -> list[str]:
    vikingdb_manager = getattr(service, "vikingdb_manager", None)
    if not vikingdb_manager:
        return []

    proxy = VikingDBManagerProxy(vikingdb_manager, ctx)
    records = await proxy.filter(
        filter={"op": "must", "field": "uri", "conds": [uri]},
        limit=10,
        output_fields=_ATTR_INDEX_FIELDS,
    )
    records = sorted(records, key=lambda item: item.get("level", 99))
    tags: list[str] = []
    for record in records:
        for tag in record.get("search_tags") or []:
            if tag not in tags:
                tags.append(tag)
    return tags


@router.get("/ls")
async def ls(
    uri: str = Query(..., description="Viking URI"),
    simple: bool = Query(False, description="Return only relative path list"),
    recursive: bool = Query(False, description="List all subdirectories recursively"),
    output: str = Query("agent", description="Output format: original or agent"),
    abs_limit: int = Query(256, description="Abstract limit (only for agent output)"),
    show_all_hidden: bool = Query(False, description="List all hidden files, like -a"),
    node_limit: int = Query(1000, description="Maximum number of nodes to list"),
    limit: Optional[int] = Query(None, description="Alias for node_limit"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """List directory contents."""
    service = get_service()
    actual_node_limit = limit if limit is not None else node_limit
    # Resolve path variables
    uri = resolve_path_variables(uri)
    try:
        result = await service.fs.ls(
            uri,
            ctx=_ctx,
            recursive=recursive,
            simple=simple,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
            node_limit=actual_node_limit,
        )
    except AGFSNotFoundError:
        raise NotFoundError(uri, "file")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=uri, resource_type="file")
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)


@router.get("/tree")
async def tree(
    uri: str = Query(..., description="Viking URI"),
    output: str = Query("agent", description="Output format: original or agent"),
    abs_limit: int = Query(256, description="Abstract limit (only for agent output)"),
    show_all_hidden: bool = Query(False, description="List all hidden files, like -a"),
    node_limit: int = Query(1000, description="Maximum number of nodes to list"),
    limit: Optional[int] = Query(None, description="Alias for node_limit"),
    level_limit: int = Query(3, description="Maximum depth level to traverse"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get directory tree."""
    service = get_service()
    actual_node_limit = limit if limit is not None else node_limit
    # Resolve path variables
    uri = resolve_path_variables(uri)
    try:
        result = await service.fs.tree(
            uri,
            ctx=_ctx,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
            node_limit=actual_node_limit,
            level_limit=level_limit,
        )
    except AGFSNotFoundError:
        raise NotFoundError(uri, "file")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=uri, resource_type="file")
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)


@router.get("/stat")
async def stat(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get resource status."""
    service = get_service()
    # Resolve path variables
    uri = resolve_path_variables(uri)
    try:
        result = await service.fs.stat(uri, ctx=_ctx)
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


@router.get("/attrs")
async def attrs(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get logical extended attributes for a URI."""
    service = get_service()
    uri = resolve_path_variables(uri)
    try:
        stat_result = await service.fs.stat(uri, ctx=_ctx)
        try:
            canonical_uri = canonicalize_uri(uri, _ctx)
        except NamespaceShapeError as exc:
            raise InvalidArgumentError(str(exc)) from exc

        result = {
            "uri": canonical_uri,
            "context_type": context_type_for_uri(canonical_uri),
            "attrs": {
                "tags": await _tags_attr(service, canonical_uri, _ctx),
            },
        }
        if result["context_type"] == "memory" and not stat_result.get("isDir", False):
            result["attrs"]["memory"] = _clean_memory_attrs(
                await service.fs.read(canonical_uri, ctx=_ctx)
            )
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


@router.post("/attrs/set_tags")
async def attrs_set_tags(
    request: SetTagsRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Set explicit k=v retrieval tags metadata for a file or directory."""
    return await content_set_tags(request, _ctx)


class MkdirRequest(BaseModel):
    """Request model for mkdir."""

    uri: str
    description: Optional[str] = None


@router.post("/mkdir")
async def mkdir(
    request: MkdirRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Create directory."""
    service = get_service()
    # Resolve path variables
    uri = resolve_path_variables(request.uri)
    try:
        await service.fs.mkdir(uri, ctx=_ctx, description=request.description)
    except AGFSClientError as e:
        mapped = map_exception(e, resource=uri, resource_type="file")
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result={"uri": uri})


@router.delete("")
async def rm(
    uri: str = Query(..., description="Viking URI"),
    recursive: bool = Query(False, description="Remove recursively"),
    wait: bool = Query(False, description="Wait for semantic refresh to complete"),
    timeout: Optional[float] = Query(None, description="Wait timeout in seconds"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Remove resource."""
    service = get_service()
    # Resolve path variables
    uri = resolve_path_variables(uri)
    try:
        result = await service.fs.rm(uri, ctx=_ctx, recursive=recursive, wait=wait, timeout=timeout)
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
    # Build response with uri and estimated_deleted_count
    response_result = {"uri": uri}
    if isinstance(result, dict) and "estimated_deleted_count" in result:
        response_result["estimated_deleted_count"] = result["estimated_deleted_count"]
    if isinstance(result, dict) and "memory_cleanup" in result:
        response_result["memory_cleanup"] = result["memory_cleanup"]
    if isinstance(result, dict) and "semantic_root_uri" in result:
        response_result["semantic_root_uri"] = result["semantic_root_uri"]
    if isinstance(result, dict) and "semantic_status" in result:
        response_result["semantic_status"] = result["semantic_status"]
    if isinstance(result, dict) and "queue_status" in result:
        response_result["queue_status"] = result["queue_status"]
    return Response(status="ok", result=response_result)


class MvRequest(BaseModel):
    """Request model for mv."""

    from_uri: str
    to_uri: str


@router.post("/mv")
async def mv(
    request: MvRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Move resource."""
    service = get_service()
    # Resolve path variables
    from_uri = resolve_path_variables(request.from_uri)
    to_uri = resolve_path_variables(request.to_uri)
    try:
        await service.fs.mv(from_uri, to_uri, ctx=_ctx)
    except AGFSNotFoundError:
        raise NotFoundError(from_uri, "file")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=from_uri, resource_type="file")
        if mapped is not None:
            raise mapped from e
        raise
    except Exception as exc:
        mapped = map_exception(exc, resource=from_uri)
        if mapped is not None:
            raise mapped from exc
        raise
    return Response(status="ok", result={"from": from_uri, "to": to_uri})
