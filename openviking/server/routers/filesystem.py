# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Filesystem endpoints for OpenViking HTTP Server."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from openviking.pyagfs.exceptions import AGFSClientError
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.schemas import ExcludeNoneRoute, URIRef
from openviking.server.schemas.filesystem import FileStat, FromTo, FSListResult
from openviking_cli.exceptions import NotFoundError

router = APIRouter(
    prefix="/api/v1/fs",
    tags=["filesystem"],
    route_class=ExcludeNoneRoute,
)


_FSLIST_EXAMPLES = {
    "simple": {
        "summary": "simple=true — list of URI strings",
        "description": (
            "When the request sets ``simple=true`` the response "
            "``result`` is a flat list of URI strings. Use this mode "
            "when only paths are needed."
        ),
        "value": {
            "status": "ok",
            "result": [
                "viking://resources/docs/intro.md",
                "viking://resources/docs/architecture.md",
            ],
        },
    },
    "detailed": {
        "summary": "simple=false (default) — list of FileStat entries",
        "description": (
            "When the request leaves ``simple`` unset or sets it to "
            "``false`` the response ``result`` is a list of FileStat "
            "objects. Fields that AGFS did not populate for the entry "
            "are omitted from the object."
        ),
        "value": {
            "status": "ok",
            "result": [
                {
                    "name": "intro.md",
                    "size": 1024,
                    "mode": 644,
                    "modTime": "2026-04-10T12:30:00Z",
                    "isDir": False,
                    "uri": "viking://resources/docs/intro.md",
                    "rel_path": "docs/intro.md",
                    "abstract": "Overview of the platform",
                },
                {
                    "name": "architecture",
                    "size": 0,
                    "mode": 755,
                    "modTime": "2026-04-09T08:00:00Z",
                    "isDir": True,
                    "uri": "viking://resources/docs/architecture",
                    "rel_path": "docs/architecture",
                },
            ],
        },
    },
}

_FSLIST_RESPONSES = {
    200: {
        "description": (
            "Polymorphic response: ``List[str]`` when the request sets "
            "``simple=true``, otherwise ``List[FileStat]``."
        ),
        "content": {"application/json": {"examples": _FSLIST_EXAMPLES}},
    }
}


@router.get(
    "/ls",
    response_model=Response[FSListResult],
    responses=_FSLIST_RESPONSES,
)
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
) -> Response[FSListResult]:
    """List directory contents.

    Return shape is polymorphic: ``simple=True`` yields a ``List[str]`` of
    URIs; otherwise a ``List[FileStat]`` with per-entry metadata.
    """
    service = get_service()
    actual_node_limit = limit if limit is not None else node_limit
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
    return Response(status="ok", result=result)


@router.get(
    "/tree",
    response_model=Response[FSListResult],
    responses=_FSLIST_RESPONSES,
)
async def tree(
    uri: str = Query(..., description="Viking URI"),
    output: str = Query("agent", description="Output format: original or agent"),
    abs_limit: int = Query(256, description="Abstract limit (only for agent output)"),
    show_all_hidden: bool = Query(False, description="List all hidden files, like -a"),
    node_limit: int = Query(1000, description="Maximum number of nodes to list"),
    limit: Optional[int] = Query(None, description="Alias for node_limit"),
    level_limit: int = Query(3, description="Maximum depth level to traverse"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[FSListResult]:
    """Get directory tree.

    Return shape matches ``/ls`` (flat list, not a recursive node tree).
    Clients reconstruct hierarchy from the ``rel_path`` field of each
    entry.
    """
    service = get_service()
    actual_node_limit = limit if limit is not None else node_limit
    result = await service.fs.tree(
        uri,
        ctx=_ctx,
        output=output,
        abs_limit=abs_limit,
        show_all_hidden=show_all_hidden,
        node_limit=actual_node_limit,
        level_limit=level_limit,
    )
    return Response(status="ok", result=result)


@router.get("/stat", response_model=Response[FileStat])
async def stat(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[FileStat]:
    """Get resource status."""
    service = get_service()
    try:
        result = await service.fs.stat(uri, ctx=_ctx)
        return Response(status="ok", result=FileStat.model_validate(result))
    except AGFSClientError as e:
        err_msg = str(e).lower()
        if "not found" in err_msg or "no such file or directory" in err_msg:
            raise NotFoundError(uri, "file")
        raise


class MkdirRequest(BaseModel):
    """Request model for mkdir."""

    uri: str


@router.post("/mkdir", response_model=Response[URIRef])
async def mkdir(
    request: MkdirRequest,
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[URIRef]:
    """Create directory."""
    service = get_service()
    await service.fs.mkdir(request.uri, ctx=_ctx)
    return Response(status="ok", result=URIRef(uri=request.uri))


@router.delete("", response_model=Response[URIRef])
async def rm(
    uri: str = Query(..., description="Viking URI"),
    recursive: bool = Query(False, description="Remove recursively"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[URIRef]:
    """Remove resource."""
    service = get_service()
    await service.fs.rm(uri, ctx=_ctx, recursive=recursive)
    return Response(status="ok", result=URIRef(uri=uri))


class MvRequest(BaseModel):
    """Request model for mv."""

    from_uri: str
    to_uri: str


@router.post("/mv", response_model=Response[FromTo])
async def mv(
    request: MvRequest,
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[FromTo]:
    """Move resource."""
    service = get_service()
    await service.fs.mv(request.from_uri, request.to_uri, ctx=_ctx)
    return Response(
        status="ok",
        result=FromTo(from_=request.from_uri, to=request.to_uri),
    )
