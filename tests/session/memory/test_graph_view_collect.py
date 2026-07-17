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
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

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
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

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
        return_value=[
            _file_entry("viking://user/demo/memories/experiences/a.md", "experiences/a.md")
        ]
    )
    mock_fs.read_file = AsyncMock(return_value=content)

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

    nodes, edges = await graph._collect_graph_data(["viking://user/demo/memories"], ctx)

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
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

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
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

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
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

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
async def test_collect_graph_data_canonicalizes_current_user_shorthand():
    shorthand_uri = "viking://user/memories/entities/project.md"
    canonical_uri = "viking://user/alice/memories/entities/project.md"
    target_uri = "viking://user/alice/memories/entities/company.md"
    source_content = f"""# Project

<!-- MEMORY_FIELDS
{{"memory_type": "entities", "links": [{{"to_uri": "{target_uri}"}}]}}
-->"""
    target_content = """# Company

<!-- MEMORY_FIELDS
{"memory_type": "entities", "links": []}
-->"""
    mock_fs = MagicMock()
    mock_fs.tree = AsyncMock(
        return_value=[
            _file_entry(shorthand_uri, "project.md"),
            _file_entry(target_uri, "company.md"),
        ]
    )
    mock_fs.read_file = AsyncMock(side_effect=[source_content, target_content])

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    nodes, edges = await graph._collect_graph_data(["viking://user/memories/entities"], ctx)

    assert {node["uri"] for node in nodes} == {canonical_uri, target_uri}
    assert edges == [
        {
            "source": canonical_uri,
            "target": target_uri,
            "link_type": "related_to",
            "weight": 1.0,
            "description": "",
        }
    ]


@pytest.mark.asyncio
async def test_collect_graph_data_raises_when_reading_memory_file_fails():
    mock_fs = MagicMock()
    mock_fs.tree = AsyncMock(
        return_value=[_file_entry("viking://user/Caroline/memories/profile.md", "profile.md")]
    )
    mock_fs.read_file = AsyncMock(side_effect=RuntimeError("boom"))

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

    with pytest.raises(RuntimeError, match="boom"):
        await graph._collect_graph_data(["viking://user/Caroline/memories"], ctx)


@pytest.mark.asyncio
async def test_collect_graph_data_drops_edges_to_unloaded_external_nodes():
    external_profile_uri = "viking://user/Caroline/memories/profile.md"
    child_uri = "viking://user/Melanie/memories/preferences/color.md"
    child_content = f"""Blue\n\n<!-- MEMORY_FIELDS\n{{\"memory_type\": \"preferences\", \"links\": [{{\"to_uri\": \"{external_profile_uri}\", \"link_type\": \"belongs_to\"}}]}}\n-->"""

    mock_fs = MagicMock()
    mock_fs.tree = AsyncMock(return_value=[_file_entry(child_uri, "preferences/color.md")])
    mock_fs.read_file = AsyncMock(return_value=child_content)

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

    nodes, edges = await graph._collect_graph_data(["viking://user/Melanie/memories"], ctx)

    assert [node["uri"] for node in nodes] == [child_uri]
    assert edges == []


@pytest.mark.asyncio
async def test_collect_graph_data_includes_linked_resource_overview(monkeypatch):
    resource_root = "viking://resources/docs"
    overview_uri = f"{resource_root}/.overview.md"
    memory_root = "viking://user/alice/memories"
    entity_uri = f"{memory_root}/entities/projects/openviking.md"
    overview_content = """# OpenViking

This overview links to [OpenViking](../../user/alice/memories/entities/projects/openviking.md).

External [documentation](https://example.com) is not a graph edge."""
    entity_content = """# OpenViking

<!-- MEMORY_FIELDS
{"memory_type": "entities"}
-->"""
    mock_fs = MagicMock()
    mock_fs.tree = AsyncMock(
        side_effect=[
            [_file_entry(overview_uri, ".overview.md")],
            [_file_entry(entity_uri, "entities/projects/openviking.md")],
        ]
    )
    mock_fs.read_file = AsyncMock(side_effect=[overview_content, entity_content])
    monkeypatch.setattr(
        "openviking.session.memory.graph_view.wiki_links_enabled",
        lambda: True,
    )

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    nodes, edges = await graph._collect_graph_data([resource_root, memory_root], ctx)

    resource_node = next(node for node in nodes if node["uri"] == overview_uri)
    assert resource_node["memory_type"] == "resource"
    assert resource_node["label"] == "docs-Summary"
    assert "<!-- MEMORY_FIELDS" not in resource_node["content_full"]
    assert mock_fs.tree.await_args_list[0].kwargs["show_all_hidden"] is True
    assert mock_fs.tree.await_args_list[1].kwargs["show_all_hidden"] is False
    assert edges == [
        {
            "source": overview_uri,
            "target": entity_uri,
            "link_type": "related_to",
            "weight": 1.0,
            "description": "",
        }
    ]


@pytest.mark.asyncio
async def test_collect_graph_data_excludes_resource_root_summary(monkeypatch):
    resource_root = "viking://resources"
    root_overview_uri = f"{resource_root}/.overview.md"
    child_overview_uri = f"{resource_root}/docs/.overview.md"
    entity_uri = "viking://user/alice/memories/entities/projects/openviking.md"
    overview_content = "[OpenViking](../../user/alice/memories/entities/projects/openviking.md)"
    entity_content = """# OpenViking

<!-- MEMORY_FIELDS
{"memory_type": "entities"}
-->"""
    mock_fs = MagicMock()
    mock_fs.tree = AsyncMock(
        side_effect=[
            [
                _file_entry(root_overview_uri, ".overview.md"),
                _file_entry(child_overview_uri, "docs/.overview.md"),
            ],
            [_file_entry(entity_uri, "entities/projects/openviking.md")],
        ]
    )
    mock_fs.read_file = AsyncMock(side_effect=[overview_content, entity_content])
    monkeypatch.setattr(
        "openviking.session.memory.graph_view.wiki_links_enabled",
        lambda: True,
    )

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    nodes, edges = await graph._collect_graph_data(
        [resource_root, "viking://user/alice/memories"], ctx
    )

    assert {node["uri"] for node in nodes} == {child_overview_uri, entity_uri}
    assert all(node["label"] != "resources-Summary" for node in nodes)
    assert edges[0]["source"] == child_overview_uri


@pytest.mark.asyncio
async def test_resource_overview_ignores_memory_fields_links(monkeypatch):
    resource_root = "viking://resources/docs"
    overview_uri = f"{resource_root}/.overview.md"
    memory_root = "viking://user/alice/memories"
    entity_uri = f"{memory_root}/entities/projects/openviking.md"
    overview_content = f"""# OpenViking

No visible entity link.

<!-- MEMORY_FIELDS
{{"links": [{{"from_uri": "{overview_uri}", "to_uri": "{entity_uri}"}}]}}
-->"""
    entity_content = """# OpenViking

<!-- MEMORY_FIELDS
{"memory_type": "entities"}
-->"""
    mock_fs = MagicMock()
    mock_fs.tree = AsyncMock(
        side_effect=[
            [_file_entry(overview_uri, ".overview.md")],
            [_file_entry(entity_uri, "entities/projects/openviking.md")],
        ]
    )
    mock_fs.read_file = AsyncMock(side_effect=[overview_content, entity_content])
    monkeypatch.setattr(
        "openviking.session.memory.graph_view.wiki_links_enabled",
        lambda: True,
    )

    graph = MemoryGraph(viking_fs=mock_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    nodes, edges = await graph._collect_graph_data([resource_root, memory_root], ctx)

    assert [node["uri"] for node in nodes] == [entity_uri]
    assert edges == []
