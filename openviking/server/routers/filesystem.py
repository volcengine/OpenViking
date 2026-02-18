# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Filesystem endpoints for OpenViking HTTP Server."""

from fastapi import APIRouter, Depends, Query
from pyagfs.exceptions import AGFSClientError
from pydantic import BaseModel

from openviking.server.auth import verify_api_key
from openviking.server.dependencies import get_service
from openviking.server.models import Response
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
    _: bool = Depends(verify_api_key),
):
    """List directory contents."""
    service = get_service()
    result = await service.fs.ls(
        uri,
        recursive=recursive,
        simple=simple,
        output=output,
        abs_limit=abs_limit,
        show_all_hidden=show_all_hidden,
        node_limit=node_limit,
    )
    return Response(status="ok", result=result)


@router.get("/tree")
async def tree(
    uri: str = Query(..., description="Viking URI"),
    output: str = Query("agent", description="Output format: original or agent"),
    abs_limit: int = Query(256, description="Abstract limit (only for agent output)"),
    show_all_hidden: bool = Query(False, description="List all hidden files, like -a"),
    node_limit: int = Query(1000, description="Maximum number of nodes to list"),
    _: bool = Depends(verify_api_key),
):
    """Get directory tree."""
    service = get_service()
    result = await service.fs.tree(
        uri,
        output=output,
        abs_limit=abs_limit,
        show_all_hidden=show_all_hidden,
        node_limit=node_limit,
    )
    return Response(status="ok", result=result)


@router.get("/stat")
async def stat(
    uri: str = Query(..., description="Viking URI"),
    _: bool = Depends(verify_api_key),
):
    """Get resource status."""
    service = get_service()
    try:
        result = await service.fs.stat(uri)
        return Response(status="ok", result=result)
    except AGFSClientError as e:
        if "no such file or directory" in str(e).lower():
            raise NotFoundError(uri, "file")
        raise


class MkdirRequest(BaseModel):
    """Request model for mkdir."""

    uri: str


@router.post("/mkdir")
async def mkdir(
    request: MkdirRequest,
    _: bool = Depends(verify_api_key),
):
    """Create directory."""
    service = get_service()
    await service.fs.mkdir(request.uri)
    return Response(status="ok", result={"uri": request.uri})


@router.delete("")
async def rm(
    uri: str = Query(..., description="Viking URI"),
    recursive: bool = Query(False, description="Remove recursively"),
    _: bool = Depends(verify_api_key),
):
    """Remove resource."""
    service = get_service()
    await service.fs.rm(uri, recursive=recursive)
    return Response(status="ok", result={"uri": uri})


class MvRequest(BaseModel):
    """Request model for mv."""

    from_uri: str
    to_uri: str


@router.post("/mv")
async def mv(
    request: MvRequest,
    _: bool = Depends(verify_api_key),
):
    """Move resource."""
    service = get_service()
    await service.fs.mv(request.from_uri, request.to_uri)
    return Response(status="ok", result={"from": request.from_uri, "to": request.to_uri})
