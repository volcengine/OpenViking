# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session.memory.graph_view import _render_graph_html


def test_render_graph_html_embeds_cytoscape_viewer_metadata():
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

    assert "cytoscape" in html.lower()
    assert "content_preview" in html
    assert "content_truncated" in html
    assert "same topic" in html
    assert "related_to" in html
    assert "experiences" in html
    assert "memory_type" in html


def test_render_graph_html_escapes_newline_in_preview_truncated_marker():
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

    assert "detailContent.textContent += '\\n\\n[preview truncated]'" in html
    assert "if (node.data('content_truncated')) desc += '\\n\\n[preview truncated]'" in html
    assert "detailContent.textContent += '\n\n[preview truncated]'" not in html
    assert "if (node.data('content_truncated')) desc += '\n\n[preview truncated]'" not in html
