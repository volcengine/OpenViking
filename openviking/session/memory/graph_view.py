# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory graph generator — builds a self-contained D3.js force-directed HTML graph
from all links stored in MEMORY_FIELDS across a memory space.

Usage:
    graph = MemoryGraph(viking_fs)
    path = await graph.gen_graph("viking://user/{space}/memories", ctx=ctx)
"""

import json
from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext
from openviking.session.memory.utils.messages import parse_memory_file_with_fields
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

    async def gen_graph(
        self,
        space_uri: str,
        ctx: RequestContext,
    ) -> str:
        """Scan all memory files, extract links, build graph HTML, write to space.

        Args:
            space_uri: Root URI (e.g. viking://user/{space}/memories)
            ctx: Request context

        Returns:
            URI of the generated .graph.html file
        """
        viking_fs = self._get_viking_fs()
        if not viking_fs:
            raise ValueError("VikingFS not available")

        # 1. Glob all .md files recursively
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

        logger.info(f"[gen_graph] Found {len(md_uris)} memory files under {space_uri}")

        # 2. Parse each file, collect nodes + edges
        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []

        for uri in md_uris:
            try:
                content = await viking_fs.read_file(uri, ctx=ctx)
                if not content:
                    continue
                parsed = parse_memory_file_with_fields(content)
            except Exception as e:
                logger.warning(f"Failed to read/parse {uri}: {e}")
                continue

            memory_type = parsed.get("memory_type", "")
            category = parsed.get("category", "")
            name = parsed.get("name", "")
            label = name if name else uri.split("/")[-1].replace(".md", "")

            nodes[uri] = {
                "id": uri,
                "uri": uri,
                "label": label,
                "memory_type": memory_type,
                "category": category,
            }

            for link_data in parsed.get("links", []):
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

        # 3. Ensure link endpoints exist as nodes
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

        # 4. Deduplicate and filter edges
        seen = set()
        unique_edges = []
        for e in edges:
            if e["source"] not in nodes or e["target"] not in nodes:
                continue
            key = (e["source"], e["target"], e["link_type"])
            if key not in seen:
                seen.add(key)
                unique_edges.append(e)

        logger.info(f"[gen_graph] Built graph: {len(nodes)} nodes, {len(unique_edges)} edges")

        # 5. Render & write
        html = _render_graph_html(list(nodes.values()), unique_edges)
        graph_path = f"{space_uri.rstrip('/')}/.graph.html"
        try:
            await viking_fs.write_file(graph_path, html, ctx=ctx)
            tracer.info(f"[gen_graph] Generated graph: {graph_path}")
        except Exception as e:
            logger.error(f"Failed to write graph {graph_path}: {e}")
            raise

        return graph_path


# ---------------------------------------------------------------------------
# HTML template — self-contained D3.js force graph (Obsidian-style)
# ---------------------------------------------------------------------------


def _render_graph_html(nodes: List[Dict], edges: List[Dict]) -> str:
    nodes_json = json.dumps(nodes, ensure_ascii=False)
    edges_json = json.dumps(edges, ensure_ascii=False)
    type_colors_json = json.dumps(TYPE_COLORS)
    link_styles_json = json.dumps(LINK_STYLES)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Memory Graph</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #1a1a2e; font-family: -apple-system, sans-serif; overflow: hidden; }}
  #tooltip {{
    position: absolute; display: none; background: rgba(30,30,50,0.95);
    border: 1px solid #444; border-radius: 6px; padding: 8px 12px;
    color: #eee; font-size: 12px; max-width: 320px; pointer-events: none; z-index: 10;
  }}
  #tooltip .tt-label {{ font-weight: bold; font-size: 14px; margin-bottom: 4px; }}
  #tooltip .tt-type {{ color: #888; margin-bottom: 2px; }}
  #tooltip .tt-uri {{ color: #666; font-size: 10px; word-break: break-all; }}
  #legend {{
    position: absolute; top: 12px; right: 12px; background: rgba(30,30,50,0.9);
    border: 1px solid #444; border-radius: 6px; padding: 10px 14px; color: #ccc;
    font-size: 11px;
  }}
  #legend h4 {{ margin-bottom: 6px; font-size: 13px; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; margin: 3px 0; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
  .legend-line {{ width: 20px; height: 2px; flex-shrink: 0; }}
  #stats {{
    position: absolute; bottom: 12px; left: 12px; color: #666; font-size: 11px;
  }}
</style>
</head>
<body>
<div id="tooltip"><div class="tt-label"></div><div class="tt-type"></div><div class="tt-uri"></div></div>
<div id="legend"></div>
<div id="stats"></div>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const nodes = {nodes_json};
const edges = {edges_json};
const typeColors = {type_colors_json};
const linkStyles = {link_styles_json};

const W = window.innerWidth, H = window.innerHeight;
const tooltip = document.getElementById('tooltip');
const legend = document.getElementById('legend');
const stats = document.getElementById('stats');

const svg = d3.select('body').append('svg').attr('width', W).attr('height', H);
const g = svg.append('g');
svg.call(d3.zoom().scaleExtent([0.1, 8]).on('zoom', e => g.attr('transform', e.transform)));

// Arrow markers per link type
const linkTypes = [...new Set(edges.map(e => e.link_type))];
const defs = svg.append('defs');
linkTypes.forEach(lt => {{
  const style = linkStyles[lt] || {{color:'#999',dash:'none'}};
  defs.append('marker')
    .attr('id', 'arrow-' + lt)
    .attr('viewBox', '0 -5 10 10')
    .attr('refX', 20).attr('refY', 0)
    .attr('markerWidth', 6).attr('markerHeight', 6)
    .attr('orient', 'auto')
    .append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', style.color);
}});

// Simulation
const simulation = d3.forceSimulation(nodes)
  .force('link', d3.forceLink(edges).id(d => d.id).distance(80).strength(0.5))
  .force('charge', d3.forceManyBody().strength(-200))
  .force('center', d3.forceCenter(W/2, H/2))
  .force('collision', d3.forceCollide().radius(18));

// Edges
const link = g.append('g').selectAll('line')
  .data(edges).join('line')
  .attr('stroke', d => (linkStyles[d.link_type]||{{}}).color || '#555')
  .attr('stroke-width', d => Math.max(1, d.weight * 2.5))
  .attr('stroke-dasharray', d => (linkStyles[d.link_type]||{{}}).dash || 'none')
  .attr('marker-end', d => 'url(#arrow-' + d.link_type + ')')
  .attr('opacity', 0.6);

// Nodes
const node = g.append('g').selectAll('circle')
  .data(nodes).join('circle')
  .attr('r', d => {{
    const deg = edges.filter(e => e.source.id === d.id || e.target.id === d.id).length;
    return 6 + Math.min(deg * 1.5, 16);
  }})
  .attr('fill', d => typeColors[d.memory_type] || '#636e72')
  .attr('stroke', '#2d3436').attr('stroke-width', 1.5)
  .call(d3.drag()
    .on('start', (e,d) => {{ if(!e.active) simulation.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }})
    .on('drag', (e,d) => {{ d.fx=e.x; d.fy=e.y; }})
    .on('end', (e,d) => {{ if(!e.active) simulation.alphaTarget(0); d.fx=null; d.fy=null; }})
  );

// Labels
const label = g.append('g').selectAll('text')
  .data(nodes).join('text')
  .text(d => d.label.length > 20 ? d.label.slice(0,18)+'…' : d.label)
  .attr('font-size', 10).attr('fill', '#ccc')
  .attr('text-anchor', 'middle').attr('dy', -14)
  .attr('pointer-events', 'none');

// Hover highlight
node.on('mouseover', (e, d) => {{
  tooltip.style.display = 'block';
  tooltip.querySelector('.tt-label').textContent = d.label;
  tooltip.querySelector('.tt-type').textContent = d.memory_type ? '[' + d.memory_type + ']' + (d.category ? ' ' + d.category : '') : '';
  tooltip.querySelector('.tt-uri').textContent = d.uri;
  const connected = new Set([d.id]);
  edges.forEach(e => {{
    if (e.source.id === d.id) connected.add(e.target.id);
    if (e.target.id === d.id) connected.add(e.source.id);
  }});
  node.attr('opacity', n => connected.has(n.id) ? 1 : 0.15);
  link.attr('opacity', e => (e.source.id === d.id || e.target.id === d.id) ? 0.9 : 0.05);
  label.attr('opacity', n => connected.has(n.id) ? 1 : 0.1);
}})
.on('mousemove', e => {{
  tooltip.style.left = (e.pageX + 12) + 'px';
  tooltip.style.top = (e.pageY - 28) + 'px';
}})
.on('mouseout', () => {{
  tooltip.style.display = 'none';
  node.attr('opacity', 1);
  link.attr('opacity', 0.6);
  label.attr('opacity', 1);
}});

// Tick
simulation.on('tick', () => {{
  link.attr('x1', d=>d.source.x).attr('y1', d=>d.source.y)
      .attr('x2', d=>d.target.x).attr('y2', d=>d.target.y);
  node.attr('cx', d=>d.x).attr('cy', d=>d.y);
  label.attr('x', d=>d.x).attr('y', d=>d.y);
}});

// Legend
let legendHtml = '<h4>Nodes (memory_type)</h4>';
const usedTypes = [...new Set(nodes.map(n => n.memory_type).filter(Boolean))];
usedTypes.forEach(t => {{
  legendHtml += '<div class="legend-item"><div class="legend-dot" style="background:' +
    (typeColors[t]||'#636e72') + '"></div>' + t + '</div>';
}});
const usedLinkTypes = [...new Set(edges.map(e => e.link_type).filter(Boolean))];
if (usedLinkTypes.length) {{
  legendHtml += '<h4 style="margin-top:8px">Edges (link_type)</h4>';
  usedLinkTypes.forEach(lt => {{
    const s = linkStyles[lt] || {{color:'#999',dash:'none'}};
    legendHtml += '<div class="legend-item"><div class="legend-line" style="background:' +
      s.color + '"></div>' + lt + '</div>';
  }});
}}
legend.innerHTML = legendHtml;

stats.textContent = nodes.length + ' nodes · ' + edges.length + ' edges';
</script>
</body>
</html>"""
