# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for memory tools.
"""


from openviking.session.memory.tools import (
    MemoryFindTool,
    MemoryLsTool,
    MemoryReadTool,
    MemoryTreeTool,
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

    def test_find_tool_properties(self):
        """Test MemoryFindTool properties."""
        tool = MemoryFindTool()

        assert tool.name == "find"
        assert "Semantic search" in tool.description
        assert "query" in tool.parameters["properties"]

    def test_ls_tool_properties(self):
        """Test MemoryLsTool properties."""
        tool = MemoryLsTool()

        assert tool.name == "ls"
        assert "List directory" in tool.description
        assert "uri" in tool.parameters["properties"]

    def test_tree_tool_properties(self):
        """Test MemoryTreeTool properties."""
        tool = MemoryTreeTool()

        assert tool.name == "tree"
        assert "Recursively list" in tool.description

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
        assert "find" in all_tools
        assert "ls" in all_tools
        assert "tree" in all_tools

        # Check get_tool
        read_tool = get_tool("read")
        assert read_tool is not None
        assert isinstance(read_tool, MemoryReadTool)

        # Check get_tool_schemas
        schemas = get_tool_schemas()
        assert len(schemas) == 4
        schema_names = [s["function"]["name"] for s in schemas]
        assert "read" in schema_names
        assert "find" in schema_names
        assert "ls" in schema_names
        assert "tree" in schema_names
