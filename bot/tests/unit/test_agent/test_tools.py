"""Tests for agent tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vikingbot.agent.tools.base import Tool, ToolResult
from vikingbot.agent.tools.registry import ToolRegistry


class TestTool(Tool):
    """Test tool implementation."""

    name = "test_tool"
    description = "A test tool for testing"
    parameters = {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "Input to process"
            }
        },
        "required": ["input"]
    }

    async def execute(self, input: str) -> str:
        """Execute the test tool."""
        return f"Processed: {input}"


class TestToolRegistry:
    """Tests for ToolRegistry."""

    def test_register_tool(self):
        """Test registering a tool."""
        registry = ToolRegistry()
        tool = TestTool()

        registry.register(tool)

        assert "test_tool" in registry.get_all_tools()
        assert registry.get_tool("test_tool") == tool

    def test_register_duplicate_tool(self):
        """Test registering a duplicate tool raises error."""
        registry = ToolRegistry()
        tool = TestTool()

        registry.register(tool)

        with pytest.raises(ValueError, match="Tool 'test_tool' is already registered"):
            registry.register(tool)

    def test_get_nonexistent_tool(self):
        """Test getting a non-existent tool returns None."""
        registry = ToolRegistry()

        result = registry.get_tool("nonexistent")

        assert result is None

    def test_get_all_tools(self):
        """Test getting all registered tools."""
        registry = ToolRegistry()
        tool1 = TestTool()
        tool2 = TestTool()
        tool2.name = "test_tool_2"

        registry.register(tool1)
        registry.register(tool2)

        tools = registry.get_all_tools()

        assert len(tools) == 2
        assert "test_tool" in tools
        assert "test_tool_2" in tools


class TestToolExecution:
    """Tests for tool execution."""

    @pytest.mark.asyncio
    async def test_tool_execution(self):
        """Test basic tool execution."""
        tool = TestTool()

        result = await tool.execute(input="hello")

        assert result == "Processed: hello"

    @pytest.mark.asyncio
    async def test_tool_execution_error(self):
        """Test tool execution with error."""

        class FailingTool(Tool):
            name = "failing_tool"
            description = "A tool that fails"
            parameters = {"type": "object", "properties": {}}

            async def execute(self, **kwargs) -> str:
                raise ValueError("Intentional failure")

        tool = FailingTool()

        with pytest.raises(ValueError, match="Intentional failure"):
            await tool.execute()


class TestToolSchema:
    """Tests for tool schema validation."""

    def test_tool_schema_structure(self):
        """Test tool schema has required fields."""
        tool = TestTool()

        assert tool.name
        assert tool.description
        assert isinstance(tool.parameters, dict)
        assert tool.parameters.get("type") == "object"

    def test_tool_parameters_schema(self):
        """Test tool parameters follow JSON schema."""
        tool = TestTool()

        params = tool.parameters
        assert "properties" in params
        assert "input" in params["properties"]
        assert params["properties"]["input"]["type"] == "string"
        assert "required" in params
        assert "input" in params["required"]
