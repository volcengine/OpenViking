# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.graph_view import MemoryGraph
from openviking_cli.session.user_id import UserIdentifier


def _file_entry(uri: str, rel_path: str) -> dict:
    return {"uri": uri, "rel_path": rel_path, "isDir": False}


@pytest.mark.asyncio
async def test_collect_graph_data_preserves_markdown_links_in_content_full():
    content = """2023-08-22 (Tuesday) ChatLog:\n[Calvin]: I scored a deal with [Frank Ocean](../../../../entities/personal/calvin.md)!\n\n<!-- MEMORY_FIELDS\n{\"memory_type\": \"events\", \"links\": [{\"to_uri\": \"viking://user/Calvin/memories/entities/personal/calvin.md\", \"link_type\": \"related_to\", \"match_text\": \"Frank\"}]}\n-->"""

    mock_fs = MagicMock()
    mock_fs.tree = AsyncMock(
        return_value=[
            _file_entry(
                "viking://user/Calvin/memories/events/2023/08/22/collab_with_frank_ocean.md",
                "events/2023/08/22/collab_with_frank_ocean.md",
            )
        ]
    )
    mock_fs.read_file = AsyncMock(return_value=content)

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

    nodes, edges = await graph._collect_graph_data(["viking://user/Calvin/memories"], ctx)

    assert len(nodes) == 1
    assert (
        nodes[0]["content_full"]
        == "2023-08-22 (Tuesday) ChatLog:\n[Calvin]: I scored a deal with [Frank Ocean](../../../../entities/personal/calvin.md)!"
    )
    assert edges == []


@pytest.mark.asyncio
async def test_collect_graph_data_includes_root_level_profile_markdown():
    content = """# Caroline\n- likes painting\n\n<!-- MEMORY_FIELDS\n{\"memory_type\": \"profile\", \"links\": []}\n-->"""

    mock_fs = MagicMock()
    mock_fs.tree = AsyncMock(
        return_value=[_file_entry("viking://user/Caroline/memories/profile.md", "profile.md")]
    )
    mock_fs.read_file = AsyncMock(return_value=content)

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

    nodes, edges = await graph._collect_graph_data(["viking://user/Caroline/memories"], ctx)

    assert [node["uri"] for node in nodes] == ["viking://user/Caroline/memories/profile.md"]
    assert nodes[0]["memory_type"] == "profile"
    assert nodes[0]["content_preview"] == "# Caroline\n- likes painting"
    assert edges == []


@pytest.mark.asyncio
async def test_collect_graph_data_includes_content_preview():
    content = """Demo content line 1\nDemo content line 2\n\n<!-- MEMORY_FIELDS\n{\"memory_type\": \"experiences\", \"links\": []}\n-->"""

    mock_fs = MagicMock()
    mock_fs.tree = AsyncMock(
        return_value=[_file_entry("viking://agent/demo/memories/experiences/a.md", "experiences/a.md")]
    )
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
    mock_fs.tree = AsyncMock(
        return_value=[_file_entry("viking://user/Caroline/memories/profile.md", "profile.md")]
    )
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
    mock_fs.tree = AsyncMock(
        return_value=[_file_entry("viking://user/Caroline/memories/profile.md", "profile.md")]
    )
    mock_fs.read_file = AsyncMock(return_value=content)

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

    nodes, edges = await graph._collect_graph_data(["viking://user/Caroline/memories"], ctx)

    assert len(nodes) == 1
    assert nodes[0]["memory_type"] == "profile"
    assert "# Caroline" in nodes[0]["content_preview"]
    assert edges == []


@pytest.mark.asyncio
async def test_collect_graph_data_reads_all_nodes_before_filling_edge_targets():
    profile_uri = "viking://user/Caroline/memories/profile.md"
    child_uri = "viking://user/Caroline/memories/preferences/color.md"
    profile_content = """# Caroline\n- likes painting\n\n<!-- MEMORY_FIELDS\n{\"memory_type\": \"profile\", \"links\": []}\n-->"""
    child_content = f"""Blue\n\n<!-- MEMORY_FIELDS\n{{\"memory_type\": \"preferences\", \"links\": [{{\"to_uri\": \"{profile_uri}\", \"link_type\": \"belongs_to\"}}]}}\n-->"""

    mock_fs = MagicMock()
    mock_fs.tree = AsyncMock(
        return_value=[
            _file_entry(child_uri, "preferences/color.md"),
            _file_entry(profile_uri, "profile.md"),
        ]
    )
    mock_fs.read_file = AsyncMock(side_effect=[child_content, profile_content])

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

    nodes, edges = await graph._collect_graph_data(["viking://user/Caroline/memories"], ctx)

    profile_node = next(node for node in nodes if node["uri"] == profile_uri)
    assert profile_node["memory_type"] == "profile"
    assert profile_node["content_preview"] == "# Caroline\n- likes painting"
    assert profile_node["content_full"] == "# Caroline\n- likes painting"
    assert {
        "source": child_uri,
        "target": profile_uri,
        "link_type": "belongs_to",
        "weight": 1.0,
        "description": "",
    } in edges


@pytest.mark.asyncio
async def test_collect_graph_data_raises_when_reading_memory_file_fails():
    mock_fs = MagicMock()
    mock_fs.tree = AsyncMock(
        return_value=[_file_entry("viking://user/Caroline/memories/profile.md", "profile.md")]
    )
    mock_fs.read_file = AsyncMock(side_effect=RuntimeError("boom"))

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

    with pytest.raises(RuntimeError, match="boom"):
        await graph._collect_graph_data(["viking://user/Caroline/memories"], ctx)


@pytest.mark.asyncio
async def test_collect_graph_data_drops_edges_to_unloaded_external_nodes():
    external_profile_uri = "viking://user/Caroline/memories/profile.md"
    child_uri = "viking://user/Melanie/memories/preferences/color.md"
    child_content = f"""Blue\n\n<!-- MEMORY_FIELDS\n{{\"memory_type\": \"preferences\", \"links\": [{{\"to_uri\": \"{external_profile_uri}\", \"link_type\": \"belongs_to\"}}]}}\n-->"""

    mock_fs = MagicMock()
    mock_fs.tree = AsyncMock(
        return_value=[_file_entry(child_uri, "preferences/color.md")]
    )
    mock_fs.read_file = AsyncMock(return_value=child_content)

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

    nodes, edges = await graph._collect_graph_data(["viking://user/Melanie/memories"], ctx)

    assert [node["uri"] for node in nodes] == [child_uri]
    assert edges == []
