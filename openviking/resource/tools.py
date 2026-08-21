# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Resource tools - expose resource services as LLM-callable tools for ReAct loop.

"""

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# Default cap for a single tool result before truncation (chars).
DEFAULT_MAX_RESULT_CHARS = 8000


def _serialize_result(result: Any) -> str:
    """Serialize tool result to a compact JSON string."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


def _truncate_result(result_str: str, max_chars: int) -> str:
    """Truncate result string to max_chars, with a tail indicator."""
    if len(result_str) <= max_chars:
        return result_str
    head = result_str[:max_chars]
    return f"{head}\n...[truncated, {len(result_str) - max_chars} more chars]"


class ResourceTool(ABC):
    """Abstract base class for resource tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in function calls (must be unique in registry)."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description shown to the LLM."""
        pass

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """JSON Schema describing the tool's parameters."""
        pass

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """
        Execute the tool with LLM-provided parameters.

        Args:
            **kwargs: Parameters validated against ``self.parameters``.

        Returns:
            Any JSON-serializable value (dict, list, str, etc.).
        """
        pass

    @property
    def max_result_chars(self) -> int:
        """Max chars of this tool's result before truncation."""
        return DEFAULT_MAX_RESULT_CHARS

    def to_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI function-calling schema format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }



_RESOURCE_TOOL_REGISTRY: Dict[str, ResourceTool] = {}
_LLM_EXPOSED_TOOLS: List[str] = []


def register_resource_tool(tool: ResourceTool) -> None:
    """Register a resource tool instance."""
    name = tool.name
    if name in _RESOURCE_TOOL_REGISTRY:
        logger.warning("Resource tool '%s' already registered, overwriting", name)
    _RESOURCE_TOOL_REGISTRY[name] = tool
    logger.info("Registered resource tool: %s", name)


def get_resource_tool(name: str) -> Optional[ResourceTool]:
    """Get a registered resource tool by name."""
    return _RESOURCE_TOOL_REGISTRY.get(name)


def list_resource_tools() -> List[str]:
    """List all registered resource tool names."""
    return list(_RESOURCE_TOOL_REGISTRY.keys())


def expose_to_llm(tool_name: str) -> None:
    """
    Mark a registered tool as callable by the LLM in the ReAct loop.

    Raises:
        KeyError: if ``tool_name`` is not registered.
    """
    if tool_name not in _RESOURCE_TOOL_REGISTRY:
        raise KeyError(
            f"Cannot expose unregistered tool '{tool_name}'. "
            f"Register it first with register_resource_tool()."
        )
    if tool_name not in _LLM_EXPOSED_TOOLS:
        _LLM_EXPOSED_TOOLS.append(tool_name)
        logger.info("Exposed resource tool to LLM: %s", tool_name)


def hide_from_llm(tool_name: str) -> None:
    """Remove a tool from the LLM-visible whitelist (keeps it registered)."""
    if tool_name in _LLM_EXPOSED_TOOLS:
        _LLM_EXPOSED_TOOLS.remove(tool_name)


def get_resource_tool_schemas() -> List[Dict[str, Any]]:
    """
    Get schemas for all LLM-exposed resource tools in OpenAI format.

    Returns an empty list when no tools are exposed — callers should fall
    back to the plain prompt path in that case.
    """
    schemas: List[Dict[str, Any]] = []
    for name in _LLM_EXPOSED_TOOLS:
        tool = _RESOURCE_TOOL_REGISTRY.get(name)
        if tool is not None:
            schemas.append(tool.to_schema())
    return schemas


def reset_resource_tools() -> None:
    """Clear all registered tools and the LLM whitelist."""
    _RESOURCE_TOOL_REGISTRY.clear()
    _LLM_EXPOSED_TOOLS.clear()


async def execute_resource_tool(tool_name: str, **kwargs: Any) -> str:
    """
    Execute a resource tool by name and return a truncated JSON string.

    Args:
        tool_name: Registered tool name.
        **kwargs: LLM-provided parameters.

    Returns:
        Truncated, serialized result string. On error, returns
        ``{"error": "..."}`` JSON.
    """
    tool = _RESOURCE_TOOL_REGISTRY.get(tool_name)
    if tool is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"}, ensure_ascii=False)
    try:
        result = await tool.execute(**kwargs)
    except Exception as e:
        tracer.error(f"Resource tool '{tool_name}' execution failed: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    result_str = _serialize_result(result)
    return _truncate_result(result_str, tool.max_result_chars)
