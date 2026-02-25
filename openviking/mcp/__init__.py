# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""MCP integration for OpenViking."""

from openviking.mcp.server import OpenVikingMCPAdapter, run_stdio_server
from openviking.mcp.tools import TOOL_DEFINITIONS, dispatch_tool

__all__ = [
    "TOOL_DEFINITIONS",
    "dispatch_tool",
    "OpenVikingMCPAdapter",
    "run_stdio_server",
]
