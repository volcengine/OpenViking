# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""MCP server registry and tool invocation endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.service.mcp_service import MCPToolMount

router = APIRouter(prefix="/api/v1/mcps", tags=["mcps"])


class AddMCPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    target_uri: str | None = None
    wait: bool = False


class SyncMCPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_uri: str | None = None
    wait: bool = False


class FindMCPToolsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    limit: int = Field(default=10, ge=1, le=100)
    target_uri: str | None = None


class InvokeMCPToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class MCPToolMountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_uri: str
    node_uri: str
    title: str = Field(min_length=1)
    description: str | None = None
    timeout_seconds: int = Field(default=60, gt=0)
    arguments: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] | None = None


class MountMCPToolRequest(MCPToolMountRequest):
    wait: bool = False


class BatchMountMCPToolsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mounts: list[MCPToolMountRequest] = Field(min_length=1)
    wait: bool = False


class BatchUnmountMCPToolsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_uris: list[str] = Field(min_length=1)
    wait: bool = False


@router.post("")
async def add_mcp(
    body: AddMCPRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    kwargs: dict[str, Any] = {"wait": body.wait}
    if body.target_uri is not None:
        kwargs["target_uri"] = body.target_uri
    result = await get_service().mcps.add(
        body.name,
        body.url,
        body.headers,
        ctx,
        **kwargs,
    )
    return Response(status="ok", result=result)


@router.post("/mount")
async def mount_mcp_tool(
    body: MountMCPToolRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    result = await get_service().mcps.mount(
        body.tool_uri,
        body.node_uri,
        body.title,
        body.description,
        body.timeout_seconds,
        body.arguments,
        ctx,
        tags=body.tags,
        wait=body.wait,
    )
    return Response(status="ok", result=result)


@router.post("/batch-mount")
async def batch_mount_mcp_tools(
    body: BatchMountMCPToolsRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    mounts = [
        MCPToolMount(
            tool_uri=mount.tool_uri,
            node_uri=mount.node_uri,
            title=mount.title,
            description=mount.description,
            timeout_seconds=mount.timeout_seconds,
            arguments=mount.arguments,
            tags=mount.tags,
        )
        for mount in body.mounts
    ]
    result = await get_service().mcps.batch_mount(mounts, ctx, wait=body.wait)
    return Response(status="ok", result=result)


@router.delete("/mount")
async def unmount_mcp_tool(
    node_uri: str = Query(),
    wait: bool = Query(default=False),
    ctx: RequestContext = Depends(get_request_context),
):
    result = await get_service().mcps.unmount(node_uri, ctx, wait=wait)
    return Response(status="ok", result=result)


@router.post("/batch-unmount")
async def batch_unmount_mcp_tools(
    body: BatchUnmountMCPToolsRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    result = await get_service().mcps.batch_unmount(
        body.node_uris,
        ctx,
        wait=body.wait,
    )
    return Response(status="ok", result=result)


@router.get("/mounts")
async def list_mcp_mounts(
    mcp: str = Query(),
    tool: str | None = Query(default=None),
    target_uri: str | None = Query(default=None),
    ctx: RequestContext = Depends(get_request_context),
):
    kwargs = {"target_uri": target_uri} if target_uri is not None else {}
    result = await get_service().mcps.mounts(mcp, tool, ctx, **kwargs)
    return Response(status="ok", result=result)


@router.delete("/{name}")
async def remove_mcp(
    name: str,
    target_uri: str | None = Query(default=None),
    ctx: RequestContext = Depends(get_request_context),
):
    kwargs = {"target_uri": target_uri} if target_uri is not None else {}
    result = await get_service().mcps.remove(name, ctx, **kwargs)
    return Response(status="ok", result=result)


@router.get("")
async def list_mcps(
    target_uri: str | None = Query(default=None),
    ctx: RequestContext = Depends(get_request_context),
):
    kwargs = {"target_uri": target_uri} if target_uri is not None else {}
    result = await get_service().mcps.list(ctx, **kwargs)
    return Response(status="ok", result=result)


@router.post("/find")
async def find_mcp_tools(
    body: FindMCPToolsRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    kwargs = {"include_definition": True}
    if body.target_uri is not None:
        kwargs["target_uri"] = body.target_uri
    result = await get_service().mcps.find(
        body.query,
        body.limit,
        ctx,
        **kwargs,
    )
    return Response(status="ok", result=result)


@router.post("/invoke")
async def invoke_mcp_tool(
    body: InvokeMCPToolRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    result = await get_service().mcps.invoke(body.uri, body.arguments, ctx)
    return Response(status="ok", result=result)


@router.get("/status")
async def mcp_status(
    name: str = Query(),
    target_uri: str | None = Query(default=None),
    ctx: RequestContext = Depends(get_request_context),
):
    kwargs = {"target_uri": target_uri} if target_uri is not None else {}
    result = await get_service().mcps.status(name, ctx, **kwargs)
    return Response(status="ok", result=result)


@router.get("/{name}")
async def show_mcp(
    name: str,
    target_uri: str | None = Query(default=None),
    ctx: RequestContext = Depends(get_request_context),
):
    kwargs = {"target_uri": target_uri} if target_uri is not None else {}
    result = await get_service().mcps.show(name, ctx, **kwargs)
    return Response(status="ok", result=result)


@router.post("/validate")
async def validate_mcp(
    body: AddMCPRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    result = await get_service().mcps.validate(body.name, body.url, body.headers)
    return Response(status="ok", result=result)


@router.post("/{name}/sync")
async def sync_mcp(
    name: str,
    body: SyncMCPRequest | None = None,
    ctx: RequestContext = Depends(get_request_context),
):
    kwargs: dict[str, Any] = {"wait": body.wait if body is not None else False}
    if body is not None and body.target_uri is not None:
        kwargs["target_uri"] = body.target_uri
    result = await get_service().mcps.sync(
        name,
        ctx,
        **kwargs,
    )
    return Response(status="ok", result=result)
