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

import hashlib
import json
from typing import Any, Dict, List

from openviking.server.identity import RequestContext
from openviking.session.memory.utils.link_renderer import LinkRenderer
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


def _color_for_link_type(link_type: str) -> str:
    normalized = (link_type or "related_to").strip() or "related_to"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    hue = int(digest[:8], 16) % 360
    return f"hsl({hue}, 72%, 58%)"


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

    @staticmethod
    def _infer_memory_type(uri: str, parsed_memory_type: str) -> str:
        if parsed_memory_type:
            return parsed_memory_type
        parts = [part for part in uri.split("/") if part]
        if len(parts) >= 2 and parts[-1].endswith(".md"):
            parent = parts[-2]
            if parent != "memories":
                return parent
            return parts[-1].replace(".md", "")
        return ""

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
            entries = await viking_fs.tree(
                space_uri,
                node_limit=1000000,
                level_limit=None,
                ctx=ctx,
            )
            md_uris = [
                entry["uri"]
                for entry in entries
                if not entry.get("isDir")
                and entry.get("rel_path", "").endswith(".md")
                and not entry.get("rel_path", "").endswith("/.overview.md")
                and not entry.get("rel_path", "").endswith("/.abstract.md")
            ]

            logger.info(f"[build_graph] Found {len(md_uris)} memory files under {space_uri}")

            for uri in md_uris:
                try:
                    content = await viking_fs.read_file(uri, ctx=ctx)
                    if not content:
                        continue
                    mf = MemoryFileUtils.read(content, uri=uri)
                except Exception as e:
                    logger.error(f"Failed to read/parse {uri}: {e}")
                    raise

                inferred_memory_type = self._infer_memory_type(uri, mf.memory_type or "")
                category = mf.extra_fields.get("category", "")
                name = mf.extra_fields.get("name", "")
                label = name if name else uri.split("/")[-1].replace(".md", "")

                rendered_content = LinkRenderer.render_links(mf.content or "", uri, mf.links)

                nodes[uri] = {
                    "id": uri,
                    "uri": uri,
                    "label": label,
                    "memory_type": inferred_memory_type,
                    "category": category,
                    "content_preview": self._build_content_preview(rendered_content),
                    "content_full": rendered_content,
                    "content_truncated": self._is_content_truncated(rendered_content),
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
    def _script_safe_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False).replace("</", r"<\/")

    vis_nodes = []
    for node in nodes:
        color = TYPE_COLORS.get(node.get("memory_type") or "", "#64748b")
        vis_nodes.append(
            {
                "id": node["id"],
                "label": node.get("label") or node["id"],
                "shape": "box",
                "color": {
                    "background": "#0f172a",
                    "border": color,
                    "highlight": {"background": "#f8fafc", "border": color},
                    "hover": {"background": "#0f172a", "border": color},
                },
                "font": {"color": "#f8fafc", "size": 12},
                "margin": 10,
                "widthConstraint": {"minimum": 120, "maximum": 180},
                "memory_type": node.get("memory_type", ""),
                "category": node.get("category", ""),
                "uri": node.get("uri", ""),
                "content_preview": node.get("content_preview", ""),
                "content_full": node.get("content_full", ""),
                "content_truncated": node.get("content_truncated", False),
            }
        )

    vis_edges = []
    for idx, edge in enumerate(edges):
        link_type = edge.get("link_type") or "related_to"
        color = _color_for_link_type(link_type)
        vis_edges.append(
            {
                "id": f"edge-{idx}",
                "from": edge["source"],
                "to": edge["target"],
                "label": link_type,
                "color": {"color": color, "highlight": color, "hover": color},
                "dashes": False,
                "width": max(2, float(edge.get("weight", 1.0)) * 4),
                "arrows": "to",
                "smooth": {"type": "dynamic"},
                "link_type": link_type,
                "weight": float(edge.get("weight", 1.0)),
                "description": edge.get("description", ""),
            }
        )

    nodes_json = _script_safe_json(vis_nodes)
    edges_json = _script_safe_json(vis_edges)
    type_colors_json = _script_safe_json(TYPE_COLORS)

    return rf"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Graph</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #0f172a; color: #e2e8f0; }}
  #app {{ display: grid; grid-template-columns: 1fr 320px; height: 100vh; }}
  #graph {{ width: 100%; height: 100vh; background: radial-gradient(circle at top, #1e293b 0%, #0f172a 60%); }}
  #sidebar {{ border-left: 1px solid #334155; background: rgba(15, 23, 42, 0.96); padding: 16px; overflow: auto; }}
  #sidebar h3 {{ margin: 0 0 12px; font-size: 16px; }}
  #sidebar .muted {{ color: #94a3b8; font-size: 12px; }}
  #sidebar .block {{ margin-top: 14px; padding-top: 14px; border-top: 1px solid #1e293b; }}
  #detail-content {{ white-space: normal; word-break: break-word; font-size: 12px; line-height: 1.6; color: #cbd5e1; }}
  #detail-content p, #detail-content ul, #detail-content ol, #detail-content pre, #detail-content blockquote, #detail-content h1, #detail-content h2, #detail-content h3, #detail-content h4 {{ margin: 0 0 12px; }}
  #detail-content ul, #detail-content ol {{ padding-left: 18px; }}
  #detail-content code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: rgba(30, 41, 59, 0.9); padding: 1px 4px; border-radius: 4px; }}
  #detail-content pre {{ overflow-x: auto; background: rgba(15, 23, 42, 0.72); padding: 10px 12px; border-radius: 8px; }}
  #detail-content pre code {{ background: transparent; padding: 0; }}
  #detail-content blockquote {{ border-left: 3px solid #475569; padding-left: 10px; color: #94a3b8; }}
  #detail-content a {{ color: #60a5fa; text-decoration: underline; cursor: pointer; }}
  #legend {{ position: fixed; left: 16px; top: 16px; z-index: 10; background: rgba(15, 23, 42, 0.92); border: 1px solid #334155; border-radius: 12px; padding: 12px 14px; max-width: 300px; box-shadow: 0 10px 30px rgba(0,0,0,0.25); }}
  #legend h4 {{ margin: 0 0 8px; font-size: 13px; }}
  .legend-item {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; font-size: 12px; color: #cbd5e1; }}
  .legend-item button {{ all: unset; display: flex; align-items: center; gap: 8px; cursor: pointer; width: 100%; border-radius: 8px; padding: 4px 6px; }}
  .legend-item button.active {{ background: rgba(51, 65, 85, 0.85); }}
  .legend-item button.dimmed {{ opacity: 0.55; }}
  .legend-chip {{ width: 12px; height: 12px; border-radius: 4px; flex: 0 0 12px; }}
  .legend-line {{ width: 22px; height: 3px; border-radius: 999px; flex: 0 0 22px; }}
  #tooltip {{ position: fixed; display: none; max-width: 420px; pointer-events: none; z-index: 20; background: rgba(15,23,42,0.96); border: 1px solid #475569; border-radius: 12px; padding: 12px 14px; box-shadow: 0 18px 50px rgba(0,0,0,0.35); }}
  #tooltip .title {{ font-weight: 600; margin-bottom: 4px; }}
  #tooltip .meta {{ font-size: 12px; color: #94a3b8; margin-bottom: 8px; }}
  #tooltip .desc {{ font-size: 12px; line-height: 1.5; color: #cbd5e1; white-space: pre-wrap; word-break: break-word; }}
</style>
<script>
window.__OPENVIKING_VIS_NETWORK_SOURCE__ = "external";
</script>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
</head>
<body>
<div id="legend"></div>
<div id="tooltip"><div class="title"></div><div class="meta"></div><div class="desc"></div></div>
<div id="app">
  <div id="graph"></div>
  <aside id="sidebar">
    <div class="block">
      <div id="detail-title">No selection</div>
      <div id="detail-meta" class="muted"></div>
      <div id="detail-content">Move the mouse over a node or edge to inspect it.</div>
    </div>
  </aside>
</div>
<script>
const originalNodes = {nodes_json};
const originalEdges = {edges_json};
const typeColors = {type_colors_json};
const tooltip = document.getElementById('tooltip');
const detailTitle = document.getElementById('detail-title');
const detailMeta = document.getElementById('detail-meta');
const detailContent = document.getElementById('detail-content');
const legend = document.getElementById('legend');

function renderInitError(message) {{
  const graph = document.getElementById('graph');
  if (graph) {{
    graph.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;padding:24px;color:#e2e8f0;text-align:center;line-height:1.6;">${{message}}</div>`;
  }}
  detailTitle.textContent = 'Graph unavailable';
  detailMeta.textContent = '';
  detailContent.textContent = message;
  legend.innerHTML = '';
}}

if (!window.vis || !window.vis.DataSet || !window.vis.Network) {{
  renderInitError('Graph library failed to load. If you opened this file locally, check network access or regenerate it with an embedded graph library bundle.');
}} else {{
const nodes = new vis.DataSet(originalNodes);
const edges = new vis.DataSet(originalEdges);
const activeMemoryTypes = new Set();
const activeLinkTypes = new Set();

const network = new vis.Network(
  document.getElementById('graph'),
  {{ nodes, edges }},
  {{
    autoResize: true,
    interaction: {{ hover: true, tooltipDelay: 100, navigationButtons: true, keyboard: true }},
    physics: {{
      stabilization: {{ iterations: 250, updateInterval: 25 }},
      barnesHut: {{
        gravitationalConstant: -7000,
        centralGravity: 0.18,
        springLength: 120,
        springConstant: 0.02,
        damping: 0.24,
        avoidOverlap: 0.2,
      }},
      minVelocity: 0.75,
    }},
    nodes: {{
      shape: 'box',
      borderWidth: 2,
      shadow: false,
      scaling: {{
        min: 18,
        max: 42,
        label: {{ enabled: true, min: 11, max: 20 }},
      }},
      chosen: {{
        node(values, id, selected) {{
          if (!selected) {{
            return;
          }}
          values.borderWidth = 4;
          values.size = Math.max(values.size || 18, 34);
          values.shadow = true;
          values.shadowColor = 'rgba(148, 163, 184, 0.55)';
          values.shadowSize = 28;
          values.shadowX = 0;
          values.shadowY = 0;
          values.color = {{ background: '#f8fafc', border: values.color.border }};
        }},
        label(values, id, selected) {{
          values.size = Math.max(values.size || 12, 18);
          values.bold = true;
          if (!selected) {{
            return;
          }}
          values.color = '#0f172a';
        }},
      }},
    }},
    edges: {{ smooth: true, font: {{ color: '#cbd5e1', size: 10, strokeWidth: 0 }}, arrows: {{ to: {{ enabled: true, scaleFactor: 0.7 }} }} }},
  }}
);

function resolveRelativeUri(baseUri, relativeUri) {{
  if (!relativeUri) {{
    return '';
  }}
  if (relativeUri.startsWith('viking://')) {{
    return relativeUri;
  }}
  if (!baseUri.startsWith('viking://')) {{
    return relativeUri;
  }}

  const baseParts = baseUri.split('/');
  baseParts.pop();

  for (const segment of relativeUri.split('/')) {{
    if (!segment || segment === '.') {{
      continue;
    }}
    if (segment === '..') {{
      if (baseParts.length > 3) {{
        baseParts.pop();
      }}
      continue;
    }}
    baseParts.push(segment);
  }}

  return baseParts.join('/');
}}

function escapeHtml(text) {{
  return (text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}}

function escapedPreviewText(text, truncated) {{
  return text || '(empty)';
}}

function renderMarkdown(text, baseUri = '') {{
  const source = text || '';
  if (!source.trim()) {{
    return '<p>(empty)</p>';
  }}

  const codeBlocks = [];
  let html = escapeHtml(source).replace(/```([\s\S]*?)```/g, (_, code) => {{
    const token = `__CODE_BLOCK_${{codeBlocks.length}}__`;
    codeBlocks.push(`<pre><code>${{code.trim()}}</code></pre>`);
    return token;
  }});

  html = html.replace(/^###\s+(.*)$/gm, '<h3>$1</h3>');
  html = html.replace(/^##\s+(.*)$/gm, '<h2>$1</h2>');
  html = html.replace(/^#\s+(.*)$/gm, '<h1>$1</h1>');
  html = html.replace(/^>\s?(.*)$/gm, '<blockquote>$1</blockquote>');
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {{
    const resolvedUri = resolveRelativeUri(baseUri, href);
    if (resolvedUri.startsWith('viking://')) {{
      return `<a href="${{href}}" data-target-uri="${{resolvedUri}}">${{label}}</a>`;
    }}
    return `<a href="${{href}}">${{label}}</a>`;
  }});
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  const lines = html.split('\\n');
  const rendered = [];
  let listItems = [];

  const flushList = () => {{
    if (listItems.length > 0) {{
      rendered.push(`<ul>${{listItems.join('')}}</ul>`);
      listItems = [];
    }}
  }};

  for (const line of lines) {{
    if (/^<h[1-3]>|^<blockquote>|^<pre>/.test(line)) {{
      flushList();
      rendered.push(line);
      continue;
    }}
    if (/^[-*]\s+/.test(line)) {{
      listItems.push(`<li>${{line.replace(/^[-*]\s+/, '')}}</li>`);
      continue;
    }}
    if (!line.trim()) {{
      flushList();
      continue;
    }}
    flushList();
    rendered.push(`<p>${{line}}</p>`);
  }}
  flushList();

  html = rendered.join('');
  codeBlocks.forEach((block, index) => {{
    html = html.replace(`__CODE_BLOCK_${{index}}__`, block);
  }});
  return html;
}}

function showTooltip(x, y, title, meta, desc, baseUri = '') {{
  tooltip.style.display = 'block';
  tooltip.querySelector('.title').textContent = title || '';
  tooltip.querySelector('.meta').textContent = meta || '';
  tooltip.querySelector('.desc').innerHTML = renderMarkdown(desc || '', baseUri);
  tooltip.style.left = (x + 16) + 'px';
  tooltip.style.top = (y + 16) + 'px';
}}

function hideTooltip() {{
  tooltip.style.display = 'none';
}}

function focusNodeById(targetNodeId, options = {{}}) {{
  if (!targetNodeId || !nodes.get(targetNodeId)) {{
    return;
  }}
  const shouldCenter = options.center === true;
  network.unselectAll();
  network.selectNodes([targetNodeId]);
  if (shouldCenter) {{
    network.focus(targetNodeId, {{ animation: {{ duration: 350, easingFunction: 'easeInOutQuad' }}, scale: network.getScale() }});
  }}
  showNodeDetails(nodes.get(targetNodeId));
}}

function showNodeDetails(node) {{
  detailTitle.textContent = node.label || node.id;
  detailMeta.textContent = `${{node.memory_type || 'unknown'}} · ${{node.uri || ''}}`;
  detailContent.innerHTML = renderMarkdown(node.content_full || node.content_preview || '', node.uri || '');
}}

function showEdgeDetails(edge) {{
  detailTitle.textContent = edge.link_type || 'relation';
  detailMeta.textContent = `${{edge.link_type}} · weight=${{edge.weight}}`;
  const sourceUri = edge.from || edge.source || '';
  const targetUri = edge.to || edge.target || '';
  detailContent.innerHTML = renderMarkdown(`- from_uri: ${{escapeHtml(sourceUri)}}\n- to_uri: ${{escapeHtml(targetUri)}}\n\n${{escapeHtml(edge.description || '(no description)')}}`);
}}

function renderLegend() {{
  const typeItems = Object.entries(typeColors).map(([k, v]) => {{
    const isActive = activeMemoryTypes.has(k);
    const activeClass = isActive ? 'active' : (activeMemoryTypes.size > 0 ? 'dimmed' : '');
    return `<div class="legend-item"><button class="${{activeClass}}" data-memory-type="${{k}}"><span class="legend-chip" style="background:${{v}}"></span><span>${{k}}</span></button></div>`;
  }}).join('');
  const linkTypes = [...new Set(originalEdges.map((edge) => edge.link_type).filter(Boolean))].sort();
  const edgeItems = linkTypes.map((linkType) => {{
    const color = originalEdges.find((edge) => edge.link_type === linkType)?.color?.color || '#94a3b8';
    const isActive = activeLinkTypes.has(linkType);
    const activeClass = isActive ? 'active' : (activeLinkTypes.size > 0 ? 'dimmed' : '');
    return `<div class="legend-item"><button class="${{activeClass}}" data-link-type="${{linkType}}"><span class="legend-line" style="background:${{color}}"></span><span>${{linkType}}</span></button></div>`;
  }}).join('');
  const linkSection = edgeItems ? `<h4 style="margin-top:12px;">Link Types</h4>${{edgeItems}}` : '';
  legend.innerHTML = `<h4>Memory Types</h4>${{typeItems}}<div class="muted" style="margin:6px 0 10px;">Click types to filter. Click again to clear each selection.</div>${{linkSection}}`;
  legend.querySelectorAll('button[data-memory-type]').forEach((button) => {{
    button.addEventListener('click', () => {{
      const memoryType = button.dataset.memoryType;
      if (activeMemoryTypes.has(memoryType)) {{
        activeMemoryTypes.delete(memoryType);
      }} else {{
        activeMemoryTypes.add(memoryType);
      }}
      applyFilter();
      renderLegend();
    }});
  }});
  legend.querySelectorAll('button[data-link-type]').forEach((button) => {{
    button.addEventListener('click', () => {{
      const linkType = button.dataset.linkType;
      if (activeLinkTypes.has(linkType)) {{
        activeLinkTypes.delete(linkType);
      }} else {{
        activeLinkTypes.add(linkType);
      }}
      applyFilter();
      renderLegend();
    }});
  }});
}}

function restoreVisibleGraph(selectedNodeIds = []) {{
  const nodeSelection = new Set(selectedNodeIds);
  nodes.update(originalNodes.map(node => ({{
    id: node.id,
    hidden: false,
  }})));
  edges.update(originalEdges.map((edge, index) => ({{
    id: edge.id || `edge-${{index}}`,
    hidden: false,
  }})));
  network.unselectAll();
  if (selectedNodeIds.length > 0) {{
    network.selectNodes(selectedNodeIds);
  }}
  network.setOptions({{ physics: false }});
}}

function applyFilter(shouldFit = false) {{
  if (activeMemoryTypes.size === 0 && activeLinkTypes.size === 0) {{
    restoreVisibleGraph();
    return;
  }}

  const visibleNodes = originalNodes.filter(node => activeMemoryTypes.size === 0 || activeMemoryTypes.has(node.memory_type));
  const visibleNodeIds = new Set(visibleNodes.map(node => node.id));
  const visibleEdges = originalEdges.filter(edge => (activeLinkTypes.size === 0 || activeLinkTypes.has(edge.link_type)) && visibleNodeIds.has(edge.from) && visibleNodeIds.has(edge.to));
  const visibleEdgeIds = new Set(visibleEdges.map(edge => edge.id));

  nodes.update(originalNodes.map(node => ({{
    id: node.id,
    hidden: !visibleNodeIds.has(node.id),
  }})));
  edges.update(originalEdges.map((edge, index) => ({{
    id: edge.id || `edge-${{index}}`,
    hidden: !visibleEdgeIds.has(edge.id || `edge-${{index}}`),
  }})));

  network.unselectAll();
  network.selectNodes(visibleNodes.map(node => node.id));
  network.setOptions({{ physics: false }});
}}

detailContent.addEventListener('click', (event) => {{
  const link = event.target.closest('a[data-target-uri]');
  if (!link) {{
    return;
  }}
  event.preventDefault();
  const targetNodeId = link.dataset.targetUri;
  focusNodeById(targetNodeId, {{ center: true }});
}});

network.once('stabilized', () => {{
  network.fit({{ animation: false, padding: 80 }});
  network.setOptions({{ physics: false }});
}});

network.on('hoverNode', (params) => {{
  const node = nodes.get(params.node);
  const meta = `${{node.memory_type || 'unknown'}} · ${{node.uri || ''}}`;
  const desc = escapedPreviewText(node.content_preview, node.content_truncated);
  showTooltip(params.event.srcEvent.clientX, params.event.srcEvent.clientY, node.label || node.id, meta, desc, node.uri || '');
  showNodeDetails(node);
}});

network.on('hoverEdge', (params) => {{
  const edge = edges.get(params.edge);
  const meta = `${{edge.link_type}} · weight=${{edge.weight}}`;
  showTooltip(params.event.srcEvent.clientX, params.event.srcEvent.clientY, edge.description || edge.link_type, meta, edge.description || '(no description)', '');
  showEdgeDetails(edge);
}});

network.on('blurNode', hideTooltip);
network.on('blurEdge', hideTooltip);
network.on('click', (params) => {{
  if (params.nodes.length > 0) {{
    const focusNodeId = params.nodes[0];
    focusNodeById(focusNodeId);
    return;
  }}

  if (params.edges.length > 0) {{
    showEdgeDetails(edges.get(params.edges[0]));
    return;
  }}

  restoreVisibleGraph();
  detailTitle.textContent = 'No selection';
  detailMeta.textContent = '';
  detailContent.innerHTML = '<p>Move the mouse over a node or edge to inspect it.</p>';
}});

renderLegend();
}}
</script>
</body>
</html>"""
