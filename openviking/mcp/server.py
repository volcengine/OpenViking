# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""OpenViking MCP server runtime."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from openviking_cli.utils.logger import get_logger

from .tools import TOOL_DEFINITIONS, dispatch_tool

logger = get_logger(__name__)


class OpenVikingMCPAdapter:
    """Thin adapter exposing OpenViking methods to MCP tool handlers."""

    def __init__(self, client: Any):
        self.client = client

    def list_tools(self) -> List[Dict[str, Any]]:
        return [dict(tool) for tool in TOOL_DEFINITIONS]

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
        return dispatch_tool(name, arguments or {}, self.client)


def run_stdio_server(path: str, config: Optional[str] = None, transport: str = "stdio") -> None:
    """Run OpenViking MCP server in stdio mode."""
    if transport != "stdio":
        raise ValueError("Only stdio transport is supported in MVP")
    if not path:
        raise ValueError("path is required")
    if config:
        os.environ["OPENVIKING_CONFIG_FILE"] = config

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "MCP dependency is missing. Install it with: pip install \"openviking[mcp]\""
        ) from exc

    from openviking.sync_client import SyncOpenViking

    client = SyncOpenViking(path=path)
    adapter = OpenVikingMCPAdapter(client)
    client.initialize()
    logger.info("[MCP] OpenViking client initialized (path=%s)", path)

    mcp = FastMCP("openviking")

    @mcp.tool(description="Semantic search in OpenViking context database.")
    def openviking_find(
        query: str,
        uri: str = "",
        limit: int = 10,
        threshold: float | None = None,
    ) -> str:
        return adapter.call_tool(
            "openviking_find",
            {
                "query": query,
                "uri": uri,
                "limit": limit,
                "threshold": threshold,
            },
        )

    @mcp.tool(description="Read content from OpenViking (L2).")
    def openviking_read(uri: str, offset: int = 0, limit: int = 200) -> str:
        return adapter.call_tool(
            "openviking_read",
            {"uri": uri, "offset": offset, "limit": limit},
        )

    @mcp.tool(description="List directory contents in OpenViking.")
    def openviking_ls(uri: str = "viking://", simple: bool = False, recursive: bool = False) -> str:
        return adapter.call_tool(
            "openviking_ls",
            {"uri": uri, "simple": simple, "recursive": recursive},
        )

    @mcp.tool(description="Read L0 abstract (.abstract.md) for a directory URI.")
    def openviking_abstract(uri: str) -> str:
        return adapter.call_tool("openviking_abstract", {"uri": uri})

    @mcp.tool(description="Read L1 overview (.overview.md) for a directory URI.")
    def openviking_overview(uri: str) -> str:
        return adapter.call_tool("openviking_overview", {"uri": uri})

    try:
        mcp.run(transport="stdio")
    finally:
        client.close()
        logger.info("[MCP] OpenViking client closed")
