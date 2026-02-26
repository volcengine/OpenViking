# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""MCP integration for OpenViking."""

from openviking.mcp.server import OpenVikingMCPAdapter, run_stdio_server
from openviking.mcp.permissions import (
    ACCESS_LEVEL_ORDER,
    MCPAccessLevel,
    access_level_name,
    can_access,
    parse_access_level,
)
from openviking.mcp.tools import TOOL_DEFINITIONS, dispatch_tool, get_tool_definitions

__all__ = [
    "TOOL_DEFINITIONS",
    "get_tool_definitions",
    "dispatch_tool",
    "MCPAccessLevel",
    "ACCESS_LEVEL_ORDER",
    "parse_access_level",
    "access_level_name",
    "can_access",
    "OpenVikingMCPAdapter",
    "run_stdio_server",
]
