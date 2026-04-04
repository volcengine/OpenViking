# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for memory tools.
"""

from openviking.session.memory.tools import (
    MemoryLsTool,
    MemoryReadTool,
    MemorySearchTool,
    get_tool,
    get_tool_schemas,
    list_tools,
)


class TestMemoryTools:
    """Tests for memory tools."""

    def test_read_tool_properties(self):
        """Test MemoryReadTool properties."""
        tool = MemoryReadTool()

        assert tool.name == "read"
        assert "Read single file" in tool.description
        assert "uri" in tool.parameters["properties"]
        assert "required" in tool.parameters

    def test_search_tool_properties(self):
        """Test MemorySearchTool properties."""
        tool = MemorySearchTool()

        assert tool.name == "search"
        assert "Semantic search" in tool.description
        assert "query" in tool.parameters["properties"]
        assert "session_info" in tool.parameters["properties"]

    def test_ls_tool_properties(self):
        """Test MemoryLsTool properties."""
        tool = MemoryLsTool()

        assert tool.name == "ls"
        assert "List directory" in tool.description
        assert "uri" in tool.parameters["properties"]

    def test_to_schema(self):
        """Test tool to_schema method."""
        tool = MemoryReadTool()
        schema = tool.to_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "read"
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]

    def test_tool_registry(self):
        """Test tool registry functions."""
        # Check that default tools are registered
        all_tools = list_tools()
        assert "read" in all_tools
        assert "search" in all_tools
        assert "ls" in all_tools

        # Check get_tool
        read_tool = get_tool("read")
        assert read_tool is not None
        assert isinstance(read_tool, MemoryReadTool)

        # Check get_tool_schemas
        schemas = get_tool_schemas()
        assert len(schemas) == 3
        schema_names = [s["function"]["name"] for s in schemas]
        assert "read" in schema_names
        assert "search" in schema_names
        assert "ls" in schema_names
