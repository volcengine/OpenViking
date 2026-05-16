# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session.memory.graph_view import _render_graph_html


def test_render_graph_html_filter_does_not_auto_fit_graph():
    html = _render_graph_html([], [])

    assert "function applyFilter(shouldFit = false)" in html
    assert "network.fit({ animation: false });" not in html


def test_render_graph_html_uses_transparent_node_fill_with_typed_borders():
    nodes = [
        {
            "id": "viking://agent/demo/memories/experiences/a.md",
            "uri": "viking://agent/demo/memories/experiences/a.md",
            "label": "a",
            "memory_type": "experiences",
            "category": "",
            "content_preview": "hello world",
            "content_truncated": False,
        }
    ]

    html = _render_graph_html(nodes, [])

    assert '"background": "rgba(15, 23, 42, 0)"' in html
    assert '"border": "#fd79a8"' in html


def test_render_graph_html_embeds_vis_network_viewer_metadata():
    nodes = [
        {
            "id": "viking://agent/demo/memories/experiences/a.md",
            "uri": "viking://agent/demo/memories/experiences/a.md",
            "label": "a",
            "memory_type": "experiences",
            "category": "",
            "content_preview": "hello world",
            "content_truncated": False,
        }
    ]
    edges = [
        {
            "source": "viking://agent/demo/memories/experiences/a.md",
            "target": "viking://agent/demo/memories/experiences/b.md",
            "link_type": "related_to",
            "weight": 0.8,
            "description": "same topic",
        }
    ]

    html = _render_graph_html(nodes, edges)

    assert "vis-network" in html.lower()
    assert "new vis.Network" in html
    assert "shape: 'box'" in html or 'shape: "box"' in html
    assert "content_preview" in html
    assert "content_truncated" in html
    assert "same topic" in html
    assert "related_to" in html
    assert "experiences" in html
    assert "memory_type" in html
    assert "cytoscape" not in html.lower()


def test_render_graph_html_omits_header_copy():
    html = _render_graph_html([], [])

    assert "<h3>Memory Graph</h3>" not in html
    assert "Hover to preview. Click a node to focus its neighbors." not in html


def test_render_graph_html_stops_physics_after_stabilization():
    html = _render_graph_html([], [])

    assert "network.once('stabilized'" in html
    assert "network.fit({ animation: false, padding: 80 });" in html
    assert "network.setOptions({ physics: false })" in html


def test_render_graph_html_uses_tighter_layout_configuration():
    html = _render_graph_html([], [])

    assert "gravitationalConstant: -7000" in html
    assert "springLength: 170" in html
    assert "avoidOverlap: 0.2" in html
    assert "stabilization: { iterations: 500" in html


def test_render_graph_html_supports_multi_select_memory_type_filter():
    html = _render_graph_html([], [])

    assert "const activeMemoryTypes = new Set();" in html
    assert "activeMemoryTypes.has(memoryType)" in html
    assert "activeMemoryTypes.size === 0" in html
    assert "activeMemoryTypes.add(memoryType)" in html
    assert "activeMemoryTypes.delete(memoryType)" in html


def test_render_graph_html_restores_visibility_without_rebuilding_dataset():
    html = _render_graph_html([], [])

    assert "nodes.clear()" not in html
    assert "edges.clear()" not in html
    assert "restoreVisibleGraph()" in html
    assert "hidden: false" in html


def test_render_graph_html_click_node_highlights_without_hiding_others():
    html = _render_graph_html([], [])

    assert "network.selectNodes([focusNodeId, ...connectedNodeIds]);" in html
    assert "const updatedNodes = nodes.get().map" not in html
    assert "const updatedEdges = edges.get().map" not in html


def test_render_graph_html_shows_node_content_preview_in_details():
    nodes = [
        {
            "id": "viking://user/Caroline/memories/profile.md",
            "uri": "viking://user/Caroline/memories/profile.md",
            "label": "profile",
            "memory_type": "profile",
            "category": "",
            "content_preview": "# Caroline\n- likes painting",
            "content_truncated": False,
        }
    ]

    html = _render_graph_html(nodes, [])

    assert "detailContent.textContent = escapedPreviewText(node.content_preview, node.content_truncated);" in html
    assert "return text || '(empty)';" in html


def test_render_graph_html_does_not_show_preview_truncated_marker():
    nodes = [
        {
            "id": "viking://agent/demo/memories/experiences/a.md",
            "uri": "viking://agent/demo/memories/experiences/a.md",
            "label": "a",
            "memory_type": "experiences",
            "category": "",
            "content_preview": "hello\nworld",
            "content_truncated": True,
        }
    ]

    html = _render_graph_html(nodes, [])

    assert "[preview truncated]" not in html
    assert "content_truncated" in html
