# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.graph_view import MemoryGraph
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_collect_graph_data_includes_content_preview():
    content = """Demo content line 1\nDemo content line 2\n\n<!-- MEMORY_FIELDS\n{\"memory_type\": \"experiences\", \"links\": []}\n-->"""

    mock_fs = MagicMock()
    mock_fs.glob = AsyncMock(return_value={"matches": ["viking://agent/demo/memories/experiences/a.md"]})
    mock_fs.read_file = AsyncMock(return_value=content)

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

    nodes, edges = await graph._collect_graph_data(["viking://agent/demo/memories"], ctx)

    assert len(nodes) == 1
    assert nodes[0]["memory_type"] == "experiences"
    assert "Demo content line 1" in nodes[0]["content_preview"]
    assert nodes[0]["content_truncated"] is False
    assert edges == []
