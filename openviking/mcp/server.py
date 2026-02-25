# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""OpenViking MCP server runtime."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from openviking_cli.utils.logger import get_logger

from .tools import dispatch_tool, get_tool_definitions

logger = get_logger(__name__)


class OpenVikingMCPAdapter:
    """Thin adapter exposing OpenViking methods to MCP tool handlers."""

    def __init__(self, client: Any, allow_write: bool = False):
        self.client = client
        self.allow_write = allow_write

    def list_tools(self) -> List[Dict[str, Any]]:
        return get_tool_definitions(include_write=self.allow_write)

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
        return dispatch_tool(name, arguments or {}, self.client, allow_write=self.allow_write)


def run_stdio_server(
    path: str,
    config: Optional[str] = None,
    transport: str = "stdio",
    enable_write: bool = False,
) -> None:
    """Run OpenViking MCP server in stdio mode."""
    if transport != "stdio":
        raise ValueError("Only stdio transport is supported in V1")
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
    adapter = OpenVikingMCPAdapter(client, allow_write=enable_write)
    client.initialize()
    logger.info("[MCP] OpenViking client initialized (path=%s, enable_write=%s)", path, enable_write)

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

    @mcp.tool(description="Context-aware retrieval in OpenViking.")
    def openviking_search(
        query: str,
        uri: str = "",
        session_id: str | None = None,
        limit: int = 10,
        threshold: float | None = None,
    ) -> str:
        return adapter.call_tool(
            "openviking_search",
            {
                "query": query,
                "uri": uri,
                "session_id": session_id,
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

    @mcp.tool(description="Wait until queued async processing completes.")
    def openviking_wait_processed(timeout: float | None = None) -> str:
        return adapter.call_tool("openviking_wait_processed", {"timeout": timeout})

    @mcp.tool(description="Get resource metadata and status.")
    def openviking_stat(uri: str) -> str:
        return adapter.call_tool("openviking_stat", {"uri": uri})

    @mcp.tool(description="Get directory tree in agent-friendly format.")
    def openviking_tree(
        uri: str,
        abs_limit: int = 128,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> str:
        return adapter.call_tool(
            "openviking_tree",
            {
                "uri": uri,
                "abs_limit": abs_limit,
                "show_all_hidden": show_all_hidden,
                "node_limit": node_limit,
            },
        )

    @mcp.tool(description="Search text pattern in files under a URI.")
    def openviking_grep(pattern: str, uri: str = "viking://", ignore_case: bool = False) -> str:
        return adapter.call_tool(
            "openviking_grep",
            {"pattern": pattern, "uri": uri, "ignore_case": ignore_case},
        )

    @mcp.tool(description="Search files by glob pattern under a URI.")
    def openviking_glob(pattern: str, uri: str = "viking://") -> str:
        return adapter.call_tool("openviking_glob", {"pattern": pattern, "uri": uri})

    @mcp.tool(description="Get OpenViking component status.")
    def openviking_status() -> str:
        return adapter.call_tool("openviking_status", {})

    @mcp.tool(description="Get quick health check result.")
    def openviking_health() -> str:
        return adapter.call_tool("openviking_health", {})

    if enable_write:

        @mcp.tool(description="Add local path or URL as resource into OpenViking.")
        def openviking_add_resource(
            path: str,
            to: str | None = None,
            reason: str = "",
            instruction: str = "",
            wait: bool = False,
            timeout: float | None = None,
        ) -> str:
            return adapter.call_tool(
                "openviking_add_resource",
                {
                    "path": path,
                    "to": to,
                    "reason": reason,
                    "instruction": instruction,
                    "wait": wait,
                    "timeout": timeout,
                },
            )

    try:
        mcp.run(transport="stdio")
    finally:
        client.close()
        logger.info("[MCP] OpenViking client closed")
