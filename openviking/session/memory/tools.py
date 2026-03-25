# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Memory tools - encapsulate VikingFS read operations for ReAct loop.

Reference: bot/vikingbot/agent/tools/base.py design pattern
"""

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Union

from openviking.server.identity import RequestContext
from openviking.session.memory.utils import parse_memory_file_with_fields
from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def create_tool_call_message(
    call_id: Union[str, int],
    tool_name: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Create an assistant role message with tool_calls.

    Args:
        call_id: Unique identifier for the tool call
        tool_name: Name of the tool being called
        params: Parameters for the tool call

    Returns:
        Assistant message with tool_calls field
    """
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": str(call_id),
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(params),
                },
            }
        ],
    }


def create_tool_result_message(
    call_id: Union[str, int],
    result: Any,
) -> Dict[str, Any]:
    """
    Create a tool role message with the tool execution result.

    Args:
        call_id: Unique identifier matching the tool call
        result: Result from the tool execution

    Returns:
        Tool message with result content
    """
    return {
        "role": "tool",
        "tool_call_id": str(call_id),
        "content": json.dumps(result, ensure_ascii=False),
    }


def add_tool_call_pair_to_messages(
    messages: List[Dict[str, Any]],
    call_id: Union[str, int],
    tool_name: str,
    params: Dict[str, Any],
    result: Any,
) -> None:
    """
    Add a pair of tool call + tool result messages to the messages list.

    Args:
        messages: List to append messages to
        call_id: Unique identifier for the tool call
        tool_name: Name of the tool being called
        params: Parameters for the tool call
        result: Result from the tool execution
    """
    messages.append(create_tool_call_message(call_id, tool_name, params))
    messages.append(create_tool_result_message(call_id, result))


def add_tool_call_items_to_messages(
    messages: List[Dict[str, Any]],
    tool_call_items: List[Tuple[Union[str, int], str, Dict[str, Any], Any]],
) -> None:
    """
    Add multiple tool call pairs to the messages list.

    Args:
        messages: List to append messages to
        tool_call_items: List of tuples (call_id, tool_name, params, result)
    """
    for call_id, tool_name, params, result in tool_call_items:
        add_tool_call_pair_to_messages(messages, call_id, tool_name, params, result)


class MemoryTool(ABC):
    """
    Abstract base class for memory tools.

    Similar to bot/vikingbot/agent/tools/base.py Tool,
    but simplified for memory module's internal use.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in function calls."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what the tool does."""
        pass

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """JSON Schema for tool parameters."""
        pass

    @abstractmethod
    async def execute(
        self,
        viking_fs: VikingFS,
        ctx: Optional[RequestContext],
        **kwargs: Any,
    ) -> Any:
        """
        Execute the tool with given parameters.

        Args:
            viking_fs: VikingFS instance
            ctx: Request context
            **kwargs: Tool-specific parameters

        Returns:
            Result of the tool execution (can be dict, list, string, etc.)
        """
        pass

    def to_schema(self) -> Dict[str, Any]:
        """Convert tool to OpenAI function schema format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class MemoryReadTool(MemoryTool):
    """Tool to read single memory file."""

    @property
    def name(self) -> str:
        return "read"

    @property
    def description(self) -> str:
        return "Read single file, offset is start line number (0-indexed), limit is number of lines to read, -1 means read to end"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Memory URI to read, e.g., 'viking://user/user123/memories/profile.md'",
                },
            },
            "required": ["uri"],
        }

    async def execute(
        self,
        viking_fs: VikingFS,
        ctx: Optional[RequestContext],
        **kwargs: Any,
    ) -> Any:
        try:
            uri = kwargs.get("uri", "")
            content = await viking_fs.read_file(
                uri,
                ctx=ctx,
            )
            # Parse MEMORY_FIELDS from comment and return dict directly
            parsed = parse_memory_file_with_fields(content)
            return parsed
        except Exception as e:
            logger.error(f"Failed to execute read: {e}")
            return {"error": str(e)}


class MemorySearchTool(MemoryTool):
    """Tool to perform semantic search."""

    @property
    def name(self) -> str:
        return "search"

    @property
    def description(self) -> str:
        return "Semantic search with session context, target_uri is target directory URI"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query text",
                },
                "target_uri": {
                    "type": "string",
                    "description": "Target directory URI, default empty means search all",
                    "default": "",
                },
                "session_info": {
                    "type": "object",
                    "description": "Session information with latest_archive_overview and current_messages, optional",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return, default 10",
                    "default": 10,
                },
                "score_threshold": {
                    "type": "number",
                    "description": "Score threshold, optional",
                },
                "filter": {
                    "type": "object",
                    "description": "Filter conditions, optional",
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        viking_fs: VikingFS,
        ctx: Optional[RequestContext],
        **kwargs: Any,
    ) -> Any:
        try:
            query = kwargs.get("query", "")
            target_uri = kwargs.get("target_uri", "")
            # If target_uri is empty, use default from ctx
            if (
                not target_uri
                and ctx
                and hasattr(ctx, "default_search_uris")
                and ctx.default_search_uris
            ):
                target_uri = ctx.default_search_uris
            session_info = kwargs.get("session_info")
            limit = kwargs.get("limit", 10)
            score_threshold = kwargs.get("score_threshold")
            filter = kwargs.get("filter")
            search_result = await viking_fs.search(
                query,
                target_uri=target_uri,
                session_info=session_info,
                limit=limit,
                score_threshold=score_threshold,
                filter=filter,
                ctx=ctx,
            )
            return search_result.to_dict()
        except Exception as e:
            logger.error(f"Failed to execute search: {e}")
            return {"error": str(e)}


def _format_size(size_bytes: int) -> str:
    """Format size in bytes to human readable format."""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}M"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f}K"
    else:
        return f"{size_bytes}B"


class MemoryLsTool(MemoryTool):
    """Tool to list directory contents."""

    @property
    def name(self) -> str:
        return "ls"

    @property
    def description(self) -> str:
        return "List directory content, includes abstract field when output='agent'"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Directory URI to list, e.g., 'viking://user/user123/memories'",
                },
            },
            "required": ["uri"],
        }

    async def execute(
        self,
        viking_fs: VikingFS,
        ctx: Optional[RequestContext],
        **kwargs: Any,
    ) -> Any:
        try:
            uri = kwargs.get("uri", "")
            entries = await viking_fs.ls(
                uri,
                output="agent",
                abs_limit=256,
                show_all_hidden=False,
                node_limit=1000,
                ctx=ctx,
            )
            # Format: filename size (e.g., "file.md 1.2K")
            result_lines = []
            for e in entries:
                if not e.get("isDir", False):
                    # Extract name from entry or fallback to uri
                    name = e.get("name", "")
                    if not name:
                        uri = e.get("uri", "")
                        name = uri.rsplit("/", 1)[-1] if "/" in uri else uri
                    size = e.get("size", 0)
                    result_lines.append(f"{name} {_format_size(size)}")
            if not result_lines:
                return "Directory is empty. You can write new files to create memory content."
            return "\n".join(result_lines)
        except Exception as e:
            logger.error(f"Failed to execute ls: {e}")
            return {"error": str(e)}


# Tool registry
MEMORY_TOOLS_REGISTRY: Dict[str, MemoryTool] = {}


def register_tool(tool: MemoryTool) -> None:
    """Register a memory tool."""
    MEMORY_TOOLS_REGISTRY[tool.name] = tool
    logger.debug(f"Registered memory tool: {tool.name}")


def get_tool(name: str) -> Optional[MemoryTool]:
    """Get a memory tool by name."""
    return MEMORY_TOOLS_REGISTRY.get(name)


def list_tools() -> Dict[str, MemoryTool]:
    """List all registered memory tools."""
    return MEMORY_TOOLS_REGISTRY.copy()


# Tools exposed to LLM (not all registered tools are exposed)
LLM_TOOLS = ["read", "search"]


def get_tool_schemas() -> List[Dict[str, Any]]:
    """Get tools exposed to LLM in OpenAI function schema format."""
    return [tool.to_schema() for tool in MEMORY_TOOLS_REGISTRY.values() if tool.name in LLM_TOOLS]


# Register default tools
register_tool(MemoryReadTool())
register_tool(MemorySearchTool())
register_tool(MemoryLsTool())
