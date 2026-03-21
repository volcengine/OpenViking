# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Memory tools - encapsulate VikingFS read operations for ReAct loop.

Reference: bot/vikingbot/agent/tools/base.py design pattern
"""

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


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
    ) -> str:
        """
        Execute the tool with given parameters.

        Args:
            viking_fs: VikingFS instance
            ctx: Request context
            **kwargs: Tool-specific parameters

        Returns:
            String result of the tool execution
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
    ) -> str:
        try:
            uri = kwargs.get("uri", "")
            content = await viking_fs.read_file(
                uri,
                ctx=ctx,
            )
            return content
        except Exception as e:
            logger.error(f"Failed to execute read: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class MemoryFindTool(MemoryTool):
    """Tool to perform semantic search."""

    @property
    def name(self) -> str:
        return "find"

    @property
    def description(self) -> str:
        return "Semantic search, target_uri is target directory URI"

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
    ) -> str:
        try:
            query = kwargs.get("query", "")
            target_uri = kwargs.get("target_uri", "")
            limit = kwargs.get("limit", 10)
            score_threshold = kwargs.get("score_threshold")
            filter = kwargs.get("filter")
            find_result = await viking_fs.find(
                query,
                target_uri=target_uri,
                limit=limit,
                score_threshold=score_threshold,
                filter=filter,
                ctx=ctx,
            )
            return json.dumps(find_result, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to execute find: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)


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
    ) -> str:
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
            # ls -F style: files only (no directories), with type indicators
            # For our use case, we just filter to files only
            files_only = [f'{e.get("name")} # {e.get("abstract")}' for e in entries if not e.get("isDir", False)]
            return '\n'.join(files_only)
        except Exception as e:
            logger.error(f"Failed to execute ls: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)




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


def get_tool_schemas() -> List[Dict[str, Any]]:
    """Get all registered tools in OpenAI function schema format."""
    return [tool.to_schema() for tool in MEMORY_TOOLS_REGISTRY.values()]


# Register default tools
register_tool(MemoryReadTool())
register_tool(MemoryFindTool())
register_tool(MemoryLsTool())
