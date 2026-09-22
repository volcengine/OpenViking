# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Filesystem endpoints for OpenViking HTTP Server."""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.pyagfs.exceptions import AGFSClientError, AGFSNotFoundError
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.error_mapping import map_exception
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.storage.acl import ResourceAttrs
from openviking.storage.vector_ids import is_vector_record_id
from openviking_cli.exceptions import NotFoundError

router = APIRouter(prefix="/api/v1/fs", tags=["filesystem"])


@router.get("/ls")
async def ls(
    uri: str = Query(..., description="Viking URI"),
    simple: bool = Query(False, description="Return only relative path list"),
    recursive: bool = Query(False, description="List all subdirectories recursively"),
    output: str = Query("agent", description="Output format: original or agent"),
    abs_limit: int = Query(256, description="Abstract limit (only for agent output)"),
    show_all_hidden: bool = Query(False, description="List all hidden files, like -a"),
    node_limit: int = Query(1000, description="Maximum number of nodes to list"),
    offset: int = Query(0, ge=0, description="Number of visible nodes to skip"),
    limit: Optional[int] = Query(None, ge=1, description="Alias for node_limit"),
    sort_by: Optional[Literal["name", "mtime"]] = Query(
        None,
        description="Sort directory and file groups before applying node_limit",
    ),
    sort_order: Literal["asc", "desc"] = Query("asc", description="Sort direction"),
    extra_fields: Optional[list[str]] = Query(
        None, description="Extra fields to include: locked, id, count"
    ),
    tags: list[str] | None = Query(None, description="Only include entries matching all k=v tags"),
    include_tags: bool = Query(False, description="Include tags in each entry"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """List directory contents."""
    service = get_service()
    actual_node_limit = limit if limit is not None else node_limit
    # Resolve path variables
    uri = validate_request_viking_uri(resolve_path_variables(uri), _ctx)
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
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
            extra_fields=extra_fields,
            tags=tags,
            include_tags=include_tags,
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
    offset: int = Query(0, ge=0, description="Number of visible nodes to skip"),
    limit: Optional[int] = Query(None, ge=1, description="Alias for node_limit"),
    level_limit: int = Query(3, description="Maximum depth level to traverse"),
    extra_fields: Optional[list[str]] = Query(
        None, description="Extra fields to include: locked, id, count"
    ),
    tags: list[str] | None = Query(None, description="Only include entries matching all k=v tags"),
    include_tags: bool = Query(False, description="Include tags in each entry"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get directory tree."""
    service = get_service()
    actual_node_limit = limit if limit is not None else node_limit
    # Resolve path variables
    uri = validate_request_viking_uri(resolve_path_variables(uri), _ctx)
    try:
        result = await service.fs.tree(
            uri,
            ctx=_ctx,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
            node_limit=actual_node_limit,
            level_limit=level_limit,
            offset=offset,
            extra_fields=extra_fields,
            tags=tags,
            include_tags=include_tags,
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
    uri: str = Query(..., description="Viking URI or vector record id (32-char hex)"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get resource status."""
    service = get_service()
    # If the argument is a raw 32-hex vector record id, skip URI validation
    # (id lookup + access check happens inside VikingFS.stat).
    if is_vector_record_id(uri):
        resolved = uri
    else:
        resolved = validate_request_viking_uri(resolve_path_variables(uri), _ctx)
    try:
        result = await service.fs.stat(resolved, ctx=_ctx, include_lock_status=True)
        # URI requests use the canonical validated URI. ID requests are resolved
        # inside VikingFS, which returns the corresponding canonical URI.
        response_uri = result.get("uri", resolved)
        return Response(status="ok", result={**result, "uri": response_uri})
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


class MkdirRequest(BaseModel):
    """Request model for mkdir."""

    uri: str
    description: Optional[str] = None
    attrs: ResourceAttrs | None = None


@router.post("/mkdir")
async def mkdir(
    request: MkdirRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Create directory."""
    service = get_service()
    # Resolve path variables
    uri = validate_request_viking_uri(resolve_path_variables(request.uri), _ctx)
    try:
        await service.fs.mkdir(
            uri,
            ctx=_ctx,
            description=request.description,
            **({"attrs": request.attrs} if request.attrs is not None else {}),
        )
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
    uri = validate_request_viking_uri(resolve_path_variables(uri), _ctx)
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


class CpRequest(BaseModel):
    """Request model for cp."""

    from_uri: str
    to_uri: str
    recursive: bool = False


@router.post("/cp")
async def cp(
    request: CpRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Copy a file or directory together with its vector records."""
    service = get_service()
    from_uri = validate_request_viking_uri(
        resolve_path_variables(request.from_uri), _ctx, field_name="from_uri"
    )
    to_uri = validate_request_viking_uri(
        resolve_path_variables(request.to_uri), _ctx, field_name="to_uri"
    )
    try:
        result = await service.fs.cp(
            from_uri,
            to_uri,
            recursive=request.recursive,
            ctx=_ctx,
        )
    except AGFSNotFoundError:
        raise NotFoundError(from_uri, "file")
    except AGFSClientError as exc:
        mapped = map_exception(exc, resource=from_uri, resource_type="file")
        if mapped is not None:
            raise mapped from exc
        raise
    except Exception as exc:
        mapped = map_exception(exc, resource=from_uri)
        if mapped is not None:
            raise mapped from exc
        raise

    response_result = dict(result or {})
    response_result.setdefault("from", from_uri)
    response_result.setdefault("to", to_uri)
    response_result.setdefault("recursive", request.recursive)
    return Response(status="ok", result=response_result)


@router.post("/mv")
async def mv(
    request: MvRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Move resource."""
    service = get_service()
    # Resolve path variables
    from_uri = validate_request_viking_uri(
        resolve_path_variables(request.from_uri), _ctx, field_name="from_uri"
    )
    to_uri = validate_request_viking_uri(
        resolve_path_variables(request.to_uri), _ctx, field_name="to_uri"
    )
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
