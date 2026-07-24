# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource monitoring and management module."""

from openviking.resource.tools import (
    DEFAULT_MAX_RESULT_CHARS,
    ResourceTool,
    execute_resource_tool,
    expose_to_llm,
    get_resource_tool,
    get_resource_tool_schemas,
    hide_from_llm,
    list_resource_tools,
    register_resource_tool,
    reset_resource_tools,
)
from openviking.resource.resource_loop import run_resource_tool_loop
from openviking.resource.watch_manager import WatchManager, WatchTask

__all__ = [
    "DEFAULT_MAX_RESULT_CHARS",
    "ResourceTool",
    "WatchManager",
    "WatchTask",
    "execute_resource_tool",
    "expose_to_llm",
    "get_resource_tool",
    "get_resource_tool_schemas",
    "hide_from_llm",
    "list_resource_tools",
    "register_resource_tool",
    "reset_resource_tools",
    "run_resource_tool_loop",
]
