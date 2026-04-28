# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for memory tools.
"""

import pytest

from openviking.server.identity import RequestContext, Role, ToolContext
from openviking.session.memory.tools import (
    MemoryLsTool,
    MemoryReadTool,
    MemorySearchTool,
    get_tool,
    get_tool_schemas,
    list_tools,
)
from openviking_cli.session.user_id import UserIdentifier


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
        assert "limit" in tool.parameters["properties"]

    @pytest.mark.asyncio
    async def test_search_tool_uses_request_context(self):
        """Test MemorySearchTool passes RequestContext into VikingFS search."""

        class MockSearchResult:
            def to_dict(self):
                return {
                    "memories": [
                        {
                            "uri": "viking://user/test-account/test-user/memories/profile.md",
                            "score": 0.9,
                        }
                    ],
                    "resources": [],
                    "skills": [],
                }

        class MockVikingFS:
            def __init__(self):
                self.received_ctx = None
                self.received_target_uri = None
                self.received_limit = None

            async def search(self, query, target_uri="", limit=10, ctx=None, **kwargs):
                self.received_ctx = ctx
                self.received_target_uri = target_uri
                self.received_limit = limit
                _ = ctx.namespace_policy
                return MockSearchResult()

        request_ctx = RequestContext(
            user=UserIdentifier(
                account_id="test-account",
                user_id="test-user",
                agent_id="test-agent",
            ),
            role=Role.USER,
        )
        tool_ctx = ToolContext(
            request_ctx=request_ctx,
            default_search_uris=["viking://user/test-account/test-user/memories"],
        )
        viking_fs = MockVikingFS()

        result = await MemorySearchTool().execute(
            viking_fs,
            tool_ctx,
            query="profile",
            limit=2,
        )

        assert result == [
            {"uri": "viking://user/test-account/test-user/memories/profile.md", "score": 0.9}
        ]
        assert viking_fs.received_ctx is request_ctx
        assert viking_fs.received_target_uri == tool_ctx.default_search_uris
        assert viking_fs.received_limit == 12

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
        schema_names = [s["function"]["name"] for s in schemas]
        assert "read" in schema_names
        assert all(name in all_tools for name in schema_names)
