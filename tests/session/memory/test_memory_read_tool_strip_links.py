# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role, ToolContext
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.tools import MemoryReadTool
from openviking_cli.session.user_id import UserIdentifier


class MockPageIdMap:
    def get_page_id(self, uri):
        return None


class MockVikingFS:
    async def read_file(self, uri, ctx=None, **kwargs):
        return (
            "Gina values [emotional support](../../events/2023/03/23/mutual_business_support.md) "
            "with Jon.\n\n"
            "<!-- MEMORY_FIELDS\n"
            "{\"memory_type\": \"experiences\"}\n"
            "-->"
        )


@pytest.mark.asyncio
async def test_read_tool_strips_local_memory_links_from_llm_content():
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

    assert result["content"] == "1 | Gina values emotional support with Jon."


@pytest.mark.asyncio
async def test_read_tool_uses_memory_file_plain_content(monkeypatch):
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
    assert result["content"] == "1 | Gina values emotional support with Jon."
