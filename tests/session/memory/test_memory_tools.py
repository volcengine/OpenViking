# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for memory tools.
"""

import pytest

from openviking.server.identity import RequestContext, Role, ToolContext
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.tools import (
    MEMORY_TOOLS_REGISTRY,
    MemoryLsTool,
    MemoryReadTool,
    MemorySearchTool,
    get_tool,
    get_tool_schemas,
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
        assert "offset" in tool.parameters["properties"]
        assert "limit" in tool.parameters["properties"]
        assert "required" in tool.parameters

    @pytest.mark.asyncio
    async def test_read_tool_strips_local_memory_links_from_llm_content(self):
        class MockPageIdMap:
            def get_page_id(self, uri):
                return None

        class MockVikingFS:
            async def read_file(self, uri, ctx=None, **kwargs):
                return (
                    "Gina values [emotional support](../../events/2023/03/23/mutual_business_support.md) "
                    "with Jon.\n\n"
                    "<!-- MEMORY_FIELDS\n"
                    '{"memory_type": "experiences"}\n'
                    "-->"
                )

        tool_ctx = ToolContext(
            viking_fs=MockVikingFS(),
            request_ctx=RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER),
            default_search_uris=[],
            read_file_contents={},
            page_id_map=MockPageIdMap(),
        )

        result = await MemoryReadTool().execute(
            tool_ctx,
            uri="viking://user/default/memories/experiences/test.md",
        )

        assert result["content"] == "1\tGina values emotional support with Jon."

    @pytest.mark.asyncio
    async def test_read_tool_uses_memory_file_plain_content(self, monkeypatch):
        class MockPageIdMap:
            def get_page_id(self, uri):
                return None

        class MockVikingFS:
            async def read_file(self, uri, ctx=None, **kwargs):
                return (
                    "Gina values [emotional support](../../events/2023/03/23/mutual_business_support.md) "
                    "with Jon.\n\n"
                    "<!-- MEMORY_FIELDS\n"
                    '{"memory_type": "experiences"}\n'
                    "-->"
                )

        called = False
        original_plain_content = MemoryFile.plain_content

        def tracking_plain_content(self):
            nonlocal called
            called = True
            return original_plain_content(self)

        monkeypatch.setattr(MemoryFile, "plain_content", tracking_plain_content)

        tool_ctx = ToolContext(
            viking_fs=MockVikingFS(),
            request_ctx=RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER),
            default_search_uris=[],
            read_file_contents={},
            page_id_map=MockPageIdMap(),
        )

        result = await MemoryReadTool().execute(
            tool_ctx,
            uri="viking://user/default/memories/experiences/test.md",
        )

        assert called is True
        assert result["content"] == "1\tGina values emotional support with Jon."

    @pytest.mark.asyncio
    async def test_read_tool_uses_offset_and_limit_for_visible_content(self):
        class MockPageIdMap:
            def get_page_id(self, uri):
                return 42

        class MockVikingFS:
            async def read_file(self, uri, ctx=None, **kwargs):
                return (
                    'line1\nline2\nline3\n\n<!-- MEMORY_FIELDS\n{"memory_type": "experiences"}\n-->'
                )

        tool_ctx = ToolContext(
            viking_fs=MockVikingFS(),
            request_ctx=RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER),
            default_search_uris=[],
            read_file_contents={},
            page_id_map=MockPageIdMap(),
        )

        result = await MemoryReadTool().execute(
            tool_ctx,
            uri="viking://user/default/memories/experiences/test.md",
            offset=1,
            limit=1,
        )

        assert result["page_id"] == 42
        assert result["content"] == "2\tline2"

    @pytest.mark.asyncio
    async def test_read_tool_warns_when_offset_is_past_end_of_file(self):
        class MockPageIdMap:
            def get_page_id(self, uri):
                return None

        class MockVikingFS:
            async def read_file(self, uri, ctx=None, **kwargs):
                return 'line1\nline2\n\n<!-- MEMORY_FIELDS\n{"memory_type": "experiences"}\n-->'

        tool_ctx = ToolContext(
            viking_fs=MockVikingFS(),
            request_ctx=RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER),
            default_search_uris=[],
            read_file_contents={},
            page_id_map=MockPageIdMap(),
        )

        result = await MemoryReadTool().execute(
            tool_ctx,
            uri="viking://user/default/memories/experiences/test.md",
            offset=5,
        )

        assert result["content"] == (
            "<system-reminder>Warning: the file exists but is shorter than the provided "
            "offset (6). The file has 2 lines.</system-reminder>"
        )

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
                return MockSearchResult()

        request_ctx = RequestContext(
            user=UserIdentifier(
                account_id="test-account",
                user_id="test-user",
            ),
            role=Role.USER,
        )
        viking_fs = MockVikingFS()
        # Create tool_ctx with viking_fs included
        tool_ctx = ToolContext(
            viking_fs=viking_fs,
            request_ctx=request_ctx,
            default_search_uris=["viking://user/test-account/test-user/memories"],
            read_file_contents={},
        )

        result = await MemorySearchTool().execute(
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
        assert "read" in MEMORY_TOOLS_REGISTRY
        assert "search" in MEMORY_TOOLS_REGISTRY
        assert "ls" in MEMORY_TOOLS_REGISTRY

        # Check get_tool
        read_tool = get_tool("read")
        assert read_tool is not None
        assert isinstance(read_tool, MemoryReadTool)

        # Check get_tool_schemas
        schemas = get_tool_schemas()
        schema_names = [s["function"]["name"] for s in schemas]
        assert "read" in schema_names
        assert all(name in MEMORY_TOOLS_REGISTRY for name in schema_names)
