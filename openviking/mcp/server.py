# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""OpenViking MCP server runtime."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from openviking_cli.utils.logger import get_logger

from .permissions import MCPAccessLevel, access_level_name, can_access, parse_access_level
from .tools import dispatch_tool, get_tool_definitions

logger = get_logger(__name__)


class OpenVikingMCPAdapter:
    """Thin adapter exposing OpenViking methods to MCP tool handlers."""

    def __init__(self, client: Any, access_level: MCPAccessLevel | str = MCPAccessLevel.READONLY):
        self.client = client
        self.access_level = parse_access_level(access_level)

    def list_tools(self) -> List[Dict[str, Any]]:
        return get_tool_definitions(access_level=self.access_level)

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
        return dispatch_tool(name, arguments or {}, self.client, access_level=self.access_level)

    def can_access(self, required: MCPAccessLevel | str) -> bool:
        return can_access(self.access_level, required)


def run_stdio_server(
    path: str,
    config: Optional[str] = None,
    transport: str = "stdio",
    access_level: MCPAccessLevel | str = MCPAccessLevel.READONLY,
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

    resolved_access = parse_access_level(access_level)
    client = SyncOpenViking(path=path)
    adapter = OpenVikingMCPAdapter(client, access_level=resolved_access)
    client.initialize()
    logger.info(
        "[MCP] OpenViking client initialized (path=%s, access_level=%s)",
        path,
        access_level_name(resolved_access),
    )

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
    def openviking_ls(
        uri: str = "viking://",
        simple: bool = False,
        recursive: bool = False,
        output: str = "agent",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> str:
        return adapter.call_tool(
            "openviking_ls",
            {
                "uri": uri,
                "simple": simple,
                "recursive": recursive,
                "output": output,
                "abs_limit": abs_limit,
                "show_all_hidden": show_all_hidden,
                "node_limit": node_limit,
            },
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

    @mcp.tool(description="List sessions.")
    def openviking_session_list() -> str:
        return adapter.call_tool("openviking_session_list", {})

    @mcp.tool(description="Get session details.")
    def openviking_session_get(session_id: str) -> str:
        return adapter.call_tool("openviking_session_get", {"session_id": session_id})

    @mcp.tool(description="List relations of a resource.")
    def openviking_relation_list(uri: str) -> str:
        return adapter.call_tool("openviking_relation_list", {"uri": uri})

    if adapter.can_access("ingest"):

        @mcp.tool(description="Create a new session.")
        def openviking_session_create() -> str:
            return adapter.call_tool("openviking_session_create", {})

        @mcp.tool(description="Add a message to a session.")
        def openviking_session_add_message(
            session_id: str,
            role: str,
            content: str | None = None,
            parts: list[dict] | None = None,
        ) -> str:
            return adapter.call_tool(
                "openviking_session_add_message",
                {
                    "session_id": session_id,
                    "role": role,
                    "content": content,
                    "parts": parts,
                },
            )

        @mcp.tool(description="Commit a session (archive and extract memories).")
        def openviking_session_commit(session_id: str) -> str:
            return adapter.call_tool("openviking_session_commit", {"session_id": session_id})

        @mcp.tool(description="Add local path or URL as resource into OpenViking.")
        def openviking_resource_add(
            path: str,
            to: str | None = None,
            reason: str = "",
            instruction: str = "",
            wait: bool = False,
            timeout: float | None = None,
        ) -> str:
            return adapter.call_tool(
                "openviking_resource_add",
                {
                    "path": path,
                    "to": to,
                    "reason": reason,
                    "instruction": instruction,
                    "wait": wait,
                    "timeout": timeout,
                },
            )

        @mcp.tool(
            description=(
                "[Deprecated alias] Add local path or URL as resource into OpenViking. "
                "Use openviking_resource_add instead."
            )
        )
        def openviking_add_resource(
            path: str,
            to: str | None = None,
            reason: str = "",
            instruction: str = "",
            wait: bool = False,
            timeout: float | None = None,
        ) -> str:
            return adapter.call_tool(
                "openviking_resource_add",
                {
                    "path": path,
                    "to": to,
                    "reason": reason,
                    "instruction": instruction,
                    "wait": wait,
                    "timeout": timeout,
                },
            )

        @mcp.tool(description="Add a skill into OpenViking.")
        def openviking_resource_add_skill(
            data: str,
            wait: bool = False,
            timeout: float | None = None,
        ) -> str:
            return adapter.call_tool(
                "openviking_resource_add_skill",
                {"data": data, "wait": wait, "timeout": timeout},
            )

    if adapter.can_access("mutate"):

        @mcp.tool(description="Create relation links from one URI to one or more targets.")
        def openviking_relation_link(
            from_uri: str,
            uris: str | list[str],
            reason: str = "",
        ) -> str:
            return adapter.call_tool(
                "openviking_relation_link",
                {"from_uri": from_uri, "uris": uris, "reason": reason},
            )

        @mcp.tool(description="Remove a relation link.")
        def openviking_relation_unlink(from_uri: str, uri: str) -> str:
            return adapter.call_tool(
                "openviking_relation_unlink",
                {"from_uri": from_uri, "uri": uri},
            )

        @mcp.tool(description="Create a directory.")
        def openviking_fs_mkdir(uri: str) -> str:
            return adapter.call_tool("openviking_fs_mkdir", {"uri": uri})

        @mcp.tool(description="Move or rename a resource.")
        def openviking_fs_mv(from_uri: str, to_uri: str) -> str:
            return adapter.call_tool("openviking_fs_mv", {"from_uri": from_uri, "to_uri": to_uri})

    if adapter.can_access("admin"):

        @mcp.tool(description="Delete a session.")
        def openviking_session_delete(session_id: str) -> str:
            return adapter.call_tool("openviking_session_delete", {"session_id": session_id})

        @mcp.tool(description="Remove a resource.")
        def openviking_fs_rm(uri: str, recursive: bool = False) -> str:
            return adapter.call_tool("openviking_fs_rm", {"uri": uri, "recursive": recursive})

        @mcp.tool(description="Export context as .ovpack.")
        def openviking_pack_export(uri: str, to: str) -> str:
            return adapter.call_tool("openviking_pack_export", {"uri": uri, "to": to})

        @mcp.tool(description="Import .ovpack into target URI.")
        def openviking_pack_import(
            file_path: str,
            target_uri: str,
            force: bool = False,
            vectorize: bool = True,
        ) -> str:
            return adapter.call_tool(
                "openviking_pack_import",
                {
                    "file_path": file_path,
                    "target_uri": target_uri,
                    "force": force,
                    "vectorize": vectorize,
                },
            )

    try:
        mcp.run(transport="stdio")
    finally:
        client.close()
        logger.info("[MCP] OpenViking client closed")
