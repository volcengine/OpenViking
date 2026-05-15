# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.graph_view import MemoryGraph
from openviking.session.memory.utils import parse_memory_file_with_fields
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
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


@pytest.mark.asyncio
async def test_collect_graph_data_keeps_profile_body_as_content_preview():
    content = """# Caroline\n- likes painting\n\n<!-- MEMORY_FIELDS\n{\"memory_type\": \"profile\", \"links\": []}\n-->"""

    mock_fs = MagicMock()
    mock_fs.glob = AsyncMock(return_value={"matches": ["viking://user/Caroline/memories/profile.md"]})
    mock_fs.read_file = AsyncMock(return_value=content)

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

    nodes, edges = await graph._collect_graph_data(["viking://user/Caroline/memories"], ctx)

    assert len(nodes) == 1
    assert nodes[0]["memory_type"] == "profile"
    assert nodes[0]["content_preview"] == "# Caroline\n- likes painting"
    assert nodes[0]["content_truncated"] is False
    assert edges == []


@pytest.mark.asyncio
async def test_collect_graph_data_infers_memory_type_from_parent_directory():
    content = """# Caroline\n- likes painting\n\n<!-- MEMORY_FIELDS\n{\"links\": []}\n-->"""

    mock_fs = MagicMock()
    mock_fs.glob = AsyncMock(return_value={"matches": ["viking://user/Caroline/memories/profile.md"]})
    mock_fs.read_file = AsyncMock(return_value=content)

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

    nodes, edges = await graph._collect_graph_data(["viking://user/Caroline/memories"], ctx)

    assert len(nodes) == 1
    assert nodes[0]["memory_type"] == "profile"
    assert "# Caroline" in nodes[0]["content_preview"]
    assert edges == []


def test_memory_file_utils_write_preserves_memory_type_in_comment():
    memory_file = MemoryFile(
        uri="viking://user/default/memories/preferences/code_style.md",
        memory_type="preferences",
        content="Prefers concise responses.",
        extra_fields={"topic": "code_style"},
    )

    written = MemoryFileUtils.write(memory_file)
    parsed = parse_memory_file_with_fields(written)

    assert parsed["memory_type"] == "preferences"
    assert parsed["topic"] == "code_style"
    assert parsed["content"] == "Prefers concise responses."
