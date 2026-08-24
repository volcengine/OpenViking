# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
import py_compile
import subprocess
import warnings
from pathlib import Path

from openviking.session.memory.graph_view import _render_graph_html


def _render_generated_markdown(markdown: str, base_uri: str = "") -> str:
    """Execute the generated graph renderer, not a Python reimplementation."""
    html = _render_graph_html([], [])
    helper_start = html.index("function resolveRelativeUri")
    helper_end = html.index("function showTooltip", helper_start)
    renderer_source = html[helper_start:helper_end]
    harness = """
const fs = require('node:fs');
const vm = require('node:vm');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = {};
vm.createContext(context);
vm.runInContext(payload.rendererSource, context);
process.stdout.write(JSON.stringify(context.renderMarkdown(payload.markdown, payload.baseUri)));
"""
    result = subprocess.run(
        ["node", "-e", harness],
        input=json.dumps(
            {
                "rendererSource": renderer_source,
                "markdown": markdown,
                "baseUri": base_uri,
            }
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_graph_view_module_compiles_without_escape_warnings():
    path = (
        Path(__file__).resolve().parents[3] / "openviking" / "session" / "memory" / "graph_view.py"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        py_compile.compile(str(path), doraise=True)


def test_render_graph_html_supports_relative_markdown_links():
    nodes = [
        {
            "id": "viking://user/Caroline/memories/profile.md",
            "uri": "viking://user/Caroline/memories/profile.md",
            "label": "profile",
            "memory_type": "profile",
            "category": "",
            "content_preview": "[music](./preferences/music.md)",
            "content_full": "[music](./preferences/music.md)\n[up](../events/2023/05/08/test.md)",
            "content_truncated": False,
        }
    ]

    html = _render_graph_html(nodes, [])

    assert "function resolveRelativeUri(baseUri, relativeUri)" in html
    assert "function resolveMarkdownLink(rawHref, baseUri = '')" in html
    assert "const link = resolveMarkdownLink(href, baseUri);" in html
    assert 'data-target-uri="${escapeHtml(link.targetUri)}"' in html


def test_generated_graph_markdown_renderer_makes_dangerous_schemes_inert():
    for href in (
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        " java%73cript:alert(1)",
        "data:text/html,alert(1)",
        "blob:https://example.test/identifier",
    ):
        rendered = _render_generated_markdown(f"[hostile]({href})")

        assert "<a " not in rendered
        assert '<span class="unsafe-link">hostile</span>' in rendered


def test_generated_graph_markdown_renderer_preserves_safe_links_and_escapes_attributes():
    base_uri = "viking://user/Caroline/memories/profile.md"

    relative = _render_generated_markdown("[music](./preferences/music.md)", base_uri)
    viking = _render_generated_markdown(
        "[music](viking://user/Caroline/memories/preferences/music.md)", base_uri
    )
    https = _render_generated_markdown("[web](https://example.test/path?x=1)", base_uri)
    mailto = _render_generated_markdown("[mail](mailto:user@example.test)", base_uri)
    tel = _render_generated_markdown("[call](tel:+491234567)", base_uri)
    quoted = _render_generated_markdown(
        '[quoted](https://example.test/?title="safe")', base_uri
    )

    assert (
        '<a href="viking://user/Caroline/memories/preferences/music.md" '
        'data-target-uri="viking://user/Caroline/memories/preferences/music.md">music</a>'
        in relative
    )
    assert 'data-target-uri="viking://user/Caroline/memories/preferences/music.md"' in viking
    assert '<a href="https://example.test/path?x=1">web</a>' in https
    assert '<a href="mailto:user@example.test">mail</a>' in mailto
    assert '<a href="tel:+491234567">call</a>' in tel
    assert 'href="https://example.test/?title=&amp;quot;safe&amp;quot;"' in quoted
    assert 'href="https://example.test/?title="safe"' not in quoted


def test_render_graph_html_renders_tooltip_content_as_markdown():
    html = _render_graph_html([], [])

    assert "tooltip.querySelector('.desc').innerHTML = renderMarkdown(desc || '', baseUri);" in html
    assert "tooltip.querySelector('.desc').textContent = desc || '';" not in html


def test_render_graph_html_embeds_full_markdown_content_and_link_targets():
    nodes = [
        {
            "id": "viking://user/Caroline/memories/profile.md",
            "uri": "viking://user/Caroline/memories/profile.md",
            "label": "profile",
            "memory_type": "profile",
            "category": "",
            "content_preview": "# Caroline",
            "content_full": "# Caroline\n\nSee [music](viking://user/Caroline/memories/preferences/music.md)",
            "content_truncated": False,
        }
    ]
    edges = [
        {
            "source": "viking://user/Caroline/memories/profile.md",
            "target": "viking://user/Caroline/memories/preferences/music.md",
            "link_type": "related_to",
            "weight": 1.0,
            "description": "music preference",
        }
    ]

    html = _render_graph_html(nodes, edges)

    assert (
        '"content_full": "# Caroline\\n\\nSee [music](viking://user/Caroline/memories/preferences/music.md)"'
        in html
    )
    assert "function renderMarkdown" in html
    assert (
        "detailContent.innerHTML = renderMarkdown(node.content_full || node.content_preview || '', node.uri || '');"
        in html
    )
    assert "detailContent.addEventListener('click'" in html
    assert "const targetNodeId = link.dataset.targetUri;" in html
    assert (
        "network.focus(targetNodeId, { animation: { duration: 350, easingFunction: 'easeInOutQuad' }, scale: network.getScale() });"
        in html
    )


def test_render_graph_html_clicking_detail_link_centers_target_node():
    html = _render_graph_html([], [])

    assert (
        "network.focus(targetNodeId, { animation: { duration: 350, easingFunction: 'easeInOutQuad' }, scale: network.getScale() });"
        in html
    )


def test_render_graph_html_uses_dark_node_background_with_light_text():
    nodes = [
        {
            "id": "viking://user/demo/memories/experiences/a.md",
            "uri": "viking://user/demo/memories/experiences/a.md",
            "label": "a",
            "memory_type": "experiences",
            "category": "",
            "content_preview": "hello world",
            "content_truncated": False,
        }
    ]

    html = _render_graph_html(nodes, [])

    assert '"background": "#0f172a"' in html
    assert '"hover": {"background": "#0f172a", "border": "#fd79a8"}' in html
    assert '"font": {"color": "#f8fafc", "size": 12}' in html
    assert '"border": "#fd79a8"' in html


def test_render_graph_html_embeds_vis_network_viewer_metadata():
    nodes = [
        {
            "id": "viking://user/demo/memories/experiences/a.md",
            "uri": "viking://user/demo/memories/experiences/a.md",
            "label": "a",
            "memory_type": "experiences",
            "category": "",
            "content_preview": "hello world",
            "content_truncated": False,
        }
    ]
    edges = [
        {
            "source": "viking://user/demo/memories/experiences/a.md",
            "target": "viking://user/demo/memories/experiences/b.md",
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


def test_render_graph_html_embeds_vis_network_load_guard():
    html = _render_graph_html([], [])

    assert 'window.__OPENVIKING_VIS_NETWORK_SOURCE__ = "external";' in html
    assert (
        '<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>'
        in html
    )
    assert "if (!window.vis || !window.vis.DataSet || !window.vis.Network)" in html
    assert "Graph library failed to load. If you opened this file locally" in html


def test_render_graph_html_keeps_newline_escape_in_split_call():
    html = _render_graph_html([], [])

    split_snippet = "const lines = html.split('\\\\n');"
    assert split_snippet in html
    assert "const lines = html.split('\n');" not in html


def test_render_graph_html_stops_physics_after_stabilization():
    html = _render_graph_html([], [])

    assert "network.once('stabilized'" in html
    assert "network.fit({ animation: false, padding: 80 });" in html
    assert "network.setOptions({ physics: false })" in html


def test_render_graph_html_uses_tighter_layout_configuration():
    html = _render_graph_html([], [])

    assert "gravitationalConstant: -7000" in html
    assert "springLength: 120" in html
    assert "avoidOverlap: 0.2" in html
    assert "stabilization: { iterations: 250" in html


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


def test_render_graph_html_inverts_selected_node_colors():
    nodes = [
        {
            "id": "viking://user/demo/memories/experiences/a.md",
            "uri": "viking://user/demo/memories/experiences/a.md",
            "label": "a",
            "memory_type": "experiences",
            "category": "",
            "content_preview": "hello world",
            "content_truncated": False,
        }
    ]

    html = _render_graph_html(nodes, [])

    assert '"highlight": {"background": "#f8fafc", "border": "#fd79a8"}' in html
    assert '"font": {"color": "#f8fafc", "size": 12}' in html
    assert "node(values, id, selected) {" in html
    assert "if (!selected) {" in html
    assert "return;" in html
    assert "values.color = { background: '#f8fafc', border: values.color.border };" in html
    assert "label(values, id, selected) {" in html
    assert "if (!selected) {" in html
    assert "values.color = '#0f172a';" in html


def test_render_graph_html_edge_details_include_source_and_target_uris():
    html = _render_graph_html([], [])

    assert "detailMeta.textContent = `${edge.link_type} · weight=${edge.weight}`;" in html
    assert "const sourceUri = edge.from || edge.source || '';" in html
    assert "const targetUri = edge.to || edge.target || '';" in html
    assert (
        "detailContent.innerHTML = renderMarkdown(`- from_uri: ${escapeHtml(sourceUri)}\\n- "
        "to_uri: ${escapeHtml(targetUri)}\\n\\n${escapeHtml(edge.description || '(no description)')}`);"
        in html
    )


def test_render_graph_html_renders_dynamic_relationship_legend_from_edges():
    edges = [
        {
            "source": "viking://user/Caroline/memories/profile.md",
            "target": "viking://user/Caroline/memories/preferences/music.md",
            "link_type": "inspired_by",
            "weight": 1.0,
            "description": "same hash color",
        },
        {
            "source": "viking://user/Caroline/memories/preferences/music.md",
            "target": "viking://user/Caroline/memories/events/show.md",
            "link_type": "works_with",
            "weight": 1.0,
            "description": "different hash color",
        },
        {
            "source": "viking://user/Caroline/memories/events/show.md",
            "target": "viking://user/Caroline/memories/entities/band.md",
            "link_type": "inspired_by",
            "weight": 0.5,
            "description": "same hash color again",
        },
    ]

    html = _render_graph_html([], edges)

    assert "const activeLinkTypes = new Set();" in html
    assert (
        "const linkTypes = [...new Set(originalEdges.map((edge) => edge.link_type).filter(Boolean))].sort();"
        in html
    )
    assert "activeLinkTypes.has(linkType)" in html
    assert "activeLinkTypes.add(linkType)" in html
    assert "activeLinkTypes.delete(linkType)" in html
    assert "inspired_by" in html
    assert "works_with" in html


def test_render_graph_html_keeps_node_label_plain_text_while_rendering_body_links():
    nodes = [
        {
            "id": "viking://user/Caroline/memories/profile.md",
            "uri": "viking://user/Caroline/memories/profile.md",
            "label": "Caroline profile",
            "memory_type": "profile",
            "category": "",
            "content_preview": "她喜欢[角色扮演游戏](entities/games/rpg.md)，也喜欢开放世界游戏。",
            "content_full": "她喜欢[角色扮演游戏](entities/games/rpg.md)，也喜欢开放世界游戏。",
            "content_truncated": False,
        }
    ]

    html = _render_graph_html(nodes, [])

    assert '"label": "Caroline profile"' in html
    assert (
        '"content_full": "她喜欢[角色扮演游戏](entities/games/rpg.md)，也喜欢开放世界游戏。"'
        in html
    )
    assert (
        '"content_preview": "她喜欢[角色扮演游戏](entities/games/rpg.md)，也喜欢开放世界游戏。"'
        in html
    )
