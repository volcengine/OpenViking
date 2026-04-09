# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory tools - encapsulate VikingFS read operations for ReAct loop.

Reference: bot/vikingbot/agent/tools/base.py design pattern
"""

import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from openviking.session.memory.utils import parse_memory_file_with_fields
from openviking.session.memory.utils.content import truncate_content
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import tracer
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.server.identity import ToolContext

logger = get_logger(__name__)


def optimize_search_result(result: Any, limit: int = 10) -> Any:
    """优化搜索结果以减少 Token 消耗，并过滤掉抽象文件。"""
    if isinstance(result, dict) and "error" in result:
        return {"error": extract_error_summary(result["error"])}
    if isinstance(result, dict) and "memories" in result:
        filtered = [
            item
            for item in result["memories"]
            if not (
                item.get("uri", "").endswith(".abstract.md")
                or item.get("uri", "").endswith(".overview.md")
            )
        ]
        return [{"uri": item["uri"], "score": item["score"]} for item in filtered[:limit]]
    return []


def optimize_tool_result(tool_name: str, result: Any) -> Any:
    """优化工具结果以减少 Token 消耗。"""
    if isinstance(result, dict) and "error" in result:
        return {"error": extract_error_summary(result["error"])}
    if tool_name == "search" and isinstance(result, dict) and "memories" in result:
        return optimize_search_result(result)
    # 对 read 工具返回的 dict，如果包含 content 字段，则截断 content
    if tool_name == "read" and isinstance(result, dict) and "content" in result:
        result = result.copy()
        result["content"] = truncate_content(result["content"])
    return result


def extract_error_summary(error: str) -> str:
    if "File not found" in error:
        return "File not found"
    if "Permission denied" in error:
        return "Permission denied"
    if "Timeout" in error:
        return "Timeout"
    return error[:50]


def add_tool_call_pair_to_messages(
    messages: List[Dict[str, Any]],
    call_id: Union[str, int],
    tool_name: str,
    params: Dict[str, Any],
    result: Any,
) -> None:
    """Add a tool call pair with optimized format to save tokens."""
    messages.append(
        {
            "role": "user",
            "content": json.dumps(
                {"tool_call_name": tool_name, "args": params, "result": result}, ensure_ascii=False
            ),
        }
    )


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
        ctx: Optional["ToolContext"],
        **kwargs: Any,
    ) -> Any:
        """
        Execute the tool with given parameters.

        Args:
            viking_fs: VikingFS instance
            ctx: Tool context
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
        ctx: Optional["ToolContext"],
        **kwargs: Any,
    ) -> Any:
        uri = kwargs.get("uri", "")
        try:
            content = await viking_fs.read_file(
                uri,
                ctx=ctx.request_ctx,
            )
            # Parse MEMORY_FIELDS from comment and return dict directly
            parsed = parse_memory_file_with_fields(content)
            return parsed
        except NotFoundError as e:
            tracer.info(f"read not found: {uri}")
            return {"error": str(e)}
        except Exception as e:
            tracer.error(f"Failed to execute read: {e}")
            return {"error": str(e)}


class MemorySearchTool(MemoryTool):
    """Tool to perform semantic search."""

    @property
    def name(self) -> str:
        return "search"

    @property
    def description(self) -> str:
        return "Semantic search with session context"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query text",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return, default 10",
                    "default": 10,
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        viking_fs: VikingFS,
        ctx: Optional["ToolContext"],
        **kwargs: Any,
    ) -> Any:
        try:
            query = kwargs.get("query", "")
            # Get target_uri from ctx.default_search_uris
            target_uri = ""
            if ctx and hasattr(ctx, "default_search_uris") and ctx.default_search_uris:
                target_uri = ctx.default_search_uris
            limit = kwargs.get("limit", 10)
            # 多搜索 10 个，过滤抽象文件后再截断
            search_result = await viking_fs.search(
                query,
                target_uri=target_uri,
                limit=limit + 10,
                ctx=ctx,
            )
            return optimize_search_result(search_result.to_dict(), limit=limit)
        except Exception as e:
            tracer.error(f"Failed to execute search: {e}")
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
        ctx: Optional["ToolContext"],
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
                ctx=ctx.request_ctx,
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
            tracer.info(f"Failed to execute ls: {e}")
            return {"error": str(e)}


# Tool registry
MEMORY_TOOLS_REGISTRY: Dict[str, MemoryTool] = {}


def register_tool(tool: MemoryTool) -> None:
    """Register a memory tool."""
    MEMORY_TOOLS_REGISTRY[tool.name] = tool


def get_tool(name: str) -> Optional[MemoryTool]:
    """Get a memory tool by name."""
    return MEMORY_TOOLS_REGISTRY.get(name)


def list_tools() -> Dict[str, MemoryTool]:
    """List all registered memory tools."""
    return MEMORY_TOOLS_REGISTRY.copy()


# Tools exposed to LLM (not all registered tools are exposed)
LLM_TOOLS = ["read"]


def get_tool_schemas() -> List[Dict[str, Any]]:
    """Get tools exposed to LLM in OpenAI function schema format."""
    return [tool.to_schema() for tool in MEMORY_TOOLS_REGISTRY.values() if tool.name in LLM_TOOLS]


# Register default tools
register_tool(MemoryReadTool())
register_tool(MemorySearchTool())
register_tool(MemoryLsTool())
