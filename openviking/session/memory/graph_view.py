# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory graph generator — builds a self-contained D3.js force-directed HTML graph
from all links stored in MEMORY_FIELDS across one or more memory spaces.

Usage:
    graph = MemoryGraph(viking_fs)
    path = await graph.gen_graph("viking://user/{space}/memories", ctx=ctx)
    path = await graph.build_graph(["viking://user/a/memories", "viking://user/b/memories"], "viking://user/default/memories/.graph.html", ctx=ctx)
"""

import json
from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# Memory type → color mapping
TYPE_COLORS = {
    "profile": "#e74c3c",
    "preferences": "#3498db",
    "entities": "#2ecc71",
    "events": "#f39c12",
    "skills": "#9b59b6",
    "identity": "#1abc9c",
    "tools": "#e67e22",
    "experiences": "#fd79a8",
    "trajectories": "#6c5ce7",
}

# Link type → style mapping
LINK_STYLES = {
    "related_to": {"color": "#999", "dash": "none"},
    "belongs_to": {"color": "#3498db", "dash": "none"},
    "caused_by": {"color": "#e74c3c", "dash": "6,3"},
    "derived_from": {"color": "#9b59b6", "dash": "none"},
    "contradicts": {"color": "#e74c3c", "dash": "2,2"},
    "evolved_from": {"color": "#2ecc71", "dash": "none"},
}


class MemoryGraph:
    """Generate an Obsidian-style force-directed graph of memory links."""

    def __init__(self, viking_fs=None):
        self._viking_fs = viking_fs

    def _get_viking_fs(self):
        if self._viking_fs is None:
            self._viking_fs = get_viking_fs()
        return self._viking_fs

    @staticmethod
    def _build_content_preview(content: str, limit: int = 600) -> str:
        text = (content or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "…"

    @staticmethod
    def _is_content_truncated(content: str, limit: int = 600) -> bool:
        return len((content or "").strip()) > limit

    async def _collect_graph_data(
        self,
        space_uris: List[str],
        ctx: RequestContext,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        viking_fs = self._get_viking_fs()
        if not viking_fs:
            raise ValueError("VikingFS not available")

        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []

        for space_uri in space_uris:
            glob_result = await viking_fs.glob(
                "**/*.md",
                uri=space_uri,
                node_limit=None,
                ctx=ctx,
            )
            all_uris = glob_result.get("matches", [])
            md_uris = [
                u
                for u in all_uris
                if not u.endswith("/.overview.md") and not u.endswith("/.abstract.md")
            ]

            logger.info(f"[build_graph] Found {len(md_uris)} memory files under {space_uri}")

            for uri in md_uris:
                try:
                    content = await viking_fs.read_file(uri, ctx=ctx)
                    if not content:
                        continue
                    mf = MemoryFileUtils.read(content, uri=uri)
                except Exception as e:
                    logger.warning(f"Failed to read/parse {uri}: {e}")
                    continue

                memory_type = mf.memory_type or ""
                category = mf.extra_fields.get("category", "")
                name = mf.extra_fields.get("name", "")
                label = name if name else uri.split("/")[-1].replace(".md", "")

                nodes[uri] = {
                    "id": uri,
                    "uri": uri,
                    "label": label,
                    "memory_type": memory_type,
                    "category": category,
                    "content_preview": self._build_content_preview(mf.content),
                    "content_truncated": self._is_content_truncated(mf.content),
                }

                for link_data in mf.links:
                    if not isinstance(link_data, dict):
                        continue
                    to_uri = link_data.get("to_uri", "")
                    if not to_uri:
                        continue
                    edges.append(
                        {
                            "source": link_data.get("from_uri", uri),
                            "target": to_uri,
                            "link_type": link_data.get("link_type", "related_to"),
                            "weight": float(link_data.get("weight", 1.0)),
                            "description": link_data.get("description", ""),
                        }
                    )

        for edge in edges:
            for key in ("source", "target"):
                uri = edge[key]
                if uri not in nodes:
                    nodes[uri] = {
                        "id": uri,
                        "uri": uri,
                        "label": uri.split("/")[-1].replace(".md", ""),
                        "memory_type": "",
                        "category": "",
                    }

        seen = set()
        unique_edges = []
        for e in edges:
            if e["source"] not in nodes or e["target"] not in nodes:
                continue
            key = (e["source"], e["target"], e["link_type"])
            if key not in seen:
                seen.add(key)
                unique_edges.append(e)

        logger.info(f"[build_graph] Built graph: {len(nodes)} nodes, {len(unique_edges)} edges")
        return list(nodes.values()), unique_edges

    async def gen_graph(
        self,
        space_uri: str,
        ctx: RequestContext,
    ) -> str:
        """Scan a memory space, extract links, build graph HTML, write to that space."""
        graph_path = f"{space_uri.rstrip('/')}/.graph.html"
        return await self.build_graph([space_uri], graph_path, ctx)

    async def build_graph(
        self,
        space_uris: List[str],
        output_uri: str,
        ctx: RequestContext,
    ) -> str:
        """Scan multiple memory roots, extract links, build graph HTML, and write to output URI."""
        if not space_uris:
            raise ValueError("space_uris must not be empty")
        if not output_uri:
            raise ValueError("output_uri must not be empty")

        viking_fs = self._get_viking_fs()
        if not viking_fs:
            raise ValueError("VikingFS not available")

        nodes, edges = await self._collect_graph_data(space_uris, ctx)
        html = _render_graph_html(nodes, edges)
        try:
            await viking_fs.write_file(output_uri, html, ctx=ctx)
            tracer.info(f"[build_graph] Generated graph: {output_uri}")
        except Exception as e:
            logger.error(f"Failed to write graph {output_uri}: {e}")
            raise

        return output_uri


# ---------------------------------------------------------------------------
# HTML template — self-contained D3.js force graph (Obsidian-style)
# ---------------------------------------------------------------------------


def _render_graph_html(nodes: List[Dict], edges: List[Dict]) -> str:
    elements = [
        {"data": node}
        for node in nodes
    ] + [
        {
            "data": {
                "id": f"edge-{idx}",
                **edge,
            }
        }
        for idx, edge in enumerate(edges)
    ]
    elements_json = json.dumps(elements, ensure_ascii=False)
    type_colors_json = json.dumps(TYPE_COLORS)
    link_styles_json = json.dumps(LINK_STYLES)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Memory Graph</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #0f172a; color: #e2e8f0; }}
  #app {{ display: grid; grid-template-columns: 1fr 320px; height: 100vh; }}
  #cy {{ width: 100%; height: 100vh; background: radial-gradient(circle at top, #1e293b 0%, #0f172a 60%); }}
  #sidebar {{ border-left: 1px solid #334155; background: rgba(15, 23, 42, 0.96); padding: 16px; overflow: auto; }}
  #sidebar h3 {{ margin: 0 0 12px; font-size: 16px; }}
  #sidebar .muted {{ color: #94a3b8; font-size: 12px; }}
  #sidebar .block {{ margin-top: 14px; padding-top: 14px; border-top: 1px solid #1e293b; }}
  #sidebar pre {{ white-space: pre-wrap; word-break: break-word; font-size: 12px; line-height: 1.5; color: #cbd5e1; }}
  #legend {{ position: fixed; left: 16px; top: 16px; z-index: 10; background: rgba(15, 23, 42, 0.92); border: 1px solid #334155; border-radius: 12px; padding: 12px 14px; max-width: 280px; box-shadow: 0 10px 30px rgba(0,0,0,0.25); }}
  #legend h4 {{ margin: 0 0 8px; font-size: 13px; }}
  .legend-item {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; font-size: 12px; color: #cbd5e1; }}
  .legend-chip {{ width: 12px; height: 12px; border-radius: 4px; flex: 0 0 12px; }}
  .legend-line {{ width: 22px; height: 3px; border-radius: 999px; flex: 0 0 22px; }}
  #tooltip {{ position: fixed; display: none; max-width: 420px; pointer-events: none; z-index: 20; background: rgba(15,23,42,0.96); border: 1px solid #475569; border-radius: 12px; padding: 12px 14px; box-shadow: 0 18px 50px rgba(0,0,0,0.35); }}
  #tooltip .title {{ font-weight: 600; margin-bottom: 4px; }}
  #tooltip .meta {{ font-size: 12px; color: #94a3b8; margin-bottom: 8px; }}
  #tooltip .desc {{ font-size: 12px; line-height: 1.5; color: #cbd5e1; white-space: pre-wrap; word-break: break-word; }}
</style>
<script src="https://unpkg.com/cytoscape/dist/cytoscape.min.js"></script>
</head>
<body>
<div id="legend"></div>
<div id="tooltip"><div class="title"></div><div class="meta"></div><div class="desc"></div></div>
<div id="app">
  <div id="cy"></div>
  <aside id="sidebar">
    <h3>Memory Graph</h3>
    <div class="muted">Hover to preview. Click a node to focus its neighbors.</div>
    <div class="block">
      <div id="detail-title">No selection</div>
      <div id="detail-meta" class="muted"></div>
      <pre id="detail-content">Move the mouse over a node or edge to inspect it.</pre>
    </div>
  </aside>
</div>
<script>
const elements = {elements_json};
const typeColors = {type_colors_json};
const linkStyles = {link_styles_json};
const tooltip = document.getElementById('tooltip');
const detailTitle = document.getElementById('detail-title');
const detailMeta = document.getElementById('detail-meta');
const detailContent = document.getElementById('detail-content');

const cy = cytoscape({{
  container: document.getElementById('cy'),
  elements,
  style: [
    {{
      selector: 'node',
      style: {{
        'shape': 'round-rectangle',
        'background-color': ele => typeColors[ele.data('memory_type')] || '#64748b',
        'label': 'data(label)',
        'color': '#e2e8f0',
        'font-size': 11,
        'text-wrap': 'wrap',
        'text-max-width': 120,
        'text-valign': 'center',
        'text-halign': 'center',
        'width': 120,
        'height': 42,
        'padding': '10px',
        'border-width': 2,
        'border-color': '#0f172a'
      }}
    }},
    {{
      selector: 'edge',
      style: {{
        'curve-style': 'bezier',
        'target-arrow-shape': 'triangle',
        'line-color': ele => (linkStyles[ele.data('link_type')] || {{color:'#94a3b8'}}).color,
        'target-arrow-color': ele => (linkStyles[ele.data('link_type')] || {{color:'#94a3b8'}}).color,
        'line-style': ele => (linkStyles[ele.data('link_type')] || {{dash:'none'}}).dash === 'none' ? 'solid' : 'dashed',
        'width': ele => Math.max(2, Number(ele.data('weight') || 1) * 4),
        'opacity': 0.85
      }}
    }},
    {{
      selector: '.faded',
      style: {{ 'opacity': 0.12 }}
    }},
    {{
      selector: '.highlighted',
      style: {{ 'opacity': 1, 'z-index': 999 }}
    }}
  ],
  layout: {{ name: 'cose', animate: false, padding: 36 }}
}});

function renderLegend() {{
  const legend = document.getElementById('legend');
  const typeItems = Object.entries(typeColors).map(([k, v]) => `<div class="legend-item"><span class="legend-chip" style="background:${{v}}"></span><span>${{k}}</span></div>`).join('');
  const edgeItems = Object.entries(linkStyles).map(([k, v]) => `<div class="legend-item"><span class="legend-line" style="background:${{v.color}}"></span><span>${{k}}</span></div>`).join('');
  legend.innerHTML = `<h4>Memory Types</h4>${{typeItems}}<h4 style="margin-top:12px;">Link Types</h4>${{edgeItems}}`;
}}

function showTooltip(evt, title, meta, desc) {{
  tooltip.style.display = 'block';
  tooltip.querySelector('.title').textContent = title || '';
  tooltip.querySelector('.meta').textContent = meta || '';
  tooltip.querySelector('.desc').textContent = desc || '';
  tooltip.style.left = (evt.originalEvent.clientX + 16) + 'px';
  tooltip.style.top = (evt.originalEvent.clientY + 16) + 'px';
}}

function hideTooltip() {{
  tooltip.style.display = 'none';
}}

function showNodeDetails(node) {{
  detailTitle.textContent = node.data('label') || node.id();
  detailMeta.textContent = `${{node.data('memory_type') || 'unknown'}} · ${{node.data('uri') || ''}}`;
  detailContent.textContent = node.data('content_preview') || '(empty)';
  if (node.data('content_truncated')) {{
    detailContent.textContent += '\\n\\n[preview truncated]';
  }}
}}

function showEdgeDetails(edge) {{
  detailTitle.textContent = edge.data('link_type') || 'relation';
  detailMeta.textContent = `weight=${{edge.data('weight')}}`;
  detailContent.textContent = edge.data('description') || '(no description)';
}}

function focusNeighborhood(node) {{
  cy.elements().addClass('faded').removeClass('highlighted');
  const neighborhood = node.closedNeighborhood();
  neighborhood.removeClass('faded').addClass('highlighted');
}}

cy.on('mouseover', 'node', evt => {{
  const node = evt.target;
  const meta = `${{node.data('memory_type') || 'unknown'}} · ${{node.data('uri') || ''}}`;
  let desc = node.data('content_preview') || '(empty)';
  if (node.data('content_truncated')) desc += '\\n\\n[preview truncated]';
  showTooltip(evt, node.data('label') || node.id(), meta, desc);
  showNodeDetails(node);
}});

cy.on('mouseover', 'edge', evt => {{
  const edge = evt.target;
  const meta = `${{edge.data('link_type')}} · weight=${{edge.data('weight')}}`;
  showTooltip(evt, edge.data('description') || edge.data('link_type'), meta, edge.data('description') || '(no description)');
  showEdgeDetails(edge);
}});

cy.on('mousemove', 'node, edge', evt => {{
  tooltip.style.left = (evt.originalEvent.clientX + 16) + 'px';
  tooltip.style.top = (evt.originalEvent.clientY + 16) + 'px';
}});

cy.on('mouseout', 'node, edge', () => hideTooltip());
cy.on('tap', 'node', evt => focusNeighborhood(evt.target));
cy.on('tap', evt => {{
  if (evt.target === cy) {{
    cy.elements().removeClass('faded highlighted');
    detailTitle.textContent = 'No selection';
    detailMeta.textContent = '';
    detailContent.textContent = 'Move the mouse over a node or edge to inspect it.';
  }}
}});

renderLegend();
</script>
</body>
</html>"""
