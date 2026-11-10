#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Generate an interactive graph for one or more compiled LLM-Wiki URIs.

The script connects to an OpenViking service, verifies that the service is
healthy, checks the current identity's read access to every supplied URI, and
then writes a standalone HTML document containing the Wiki pages and graph.

Connection settings are resolved in the usual OpenViking order: command-line
arguments, OPENVIKING_* environment variables, then ~/.openviking/ovcli.conf.

Examples:
    python wiki_graph.py viking://resources/my-wiki
    python wiki_graph.py \
        viking://resources/wiki-a viking://~/resources/wiki-b \
        --output llm-wiki-graph.html --title "团队知识图谱"
    python wiki_graph.py viking://resources/my-wiki \
        --url http://localhost:1933 --api-key "$OPENVIKING_API_KEY"
"""

from __future__ import annotations

import argparse
import html
import json
import os
import posixpath
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, TypeVar
from urllib.parse import unquote

import openviking as ov

_T = TypeVar("_T")

_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(\s*(<[^>]+>|[^)\s]+)(?:\s+[\"'][^)]*[\"'])?\s*\)")
_FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)
_FENCE_RE = re.compile(r"^\s*```([^`]*)$")

_CATEGORY_ALIASES = {
    "index": "index",
    "entity": "entity",
    "entities": "entity",
    "concept": "concept",
    "concepts": "concept",
    "method": "method",
    "methods": "method",
    "comparison": "comparison",
    "comparisons": "comparison",
    "analysis": "analysis",
    "analyses": "analysis",
    "synthesis": "analysis",
    "summary": "summary",
    "summaries": "summary",
    "source": "source",
    "sources": "source",
    "audit": "audit",
}

_CATEGORY_STYLE = {
    "index": ("导航", "#f43f5e"),
    "entity": ("实体", "#10b981"),
    "concept": ("概念", "#3b82f6"),
    "method": ("方法", "#8b5cf6"),
    "comparison": ("比较", "#06b6d4"),
    "analysis": ("分析", "#f59e0b"),
    "summary": ("摘要", "#ec4899"),
    "source": ("来源", "#64748b"),
    "audit": ("构建审计", "#a855f7"),
    "other": ("其他", "#94a3b8"),
}

_CATEGORY_ORDER = tuple(_CATEGORY_STYLE)


class GraphShowError(RuntimeError):
    """A user-facing failure with a stable process exit code."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class PageSpec:
    uri: str
    root_uri: str
    relative_path: str


@dataclass(frozen=True)
class WikiPage:
    uri: str
    root_uri: str
    relative_path: str
    title: str
    category: str
    body: str


def _normalize_input_uri(uri: str) -> str:
    value = uri.strip()
    if not value.startswith("viking://"):
        raise GraphShowError(f"不是有效的 Viking URI：{uri}", exit_code=2)
    if "#" in value or "?" in value:
        raise GraphShowError(f"Viking URI 不能包含查询参数或锚点：{uri}", exit_code=2)
    if value != "viking://":
        value = value.rstrip("/")
    return value


def _uri_payload(uri: str) -> str:
    return uri[len("viking://") :].strip("/")


def _join_uri(base_uri: str, relative_path: str) -> str:
    payload = posixpath.normpath(posixpath.join(_uri_payload(base_uri), relative_path))
    if payload in {"", "."}:
        return "viking://"
    return f"viking://{payload.lstrip('/')}"


def _relative_uri(uri: str, root_uri: str) -> str:
    uri_payload = _uri_payload(uri)
    root_payload = _uri_payload(root_uri)
    if not root_payload:
        return uri_payload
    prefix = f"{root_payload.rstrip('/')}/"
    if uri_payload.startswith(prefix):
        return uri_payload[len(prefix) :]
    return posixpath.basename(uri_payload)


def _is_directory(info: Mapping[str, Any]) -> bool:
    return bool(info.get("isDir", info.get("is_dir", False)))


def _entry_uri(root_uri: str, entry: Mapping[str, Any]) -> str | None:
    uri = entry.get("uri")
    if isinstance(uri, str) and uri.startswith("viking://"):
        return uri.rstrip("/")
    rel_path = entry.get("rel_path") or entry.get("path") or entry.get("name")
    if isinstance(rel_path, str) and rel_path:
        return _join_uri(root_uri, rel_path).rstrip("/")
    return None


def _checked_call(action: str, uri: str, operation: Callable[[], _T]) -> _T:
    try:
        return operation()
    except Exception as exc:
        code = str(getattr(exc, "code", "")).upper()
        if code == "UNAUTHENTICATED":
            raise GraphShowError(
                f"身份认证失败，无法{action} {uri}。请检查 API Key。", exit_code=4
            ) from exc
        if code == "PERMISSION_DENIED":
            raise GraphShowError(f"当前用户没有权限{action} {uri}。", exit_code=4) from exc
        if code == "NOT_FOUND":
            raise GraphShowError(f"URI 不存在：{uri}", exit_code=4) from exc
        if code in {"INVALID_URI", "INVALID_ARGUMENT"}:
            raise GraphShowError(f"无效的 Viking URI：{uri}", exit_code=2) from exc
        raise GraphShowError(f"{action} {uri} 失败：{exc}") from exc


def discover_pages(client: Any, uris: Sequence[str], node_limit: int) -> list[PageSpec]:
    """Authorize every root and return its visible Markdown page specs."""
    if node_limit < 1:
        raise GraphShowError("--node-limit 必须大于 0。", exit_code=2)

    pages: dict[str, PageSpec] = {}
    for raw_uri in uris:
        root = _normalize_input_uri(raw_uri)
        info = _checked_call("访问", root, lambda root=root: client.stat(root))
        root_pages: list[PageSpec] = []

        if _is_directory(info):
            entries = _checked_call(
                "列出",
                root,
                lambda root=root: client.ls(
                    root,
                    recursive=True,
                    output="original",
                    show_all_hidden=False,
                    node_limit=node_limit,
                ),
            )
            if len(entries) >= node_limit:
                raise GraphShowError(
                    f"{root} 的条目数达到 --node-limit={node_limit}，"
                    "为避免生成不完整图谱，请提高该限制。",
                    exit_code=2,
                )
            for entry in entries:
                if not isinstance(entry, Mapping) or _is_directory(entry):
                    continue
                page_uri = _entry_uri(root, entry)
                if page_uri is None or not page_uri.lower().endswith(".md"):
                    continue
                rel_path = entry.get("rel_path")
                if not isinstance(rel_path, str) or not rel_path:
                    rel_path = _relative_uri(page_uri, root)
                root_pages.append(PageSpec(page_uri, root, rel_path.lstrip("/")))
        else:
            if not root.lower().endswith(".md"):
                raise GraphShowError(f"URI 不是 Markdown Wiki 页面：{root}", exit_code=2)
            parent = _join_uri(root, "..")
            root_pages.append(PageSpec(root, parent, posixpath.basename(_uri_payload(root))))

        if not root_pages:
            raise GraphShowError(f"{root} 下没有可读取的 Markdown Wiki 页面。", exit_code=5)
        for spec in root_pages:
            pages.setdefault(spec.uri, spec)

    return sorted(pages.values(), key=lambda page: page.uri)


def _parse_frontmatter(markdown: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(markdown)
    if match is None:
        return {}, markdown
    metadata: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        metadata[key] = value
    return metadata, markdown[match.end() :]


def _page_title(metadata: Mapping[str, str], body: str, relative_path: str) -> str:
    if metadata.get("title"):
        return metadata["title"].strip()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return unquote(posixpath.splitext(posixpath.basename(relative_path))[0])


def _page_category(metadata: Mapping[str, str], relative_path: str) -> str:
    declared = metadata.get("type", "").strip().lower()
    if declared in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[declared]
    if posixpath.basename(relative_path).lower() == "index.md":
        return "index"
    first_part = relative_path.strip("/").split("/", 1)[0].lower()
    return _CATEGORY_ALIASES.get(first_part, "other")


def read_pages(client: Any, specs: Sequence[PageSpec]) -> list[WikiPage]:
    pages: list[WikiPage] = []
    for spec in specs:
        content = _checked_call("读取", spec.uri, lambda spec=spec: client.read(spec.uri))
        if not isinstance(content, str):
            raise GraphShowError(f"读取 {spec.uri} 后未得到文本内容。")
        metadata, body = _parse_frontmatter(content)
        pages.append(
            WikiPage(
                uri=spec.uri,
                root_uri=spec.root_uri,
                relative_path=spec.relative_path,
                title=_page_title(metadata, body, spec.relative_path),
                category=_page_category(metadata, spec.relative_path),
                body=body,
            )
        )
    return pages


def _clean_link_target(target: str) -> str:
    value = html.unescape(target.strip())
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    value = unquote(value)
    return value.split("#", 1)[0].split("?", 1)[0]


def _normalized_page_uri(uri: str) -> str:
    if not uri.startswith("viking://"):
        return uri
    payload = posixpath.normpath(_uri_payload(uri))
    return "viking://" if payload in {"", "."} else f"viking://{payload}"


def _resolve_link(target: str, page: WikiPage, pages: Mapping[str, WikiPage]) -> str | None:
    clean = _clean_link_target(target)
    if not clean.lower().endswith(".md"):
        return None

    candidates: list[str] = []
    if clean.startswith("viking://"):
        candidates.append(_normalized_page_uri(clean))
    elif "://" not in clean:
        current_dir = _join_uri(page.uri, "..")
        candidates.append(_join_uri(current_dir, clean))
        candidates.append(_join_uri(page.root_uri, clean.lstrip("/")))

    for candidate in candidates:
        normalized = _normalized_page_uri(candidate)
        if normalized in pages:
            return normalized

    basename = posixpath.basename(clean)
    same_root = [
        candidate.uri
        for candidate in pages.values()
        if candidate.root_uri == page.root_uri and posixpath.basename(candidate.uri) == basename
    ]
    if len(same_root) == 1:
        return same_root[0]
    global_matches = [
        candidate.uri
        for candidate in pages.values()
        if posixpath.basename(candidate.uri) == basename
    ]
    return global_matches[0] if len(global_matches) == 1 else None


def _plain_label(markdown_label: str) -> str:
    label = re.sub(r"[`*_~]", "", markdown_label)
    return html.unescape(label).strip()


def _render_inline(text: str, page: WikiPage, pages: Mapping[str, WikiPage]) -> str:
    tokens: list[str] = []

    def replace_link(match: re.Match[str]) -> str:
        label, raw_target = match.group(1), match.group(2)
        target = _clean_link_target(raw_target)
        node_uri = _resolve_link(raw_target, page, pages)
        safe_label = html.escape(_plain_label(label))
        if node_uri is not None:
            rendered = (
                f'<a href="#" class="xlink" data-node="{html.escape(node_uri, quote=True)}">'
                f"{safe_label}</a>"
            )
        elif target.startswith("viking://"):
            rendered = (
                f'<span class="src" title="{html.escape(target, quote=True)}">{safe_label}</span>'
            )
        elif target.startswith(("https://", "http://")):
            rendered = (
                f'<a href="{html.escape(target, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{safe_label}</a>'
            )
        else:
            rendered = safe_label
        tokens.append(rendered)
        return f"\x00{len(tokens) - 1}\x00"

    rendered = _LINK_RE.sub(replace_link, text)
    rendered = html.escape(rendered)
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", rendered)
    return re.sub(r"\x00(\d+)\x00", lambda match: tokens[int(match.group(1))], rendered)


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _render_markdown(page: WikiPage, pages: Mapping[str, WikiPage]) -> str:
    """Render the useful OKF Markdown subset while escaping embedded HTML."""
    lines = page.body.splitlines()
    output: list[str] = []
    index = 0

    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue

        fence = _FENCE_RE.match(lines[index])
        if fence:
            language = fence.group(1).strip()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and _FENCE_RE.match(lines[index]) is None:
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            class_name = (
                f' class="language-{html.escape(language, quote=True)}"' if language else ""
            )
            output.append(
                f"<pre><code{class_name}>{html.escape(chr(10).join(code_lines))}</code></pre>"
            )
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            output.append(f"<h{level}>{_render_inline(heading.group(2), page, pages)}</h{level}>")
            index += 1
            continue

        if (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and _is_table_separator(lines[index + 1])
        ):
            headers = [cell.strip() for cell in stripped.strip("|").split("|")]
            output.append("<table><thead><tr>")
            output.extend(f"<th>{_render_inline(cell, page, pages)}</th>" for cell in headers)
            output.append("</tr></thead><tbody>")
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                output.append(
                    "<tr>"
                    + "".join(f"<td>{_render_inline(cell, page, pages)}</td>" for cell in cells)
                    + "</tr>"
                )
                index += 1
            output.append("</tbody></table>")
            continue

        if re.match(r"^[-*+]\s+", stripped):
            output.append("<ul>")
            while index < len(lines) and re.match(r"^[-*+]\s+", lines[index].strip()):
                item = re.sub(r"^[-*+]\s+", "", lines[index].strip())
                output.append(f"<li>{_render_inline(item, page, pages)}</li>")
                index += 1
            output.append("</ul>")
            continue

        if re.match(r"^\d+[.)]\s+", stripped):
            output.append("<ol>")
            while index < len(lines) and re.match(r"^\d+[.)]\s+", lines[index].strip()):
                item = re.sub(r"^\d+[.)]\s+", "", lines[index].strip())
                output.append(f"<li>{_render_inline(item, page, pages)}</li>")
                index += 1
            output.append("</ol>")
            continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip().lstrip(">").strip())
                index += 1
            output.append(
                f"<blockquote>{_render_inline(' '.join(quote_lines), page, pages)}</blockquote>"
            )
            continue

        paragraph = [stripped]
        index += 1
        while index < len(lines):
            next_line = lines[index].strip()
            if not next_line or re.match(r"^(#{1,6})\s+", next_line):
                break
            if _FENCE_RE.match(lines[index]) or re.match(r"^([-*+]\s+|\d+[.)]\s+|>)", next_line):
                break
            if next_line.startswith("|") and index + 1 < len(lines):
                if _is_table_separator(lines[index + 1]):
                    break
            paragraph.append(next_line)
            index += 1
        output.append(f"<p>{_render_inline(' '.join(paragraph), page, pages)}</p>")

    return "\n".join(output)


def _category_sort_key(category: str) -> int:
    try:
        return _CATEGORY_ORDER.index(category)
    except ValueError:
        return len(_CATEGORY_ORDER)


def build_graph(pages: Sequence[WikiPage]) -> dict[str, list[dict[str, Any]]]:
    page_lookup = {page.uri: page for page in pages}
    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for page in pages:
        for match in _LINK_RE.finditer(page.body):
            target = _resolve_link(match.group(2), page, page_lookup)
            if target is None or target == page.uri:
                continue
            key = (page.uri, target)
            if key in seen:
                continue
            seen.add(key)
            links.append(
                {
                    "source": page.uri,
                    "target": target,
                    "label": _plain_label(match.group(1)),
                }
            )

    degree = {page.uri: 0 for page in pages}
    for link in links:
        degree[link["source"]] += 1
        degree[link["target"]] += 1

    nodes: list[dict[str, Any]] = []
    for page in pages:
        category_label, color = _CATEGORY_STYLE[page.category]
        nodes.append(
            {
                "id": page.uri,
                "uri": page.uri,
                "title": page.title,
                "category": page.category,
                "category_label": category_label,
                "color": color,
                "degree": degree[page.uri],
                "html": _render_markdown(page, page_lookup),
            }
        )
    nodes.sort(key=lambda node: (_category_sort_key(node["category"]), node["title"]))
    links.sort(key=lambda link: (link["source"], link["target"], link["label"]))
    return {"nodes": nodes, "links": links}


def _safe_json_for_script(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _default_title(pages: Sequence[WikiPage], root_count: int) -> str:
    if root_count == 1:
        index_pages = [page for page in pages if page.category == "index"]
        if index_pages:
            return index_pages[0].title
    return "LLM Wiki 知识图谱"


def render_html(graph: Mapping[str, Any], title: str) -> str:
    nodes = graph["nodes"]
    links = graph["links"]
    categories = sorted(
        {node["category"] for node in nodes},
        key=_category_sort_key,
    )
    legend = "".join(
        '<span class="lg"><i style="background:{color}"></i>{label}</span>'.format(
            color=_CATEGORY_STYLE[category][1],
            label=html.escape(_CATEGORY_STYLE[category][0]),
        )
        for category in categories
    )
    replacements = {
        "__TITLE__": html.escape(title),
        "__PAGE_COUNT__": str(len(nodes)),
        "__LINK_COUNT__": str(len(links)),
        "__LEGEND__": legend,
        "__DATA_JSON__": _safe_json_for_script(graph),
    }
    document = _HTML_TEMPLATE
    for marker, value in replacements.items():
        document = document.replace(marker, value)
    return document


def _atomic_write(path: Path, content: str) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _client_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    pairs = {
        "url": args.url,
        "api_key": args.api_key,
        "account": args.account,
        "user": args.user,
        "actor_peer_id": args.actor_peer_id,
        "timeout": args.timeout,
    }
    return {key: value for key, value in pairs.items() if value is not None}


def generate(args: argparse.Namespace) -> Path:
    try:
        client = ov.SyncHTTPClient(**_client_kwargs(args))
        client.initialize()
    except Exception as exc:
        raise GraphShowError(f"无法初始化 OpenViking 客户端：{exc}", exit_code=3) from exc

    try:
        if not client.health():
            raise GraphShowError(
                "OpenViking 服务不可用。请确认 OV 服务已启动且 --url/OPENVIKING_URL 正确。",
                exit_code=3,
            )
        specs = discover_pages(client, args.uris, args.node_limit)
        pages = read_pages(client, specs)
    finally:
        client.close()

    title = args.title or _default_title(pages, len(args.uris))
    graph = build_graph(pages)
    output = args.output.expanduser().resolve()
    _atomic_write(output, render_html(graph, title))
    return output


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="鉴权读取一个或多个 LLM Wiki URI，并生成交互式 HTML 知识图谱。"
    )
    parser.add_argument("uris", nargs="+", metavar="VIKING_URI", help="Wiki 页面或目录 URI")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("llm-wiki-graph.html"),
        help="输出 HTML 路径（默认：./llm-wiki-graph.html）",
    )
    parser.add_argument("--title", help="页面标题；默认取单个 Wiki 的 index 标题")
    parser.add_argument("--url", help="OV 服务地址；也可通过 OPENVIKING_URL/ovcli.conf 配置")
    parser.add_argument(
        "--api-key",
        help="OV API Key；更推荐使用 OPENVIKING_API_KEY 或 ovcli.conf",
    )
    parser.add_argument("--account", help="trusted 模式下的 account 身份")
    parser.add_argument("--user", help="trusted 模式下的 user 身份")
    parser.add_argument("--actor-peer-id", help="可选的 actor peer 身份")
    parser.add_argument("--timeout", type=_positive_float, help="HTTP 超时秒数")
    parser.add_argument(
        "--node-limit",
        type=int,
        default=10_000,
        help="每个目录最多枚举的条目数（默认：10000）",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        output = generate(args)
    except GraphShowError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"错误：生成图谱失败：{exc}", file=sys.stderr)
        return 1
    print(f"已生成 LLM Wiki 图谱：{output}")
    return 0


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
  :root {
    color-scheme: light;
    --ink: #0f172a;
    --muted: #64748b;
    --line: #e2e8f0;
    --surface: rgba(255, 255, 255, 0.93);
    --canvas: #f4f6f8;
    --accent: #2563eb;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    color: var(--ink);
    background: var(--canvas);
    font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI",
                 "PingFang SC", "Microsoft YaHei", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  button, input { font: inherit; }
  #app-header {
    position: relative;
    z-index: 10;
    min-height: 72px;
    display: flex;
    align-items: center;
    gap: 22px;
    padding: 12px 22px;
    border-bottom: 1px solid rgba(226, 232, 240, 0.9);
    background: rgba(255, 255, 255, 0.88);
    backdrop-filter: blur(18px);
  }
  .brand { min-width: 0; display: flex; align-items: center; gap: 12px; }
  .brand-mark {
    position: relative;
    flex: 0 0 auto;
    width: 38px;
    height: 38px;
    border-radius: 13px;
    background: linear-gradient(145deg, #1d4ed8, #60a5fa);
    box-shadow: 0 8px 20px rgba(37, 99, 235, 0.22);
  }
  .brand-mark::before, .brand-mark::after {
    content: "";
    position: absolute;
    border-radius: 50%;
    background: #fff;
    box-shadow: 10px 8px 0 -1px rgba(255, 255, 255, 0.78);
  }
  .brand-mark::before { width: 7px; height: 7px; top: 9px; left: 9px; }
  .brand-mark::after { width: 5px; height: 5px; right: 8px; bottom: 8px; opacity: .72; }
  .brand-copy { min-width: 0; }
  .eyebrow {
    display: block;
    margin-bottom: 3px;
    color: #94a3b8;
    font-size: 9px;
    font-weight: 750;
    letter-spacing: .16em;
  }
  #app-header h1 {
    max-width: 460px;
    margin: 0;
    overflow: hidden;
    color: #172033;
    font-size: 16px;
    font-weight: 700;
    line-height: 1.25;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .header-meta {
    min-width: 0;
    margin-left: auto;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 16px;
  }
  .meta {
    flex: 0 0 auto;
    padding: 7px 10px;
    border: 1px solid #e8edf3;
    border-radius: 999px;
    color: #64748b;
    background: #f8fafc;
    font-size: 11px;
    font-weight: 600;
  }
  .legend { display: flex; align-items: center; flex-wrap: wrap; gap: 7px 13px; }
  .lg { display: inline-flex; align-items: center; gap: 5px; color: #64748b; font-size: 11px; }
  .lg i { width: 7px; height: 7px; border-radius: 50%; }
  #wrap { min-height: 0; flex: 1; display: flex; gap: 14px; padding: 14px; }
  .surface {
    overflow: hidden;
    border: 1px solid rgba(226, 232, 240, .88);
    border-radius: 18px;
    background: var(--surface);
    box-shadow: 0 8px 30px rgba(15, 23, 42, .055);
  }
  #graph-pane { position: relative; flex: 1 1 58%; min-width: 0; }
  #graph {
    display: block;
    width: 100%;
    height: 100%;
    background:
      radial-gradient(circle at 18% 12%, rgba(59, 130, 246, .07), transparent 34%),
      radial-gradient(circle at 88% 84%, rgba(16, 185, 129, .055), transparent 32%),
      #fbfcfe;
  }
  #graph-toolbar {
    position: absolute;
    z-index: 4;
    top: 14px;
    left: 14px;
    right: 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    pointer-events: none;
  }
  #node-search, #neighbor-controls {
    pointer-events: auto;
    border: 1px solid rgba(226, 232, 240, .95);
    background: rgba(255, 255, 255, .9);
    box-shadow: 0 7px 20px rgba(15, 23, 42, .07);
    backdrop-filter: blur(12px);
  }
  #node-search {
    width: min(250px, 36%);
    padding: 9px 12px;
    border-radius: 11px;
    color: #334155;
    font-size: 12px;
    outline: none;
  }
  #node-search:focus { border-color: #93c5fd; box-shadow: 0 0 0 3px rgba(59, 130, 246, .1); }
  #neighbor-controls {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 8px 7px 11px;
    border-radius: 12px;
  }
  #neighbor-status { color: #64748b; font-size: 11px; white-space: nowrap; }
  #expand-neighbors {
    padding: 6px 9px;
    border: 0;
    border-radius: 8px;
    color: #1d4ed8;
    background: #eff6ff;
    font-size: 11px;
    font-weight: 650;
    cursor: pointer;
  }
  #expand-neighbors:disabled { color: #94a3b8; background: #f1f5f9; cursor: default; }
  .edge { stroke: #cbd5e1; stroke-width: 1.05; stroke-opacity: .76; transition: opacity .18s; }
  .node { cursor: pointer; outline: none; pointer-events: bounding-box; transition: opacity .18s; }
  .node circle { stroke: #fff; stroke-width: 2; transition: r .15s, opacity .18s, stroke .15s; }
  .node:hover circle, .node:focus circle { stroke: #1d4ed8; stroke-width: 3; }
  .node circle.sel { stroke: #0f172a; stroke-width: 3; }
  .nodelabel {
    fill: #475569;
    font-size: 10.5px;
    font-weight: 600;
    paint-order: stroke;
    stroke: rgba(255, 255, 255, .96);
    stroke-width: 3px;
    stroke-linejoin: round;
    pointer-events: auto;
  }
  .dimmed { opacity: .1; }
  #panel {
    flex: 0 1 42%;
    min-width: 330px;
    padding: 28px 30px 46px;
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  #hint { margin-top: 38%; color: #94a3b8; text-align: center; font-size: 13px; line-height: 1.8; }
  .article-meta { display: flex; align-items: center; gap: 7px; color: #64748b; font-size: 11px; }
  .article-meta i { width: 8px; height: 8px; border-radius: 50%; }
  .page-uri {
    display: block;
    margin-top: 8px;
    overflow-wrap: anywhere;
    color: #94a3b8;
    font: 10px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  #panel h1 { margin: 18px 0 18px; color: #172033; font-size: 25px; line-height: 1.28; }
  #panel h2 { margin: 30px 0 11px; color: #1e293b; font-size: 17px; }
  #panel h3 { margin: 23px 0 8px; color: #334155; font-size: 14px; }
  #panel h4, #panel h5, #panel h6 { margin: 18px 0 7px; color: #475569; font-size: 13px; }
  #panel p, #panel li, #panel td, #panel th { font-size: 13px; line-height: 1.78; }
  #panel p { margin: 9px 0; color: #475569; }
  #panel ul, #panel ol { margin: 9px 0; padding-left: 22px; color: #475569; }
  #panel li + li { margin-top: 5px; }
  #panel a { color: #2563eb; text-decoration: none; border-bottom: 1px solid rgba(37, 99, 235, .22); }
  #panel a:hover { border-bottom-color: #2563eb; }
  #panel .src {
    padding: 1px 5px;
    border-radius: 5px;
    color: #64748b;
    background: #f1f5f9;
  }
  #panel code {
    padding: 2px 5px;
    border-radius: 5px;
    color: #be123c;
    background: #fff1f2;
    font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  #panel pre { overflow-x: auto; padding: 14px; border-radius: 10px; background: #0f172a; }
  #panel pre code { padding: 0; color: #e2e8f0; background: transparent; }
  #panel blockquote { margin: 14px 0; padding: 1px 14px; border-left: 3px solid #bfdbfe; color: #64748b; }
  #panel table { width: 100%; margin: 14px 0; border-collapse: collapse; }
  #panel th, #panel td { padding: 8px 9px; border: 1px solid #e2e8f0; text-align: left; vertical-align: top; }
  #panel th { color: #334155; background: #f8fafc; }
  #d3-error {
    display: none;
    position: absolute;
    inset: 0;
    place-items: center;
    padding: 28px;
    color: #b91c1c;
    background: #fff;
    text-align: center;
    line-height: 1.7;
  }
  @media (max-width: 850px) {
    #app-header { min-height: auto; align-items: flex-start; padding: 10px 14px; }
    .header-meta { gap: 7px; }
    .legend { display: none; }
    #wrap { flex-direction: column; gap: 8px; padding: 8px; overflow-y: auto; }
    #graph-pane { flex: 0 0 52vh; min-height: 360px; }
    #panel { flex: none; min-width: 0; min-height: 45vh; overflow: visible; padding: 22px 20px 36px; }
    #node-search { width: 42%; }
  }
</style>
</head>
<body>
<header id="app-header">
  <div class="brand">
    <span class="brand-mark" aria-hidden="true"></span>
    <div class="brand-copy">
      <span class="eyebrow">OPENVIKING LLM WIKI</span>
      <h1>__TITLE__</h1>
    </div>
  </div>
  <div class="header-meta">
    <span class="meta">__PAGE_COUNT__ 个节点 · __LINK_COUNT__ 条关系</span>
    <span class="legend">__LEGEND__</span>
  </div>
</header>
<div id="wrap">
  <div id="graph-pane" class="surface">
    <div id="graph-toolbar">
      <input id="node-search" type="search" placeholder="搜索 Wiki 页面" aria-label="搜索 Wiki 页面">
      <div id="neighbor-controls">
        <span id="neighbor-status" aria-live="polite">默认高亮 1 跳邻居</span>
        <button id="expand-neighbors" type="button">拓展到 2 跳</button>
      </div>
    </div>
    <svg id="graph" role="img" aria-label="LLM Wiki 知识图谱"></svg>
    <div id="d3-error">无法加载 D3 图形库。请检查网络连接后重新打开此文件。</div>
  </div>
  <article id="panel" class="surface">
    <p id="hint">点击图谱节点浏览 Wiki 页面。拖动节点可调整布局，滚轮可缩放画布。</p>
  </article>
</div>
<script>
const DATA = __DATA_JSON__;

if (!window.d3) {
  document.getElementById("d3-error").style.display = "grid";
} else {
  const svg = d3.select("#graph");
  let width = svg.node().clientWidth;
  let height = svg.node().clientHeight;
  svg.attr("viewBox", [0, 0, width, height]);
  const graphLayer = svg.append("g");
  svg.call(d3.zoom().scaleExtent([0.25, 4]).on("zoom", event => {
    graphLayer.attr("transform", event.transform);
  }));

  const nodeById = new Map(DATA.nodes.map(item => [item.id, item]));
  const adjacency = new Map(DATA.nodes.map(item => [item.id, new Set()]));
  DATA.links.forEach(link => {
    adjacency.get(link.source)?.add(link.target);
    adjacency.get(link.target)?.add(link.source);
  });
  let selectedId = null;
  let hopLimit = 1;

  function radiusFor(item) {
    return 7 + Math.sqrt(Math.max(item.degree, 1)) * 2;
  }

  function displayTitle(title) {
    const chars = Array.from(title);
    return chars.length > 9 ? chars.slice(0, 8).join("") + "…" : title;
  }

  function escapeHTML(value) {
    return String(value).replace(/[&<>"']/g, character => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
    })[character]);
  }

  const simulation = d3.forceSimulation(DATA.nodes)
    .force("link", d3.forceLink(DATA.links).id(item => item.id).distance(130).strength(.35))
    .force("charge", d3.forceManyBody().strength(-480))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide().radius(item => radiusFor(item) + 20).iterations(2));

  const edge = graphLayer.append("g").selectAll("line")
    .data(DATA.links).join("line").attr("class", "edge");
  edge.append("title").text(item => item.label || "关联");

  const node = graphLayer.append("g").selectAll("g")
    .data(DATA.nodes).join("g")
    .attr("class", "node")
    .attr("tabindex", 0)
    .attr("role", "button")
    .attr("aria-label", item => item.title)
    .on("click", (event, item) => select(item.id))
    .call(drag(simulation));

  node.append("circle")
    .attr("r", radiusFor)
    .attr("fill", item => item.color);

  node.on("keydown", (event, item) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select(item.id);
    }
  });

  node.append("text")
    .attr("class", "nodelabel")
    .attr("x", item => radiusFor(item) + 5)
    .attr("y", 4)
    .text(item => displayTitle(item.title));
  node.append("title").text(item => item.title);

  simulation.on("tick", () => {
    edge.attr("x1", item => item.source.x).attr("y1", item => item.source.y)
      .attr("x2", item => item.target.x).attr("y2", item => item.target.y);
    node.attr("transform", item => "translate(" + item.x + "," + item.y + ")");
  });

  function endpointId(endpoint) {
    return typeof endpoint === "object" ? endpoint.id : endpoint;
  }

  function nodesWithinHops(startId, maxHops) {
    const distances = new Map([[startId, 0]]);
    const queue = [startId];
    for (let index = 0; index < queue.length; index += 1) {
      const current = queue[index];
      const distance = distances.get(current);
      if (distance >= maxHops) continue;
      if (current !== startId && nodeById.get(current)?.category === "index") continue;
      for (const neighbor of adjacency.get(current) || []) {
        if (distances.has(neighbor)) continue;
        distances.set(neighbor, distance + 1);
        queue.push(neighbor);
      }
    }
    return new Set(distances.keys());
  }

  function updateNeighborhood() {
    if (!selectedId) return;
    const highlighted = nodesWithinHops(selectedId, hopLimit);
    const nextHighlighted = nodesWithinHops(selectedId, hopLimit + 1);
    node.classed("dimmed", item => !highlighted.has(item.id));
    edge.classed("dimmed", item =>
      !highlighted.has(endpointId(item.source)) || !highlighted.has(endpointId(item.target))
    );
    document.getElementById("neighbor-status").textContent =
      hopLimit + " 跳内高亮 · " + highlighted.size + " 个节点";
    const button = document.getElementById("expand-neighbors");
    const canExpand = nextHighlighted.size > highlighted.size;
    button.disabled = !canExpand;
    button.textContent = canExpand ? "拓展到 " + (hopLimit + 1) + " 跳" : "已展示全部邻居";
  }

  function select(id) {
    const item = nodeById.get(id);
    if (!item) return;
    selectedId = id;
    hopLimit = 1;
    node.selectAll("circle").classed("sel", candidate => candidate.id === id);
    updateNeighborhood();
    document.getElementById("panel").innerHTML =
      '<div class="article-meta"><i style="background:' + item.color + '"></i>' +
      escapeHTML(item.category_label) + '</div><span class="page-uri">' +
      escapeHTML(item.uri) + "</span>" + item.html;
    document.querySelectorAll("#panel a.xlink").forEach(anchor => {
      anchor.addEventListener("click", event => {
        event.preventDefault();
        select(anchor.dataset.node);
      });
    });
    document.getElementById("panel").scrollTop = 0;
  }

  document.getElementById("expand-neighbors").addEventListener("click", () => {
    if (!selectedId) return;
    hopLimit += 1;
    updateNeighborhood();
  });

  const search = document.getElementById("node-search");
  search.addEventListener("input", () => {
    const query = search.value.trim().toLocaleLowerCase();
    if (!query) {
      if (selectedId) updateNeighborhood();
      else {
        node.classed("dimmed", false);
        edge.classed("dimmed", false);
      }
      return;
    }
    const matches = new Set(DATA.nodes.filter(item =>
      item.title.toLocaleLowerCase().includes(query) || item.uri.toLocaleLowerCase().includes(query)
    ).map(item => item.id));
    node.classed("dimmed", item => !matches.has(item.id));
    edge.classed("dimmed", true);
  });
  search.addEventListener("keydown", event => {
    if (event.key !== "Enter") return;
    const query = search.value.trim().toLocaleLowerCase();
    const match = DATA.nodes.find(item =>
      item.title.toLocaleLowerCase().includes(query) || item.uri.toLocaleLowerCase().includes(query)
    );
    if (match) select(match.id);
  });

  function drag(activeSimulation) {
    return d3.drag()
      .on("start", (event, item) => {
        if (!event.active) activeSimulation.alphaTarget(.3).restart();
        item.fx = item.x;
        item.fy = item.y;
      })
      .on("drag", (event, item) => {
        item.fx = event.x;
        item.fy = event.y;
      })
      .on("end", (event, item) => {
        if (!event.active) activeSimulation.alphaTarget(0);
        item.fx = null;
        item.fy = null;
      });
  }

  window.addEventListener("resize", () => {
    width = svg.node().clientWidth;
    height = svg.node().clientHeight;
    svg.attr("viewBox", [0, 0, width, height]);
    simulation.force("center", d3.forceCenter(width / 2, height / 2)).alpha(.3).restart();
  });

  const start = DATA.nodes.find(item => item.category === "index") ||
    DATA.nodes.slice().sort((left, right) => right.degree - left.degree)[0];
  if (start) select(start.id);
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
