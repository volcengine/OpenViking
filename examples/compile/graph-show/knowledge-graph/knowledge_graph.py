#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Render an OpenViking Knowledge Graph artifact directory as interactive HTML.

Expected input layout:

    knowledge-graph/
      entities/
        <entity-id>.md
      relations.jsonl

The script validates the graph before writing anything: ``relations.jsonl``
must exist and contain valid JSON objects, every entity file must have a stable
``id`` and ``title``, and every relation endpoint must resolve to an entity.
The generated HTML embeds all graph data and entity content; D3 is loaded from
its public CDN when the page opens.

Example:
    python knowledge_graph.py /path/to/journal-to-the-west-knowledge-graph
    python knowledge_graph.py /path/to/graph -o graph.html --title "西游知识图谱"
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)
_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(\s*(<[^>]+>|[^)\s]+)(?:\s+[\"'][^)]*[\"'])?\s*\)")
_FENCE_RE = re.compile(r"^\s*```([^`]*)$")
_RELATION_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_TYPE_ALIASES = {
    "human": "person",
    "character": "person",
    "people": "person",
    "location": "place",
    "region": "place",
    "site": "place",
    "item": "artifact",
    "object": "artifact",
    "tool": "artifact",
    "weapon": "artifact",
    "contract": "document",
    "book": "document",
    "record": "document",
    "org": "organization",
    "team": "group",
    "collective": "group",
}

_TYPE_STYLE = {
    "person": {"label": "人物", "color": "#39f7ff", "symbol": "circle"},
    "animal": {"label": "生灵", "color": "#6df7b1", "symbol": "triangle"},
    "place": {"label": "地点", "color": "#ff4fd8", "symbol": "diamond"},
    "artifact": {"label": "器物", "color": "#ffbd59", "symbol": "star"},
    "document": {"label": "文书", "color": "#a88bff", "symbol": "square"},
    "organization": {"label": "组织", "color": "#4f8cff", "symbol": "hexagon"},
    "group": {"label": "群体", "color": "#35df83", "symbol": "hexagon"},
    "event": {"label": "事件", "color": "#ff6b7f", "symbol": "triangle"},
    "product": {"label": "产品", "color": "#ff8f3d", "symbol": "square"},
    "project": {"label": "项目", "color": "#ff8f3d", "symbol": "square"},
    "system": {"label": "系统", "color": "#2dc8ff", "symbol": "hexagon"},
    "service": {"label": "服务", "color": "#2dc8ff", "symbol": "hexagon"},
    "dataset": {"label": "数据集", "color": "#82aaff", "symbol": "square"},
    "standard": {"label": "标准", "color": "#c792ea", "symbol": "diamond"},
    "other": {"label": "其他", "color": "#9aa8c7", "symbol": "circle"},
}


class KnowledgeGraphError(RuntimeError):
    """A validation or input failure with a process exit code."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class Entity:
    id: str
    title: str
    entity_type: str
    description: str
    aliases: tuple[str, ...]
    sources: tuple[str, ...]
    body: str
    relative_path: str


@dataclass(frozen=True)
class Relation:
    source: str
    target: str
    relation: str
    label: str
    evidence: tuple[str, ...]
    line_number: int


def _parse_inline_list(value: str) -> list[str]:
    stripped = value.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return []
    content = stripped[1:-1].strip()
    if not content:
        return []
    values: list[str] = []
    for part in content.split(","):
        item = part.strip()
        if len(item) >= 2 and item[0] == item[-1] and item[0] in {'"', "'"}:
            item = item[1:-1]
        if item:
            values.append(item)
    return values


def _parse_frontmatter(markdown: str, path: Path) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(markdown)
    if match is None:
        raise KnowledgeGraphError(f"实体文件缺少 YAML frontmatter：{path}", exit_code=3)

    metadata: dict[str, Any] = {}
    active_list: str | None = None
    for raw_line in match.group(1).splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw_line[:1].isspace() and stripped.startswith("-") and active_list:
            item = stripped[1:].strip()
            if len(item) >= 2 and item[0] == item[-1] and item[0] in {'"', "'"}:
                item = item[1:-1]
            if item:
                metadata.setdefault(active_list, []).append(item)
            continue
        if ":" not in raw_line:
            active_list = None
            continue
        key, raw_value = raw_line.split(":", 1)
        key = key.strip().lower()
        value = raw_value.strip()
        if not value:
            metadata[key] = []
            active_list = key
            continue
        active_list = None
        inline_list = _parse_inline_list(value)
        if value.startswith("[") and value.endswith("]"):
            metadata[key] = inline_list
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        metadata[key] = value
    return metadata, markdown[match.end() :]


def _as_string(metadata: Mapping[str, Any], key: str) -> str:
    value = metadata.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _as_string_list(metadata: Mapping[str, Any], *keys: str) -> tuple[str, ...]:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, list):
            return tuple(str(item).strip() for item in value if str(item).strip())
        if isinstance(value, str) and value.strip():
            return (value.strip(),)
    return ()


def _normalize_entity_type(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    normalized = _TYPE_ALIASES.get(normalized, normalized)
    return normalized if normalized in _TYPE_STYLE else "other"


def load_entities(root: Path) -> dict[str, Entity]:
    entities_dir = root / "entities"
    if not entities_dir.is_dir():
        raise KnowledgeGraphError(f"缺少实体目录：{entities_dir}", exit_code=2)

    paths = sorted(
        path
        for path in entities_dir.glob("*.md")
        if path.is_file() and not path.name.startswith(".")
    )
    if not paths:
        raise KnowledgeGraphError(f"实体目录中没有 Markdown 文件：{entities_dir}", exit_code=2)

    entities: dict[str, Entity] = {}
    for path in paths:
        try:
            markdown = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise KnowledgeGraphError(f"无法读取实体文件 {path}：{exc}", exit_code=3) from exc
        metadata, body = _parse_frontmatter(markdown, path)
        entity_id = _as_string(metadata, "id")
        title = _as_string(metadata, "title")
        if not entity_id:
            raise KnowledgeGraphError(f"实体缺少非空 id：{path}", exit_code=3)
        if not title:
            raise KnowledgeGraphError(f"实体缺少非空 title：{path}", exit_code=3)
        if entity_id != path.stem:
            raise KnowledgeGraphError(
                f"实体 id 与文件名不一致：{path.name} 中 id={entity_id!r}", exit_code=3
            )
        if entity_id in entities:
            raise KnowledgeGraphError(f"实体 id 重复：{entity_id}", exit_code=3)
        declared_type = (
            _as_string(metadata, "entity_type")
            or _as_string(metadata, "kind")
            or _as_string(metadata, "category")
        )
        entities[entity_id] = Entity(
            id=entity_id,
            title=title,
            entity_type=_normalize_entity_type(declared_type),
            description=_as_string(metadata, "description"),
            aliases=_as_string_list(metadata, "aliases", "alias"),
            sources=_as_string_list(metadata, "sources", "source"),
            body=body.strip(),
            relative_path=f"entities/{path.name}",
        )
    return entities


def _required_relation_string(data: Mapping[str, Any], key: str, line_number: int) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeGraphError(
            f"relations.jsonl 第 {line_number} 行缺少非空字符串字段 {key!r}", exit_code=3
        )
    return value.strip()


def load_relations(root: Path, entities: Mapping[str, Entity]) -> list[Relation]:
    relations_path = root / "relations.jsonl"
    if not relations_path.is_file():
        raise KnowledgeGraphError(
            f"目录中没有 relations.jsonl：{relations_path}\n"
            "请传入 Knowledge Graph 的产物目录，而不是 knowledge-graph 编译技能目录。",
            exit_code=2,
        )
    try:
        lines = relations_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise KnowledgeGraphError(f"无法读取 {relations_path}：{exc}", exit_code=3) from exc

    relations: list[Relation] = []
    labels_by_predicate: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            data = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise KnowledgeGraphError(
                f"relations.jsonl 第 {line_number} 行不是有效 JSON：{exc.msg}", exit_code=3
            ) from exc
        if not isinstance(data, dict):
            raise KnowledgeGraphError(
                f"relations.jsonl 第 {line_number} 行必须是 JSON 对象", exit_code=3
            )
        source = _required_relation_string(data, "from", line_number)
        target = _required_relation_string(data, "to", line_number)
        predicate = _required_relation_string(data, "relation", line_number)
        label = data.get("label", predicate)
        if not isinstance(label, str) or not label.strip():
            raise KnowledgeGraphError(
                f"relations.jsonl 第 {line_number} 行的 label 必须是非空字符串",
                exit_code=3,
            )
        label = label.strip()
        if not _RELATION_RE.fullmatch(predicate):
            raise KnowledgeGraphError(
                f"relations.jsonl 第 {line_number} 行的 relation 必须是 snake_case：{predicate}",
                exit_code=3,
            )
        if source not in entities:
            raise KnowledgeGraphError(
                f"relations.jsonl 第 {line_number} 行引用了不存在的起点实体：{source}",
                exit_code=3,
            )
        if target not in entities:
            raise KnowledgeGraphError(
                f"relations.jsonl 第 {line_number} 行引用了不存在的终点实体：{target}",
                exit_code=3,
            )
        evidence_value = data.get("evidence")
        if not isinstance(evidence_value, list) or not evidence_value:
            raise KnowledgeGraphError(
                f"relations.jsonl 第 {line_number} 行的 evidence 必须是非空数组",
                exit_code=3,
            )
        evidence = tuple(
            item.strip() for item in evidence_value if isinstance(item, str) and item.strip()
        )
        if not evidence or len(evidence) != len(evidence_value):
            raise KnowledgeGraphError(
                f"relations.jsonl 第 {line_number} 行的 evidence 只能包含非空字符串",
                exit_code=3,
            )
        previous_label = labels_by_predicate.setdefault(predicate, label)
        if previous_label != label:
            raise KnowledgeGraphError(
                f"关系 {predicate!r} 使用了不一致的 label：{previous_label!r} / {label!r}",
                exit_code=3,
            )
        relations.append(
            Relation(source, target, predicate, label, evidence, line_number=line_number)
        )

    if not relations:
        raise KnowledgeGraphError(f"{relations_path} 中没有关系记录", exit_code=3)

    merged: dict[tuple[str, str, str], Relation] = {}
    for relation in relations:
        key = (relation.source, relation.relation, relation.target)
        existing = merged.get(key)
        if existing is None:
            merged[key] = relation
            continue
        evidence = tuple(dict.fromkeys((*existing.evidence, *relation.evidence)))
        merged[key] = Relation(
            existing.source,
            existing.target,
            existing.relation,
            existing.label,
            evidence,
            line_number=existing.line_number,
        )
    return sorted(merged.values(), key=lambda item: (item.source, item.relation, item.target))


def _clean_link_target(target: str) -> str:
    value = html.unescape(target.strip())
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    return value.split("#", 1)[0].split("?", 1)[0]


def _plain_label(markdown_label: str) -> str:
    return html.unescape(re.sub(r"[`*_~]", "", markdown_label)).strip()


def _render_inline(text: str, entity_ids: set[str]) -> str:
    tokens: list[str] = []

    def replace_link(match: re.Match[str]) -> str:
        label = html.escape(_plain_label(match.group(1)))
        target = _clean_link_target(match.group(2))
        target_id = Path(target).stem if target.lower().endswith(".md") else ""
        if target_id in entity_ids:
            rendered = (
                f'<button class="entity-jump" type="button" '
                f'data-node="{html.escape(target_id, quote=True)}">{label}</button>'
            )
        elif target.startswith(("viking://", "http://", "https://")):
            rendered = (
                f'<span class="source-ref" title="{html.escape(target, quote=True)}">{label}</span>'
            )
        else:
            rendered = label
        tokens.append(rendered)
        return f"\x00{len(tokens) - 1}\x00"

    rendered = _LINK_RE.sub(replace_link, text)
    rendered = html.escape(rendered)
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", rendered)
    return re.sub(r"\x00(\d+)\x00", lambda match: tokens[int(match.group(1))], rendered)


def _render_markdown(markdown: str, entity_ids: set[str]) -> str:
    lines = markdown.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        fence = _FENCE_RE.match(lines[index])
        if fence:
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and _FENCE_RE.match(lines[index]) is None:
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)) + 1, 6)
            output.append(f"<h{level}>{_render_inline(heading.group(2), entity_ids)}</h{level}>")
            index += 1
            continue
        if re.match(r"^[-*+]\s+", stripped):
            output.append("<ul>")
            while index < len(lines) and re.match(r"^[-*+]\s+", lines[index].strip()):
                item = re.sub(r"^[-*+]\s+", "", lines[index].strip())
                output.append(f"<li>{_render_inline(item, entity_ids)}</li>")
                index += 1
            output.append("</ul>")
            continue
        paragraph = [stripped]
        index += 1
        while index < len(lines):
            next_line = lines[index].strip()
            if not next_line or re.match(r"^(#{1,6})\s+", next_line):
                break
            if _FENCE_RE.match(lines[index]) or re.match(r"^[-*+]\s+", next_line):
                break
            paragraph.append(next_line)
            index += 1
        output.append(f"<p>{_render_inline(' '.join(paragraph), entity_ids)}</p>")
    return "\n".join(output)


def build_graph(entities: Mapping[str, Entity], relations: Sequence[Relation]) -> dict[str, Any]:
    degree = Counter[str]()
    relation_counts = Counter[str]()
    for relation in relations:
        degree[relation.source] += 1
        degree[relation.target] += 1
        relation_counts[relation.relation] += 1

    entity_ids = set(entities)
    nodes: list[dict[str, Any]] = []
    for entity in entities.values():
        style = _TYPE_STYLE[entity.entity_type]
        nodes.append(
            {
                "id": entity.id,
                "title": entity.title,
                "entity_type": entity.entity_type,
                "type_label": style["label"],
                "color": style["color"],
                "symbol": style["symbol"],
                "description": entity.description,
                "aliases": list(entity.aliases),
                "sources": list(entity.sources),
                "body_html": _render_markdown(entity.body, entity_ids),
                "path": entity.relative_path,
                "degree": degree[entity.id],
            }
        )
    nodes.sort(key=lambda item: (-item["degree"], item["title"]))

    links = [
        {
            "source": relation.source,
            "target": relation.target,
            "relation": relation.relation,
            "label": relation.label,
            "evidence": list(relation.evidence),
        }
        for relation in relations
    ]
    return {
        "nodes": nodes,
        "links": links,
        "relation_counts": [
            {
                "relation": predicate,
                "label": next(
                    relation.label for relation in relations if relation.relation == predicate
                ),
                "count": count,
            }
            for predicate, count in relation_counts.most_common()
        ],
    }


def _safe_json_for_script(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _type_filters(graph: Mapping[str, Any]) -> str:
    counts = Counter(node["entity_type"] for node in graph["nodes"])
    ordered = sorted(counts, key=lambda key: (-counts[key], _TYPE_STYLE[key]["label"]))
    parts = [
        '<button class="type-filter active" type="button" data-type="all">'
        f'<span class="type-dot spectrum"></span><span>全部实体</span><b>{len(graph["nodes"])}</b>'
        "</button>"
    ]
    for entity_type in ordered:
        style = _TYPE_STYLE[entity_type]
        parts.append(
            f'<button class="type-filter" type="button" data-type="{entity_type}">'
            f'<span class="type-dot" style="--type-color:{style["color"]}"></span>'
            f"<span>{html.escape(style['label'])}</span><b>{counts[entity_type]}</b></button>"
        )
    return "".join(parts)


def _relation_legend(graph: Mapping[str, Any]) -> str:
    return "".join(
        '<div class="predicate-row"><span>{label}</span><code>{predicate}</code><b>{count}</b></div>'.format(
            label=html.escape(item["label"]),
            predicate=html.escape(item["relation"]),
            count=item["count"],
        )
        for item in graph["relation_counts"]
    )


def render_html(graph: Mapping[str, Any], title: str, source_name: str) -> str:
    replacements = {
        "__TITLE__": html.escape(title),
        "__SOURCE_NAME__": html.escape(source_name),
        "__NODE_COUNT__": str(len(graph["nodes"])),
        "__LINK_COUNT__": str(len(graph["links"])),
        "__TYPE_COUNT__": str(len({node["entity_type"] for node in graph["nodes"]})),
        "__TYPE_FILTERS__": _type_filters(graph),
        "__RELATION_LEGEND__": _relation_legend(graph),
        "__DATA_JSON__": _safe_json_for_script(graph),
    }
    document = _HTML_TEMPLATE
    for marker, value in replacements.items():
        document = document.replace(marker, value)
    return document


def _atomic_write(path: Path, content: str) -> None:
    output = path.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temp_name, output)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def generate(input_path: Path, output_path: Path, title: str | None = None) -> Path:
    root = input_path.expanduser().resolve()
    if not root.is_dir():
        raise KnowledgeGraphError(f"输入路径不是目录：{root}", exit_code=2)
    relations_path = root / "relations.jsonl"
    if not relations_path.is_file():
        raise KnowledgeGraphError(
            f"目录中没有 relations.jsonl：{relations_path}\n"
            "请传入 Knowledge Graph 的产物目录，而不是 knowledge-graph 编译技能目录。",
            exit_code=2,
        )
    entities = load_entities(root)
    relations = load_relations(root, entities)
    graph = build_graph(entities, relations)
    display_title = title or root.name.replace("-", " ").replace("_", " ")
    output = output_path.expanduser().resolve()
    _atomic_write(output, render_html(graph, display_title, root.name))
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="校验 OpenViking Knowledge Graph 产物并生成酷炫的交互式 HTML。"
    )
    parser.add_argument("path", type=Path, help="包含 entities/ 和 relations.jsonl 的本地目录")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("knowledge-graph.html"),
        help="输出 HTML 路径（默认：./knowledge-graph.html）",
    )
    parser.add_argument("--title", help="页面标题；默认使用输入目录名")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        output = generate(args.path, args.output, args.title)
    except KnowledgeGraphError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"错误：生成知识图谱失败：{exc}", file=sys.stderr)
        return 1
    print(f"已生成 Knowledge Graph：{output}")
    return 0


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · KG Explorer</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
  :root {
    color-scheme: dark;
    --void: #050813;
    --panel: rgba(11, 17, 34, .76);
    --panel-solid: #0b1122;
    --line: rgba(128, 157, 219, .17);
    --muted: #8390ad;
    --text: #edf5ff;
    --cyan: #39f7ff;
    --violet: #a88bff;
  }
  * { box-sizing: border-box; }
  html, body { width: 100%; height: 100%; }
  body {
    margin: 0;
    overflow: hidden;
    color: var(--text);
    background:
      radial-gradient(circle at 46% 42%, rgba(29, 78, 216, .16), transparent 31%),
      radial-gradient(circle at 80% 8%, rgba(168, 85, 247, .13), transparent 28%),
      radial-gradient(circle at 8% 88%, rgba(6, 182, 212, .11), transparent 26%),
      var(--void);
    font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI",
                 "PingFang SC", "Microsoft YaHei", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  body::before {
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    opacity: .33;
    background-image:
      linear-gradient(rgba(95, 129, 191, .065) 1px, transparent 1px),
      linear-gradient(90deg, rgba(95, 129, 191, .065) 1px, transparent 1px);
    background-size: 44px 44px;
    mask-image: radial-gradient(circle at center, #000 28%, transparent 90%);
  }
  button, input { font: inherit; }
  button { color: inherit; }
  .app {
    position: relative;
    z-index: 1;
    height: 100%;
    display: grid;
    grid-template-rows: 76px minmax(0, 1fr);
  }
  .topbar {
    display: flex;
    align-items: center;
    gap: 22px;
    padding: 0 24px;
    border-bottom: 1px solid var(--line);
    background: rgba(5, 8, 19, .72);
    backdrop-filter: blur(20px);
  }
  .brand-orbit {
    position: relative;
    width: 42px;
    height: 42px;
    flex: 0 0 auto;
    border: 1px solid rgba(57, 247, 255, .48);
    border-radius: 50%;
    box-shadow: inset 0 0 22px rgba(57, 247, 255, .16), 0 0 24px rgba(57, 247, 255, .1);
  }
  .brand-orbit::before {
    content: "";
    position: absolute;
    inset: 8px;
    border-radius: 50%;
    background: linear-gradient(145deg, var(--cyan), #5473ff 62%, #da4fff);
    box-shadow: 0 0 18px rgba(57, 247, 255, .48);
  }
  .brand-orbit::after {
    content: "";
    position: absolute;
    width: 7px;
    height: 7px;
    top: 1px;
    left: 17px;
    border-radius: 50%;
    background: #fff;
    box-shadow: 0 0 12px var(--cyan);
  }
  .brand-copy { min-width: 0; }
  .kicker { color: var(--cyan); font: 700 9px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .24em; }
  .brand-copy h1 {
    max-width: 520px;
    margin: 5px 0 0;
    overflow: hidden;
    font-size: 17px;
    line-height: 1.2;
    letter-spacing: .02em;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .source-tag {
    max-width: 280px;
    margin-left: 6px;
    overflow: hidden;
    color: #657391;
    font: 10px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .telemetry { margin-left: auto; display: flex; align-items: stretch; gap: 8px; }
  .stat {
    min-width: 76px;
    padding: 8px 11px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: rgba(15, 23, 42, .5);
  }
  .stat b { display: block; color: #fff; font: 700 15px/1 ui-monospace, SFMono-Regular, Menlo, monospace; }
  .stat span { display: block; margin-top: 5px; color: #687796; font-size: 8px; font-weight: 700; letter-spacing: .13em; }
  .workspace {
    min-height: 0;
    display: grid;
    grid-template-columns: 238px minmax(420px, 1fr) 390px;
    gap: 1px;
    background: var(--line);
  }
  .rail, .inspector, .graph-shell { min-height: 0; background: rgba(5, 8, 19, .88); }
  .rail, .inspector { overflow-y: auto; scrollbar-color: #243252 transparent; scrollbar-width: thin; }
  .rail { padding: 20px 16px 28px; }
  .section-label {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0 5px 12px;
    color: #5f6d8a;
    font: 750 9px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace;
    letter-spacing: .18em;
  }
  .section-label::before { content: ""; width: 13px; height: 1px; background: var(--cyan); box-shadow: 0 0 8px var(--cyan); }
  .type-filter {
    width: 100%;
    display: grid;
    grid-template-columns: 18px 1fr auto;
    align-items: center;
    gap: 8px;
    margin: 4px 0;
    padding: 9px 10px;
    border: 1px solid transparent;
    border-radius: 9px;
    color: #8b98b4;
    background: transparent;
    text-align: left;
    cursor: pointer;
    transition: .18s ease;
  }
  .type-filter:hover { color: #dcecff; background: rgba(88, 117, 178, .08); }
  .type-filter.active { color: #f3fbff; border-color: rgba(57, 247, 255, .18); background: linear-gradient(90deg, rgba(57, 247, 255, .11), rgba(84, 115, 255, .04)); }
  .type-filter span:nth-child(2) { font-size: 11px; font-weight: 620; }
  .type-filter b { color: #687796; font: 600 10px/1 ui-monospace, SFMono-Regular, Menlo, monospace; }
  .type-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--type-color); box-shadow: 0 0 10px var(--type-color); }
  .type-dot.spectrum { background: linear-gradient(135deg, var(--cyan), #da4fff); box-shadow: 0 0 10px rgba(57, 247, 255, .7); }
  .rail-divider { height: 1px; margin: 22px 5px; background: var(--line); }
  .predicate-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 4px 8px;
    padding: 8px 5px;
    border-bottom: 1px solid rgba(128, 157, 219, .08);
  }
  .predicate-row span { color: #aebbd4; font-size: 11px; }
  .predicate-row code { grid-column: 1; overflow: hidden; color: #53617e; font-size: 9px; text-overflow: ellipsis; }
  .predicate-row b { grid-column: 2; grid-row: 1 / 3; align-self: center; color: #657391; font: 600 10px/1 monospace; }
  .graph-shell { position: relative; overflow: hidden; }
  .graph-shell::before {
    content: "";
    position: absolute;
    inset: 0;
    pointer-events: none;
    background:
      radial-gradient(circle at 50% 50%, transparent 0 19%, rgba(57, 247, 255, .025) 19.2% 19.5%, transparent 19.7% 37%, rgba(168, 139, 255, .022) 37.2% 37.4%, transparent 37.6%),
      radial-gradient(circle at 50% 50%, rgba(19, 34, 64, .14), transparent 70%);
  }
  #graph { position: relative; display: block; width: 100%; height: 100%; }
  .graph-tools {
    position: absolute;
    z-index: 5;
    top: 16px;
    left: 16px;
    right: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
    pointer-events: none;
  }
  .search-wrap {
    width: min(310px, 48%);
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 0 12px;
    border: 1px solid rgba(128, 157, 219, .2);
    border-radius: 10px;
    background: rgba(8, 13, 28, .78);
    box-shadow: 0 10px 32px rgba(0, 0, 0, .22);
    backdrop-filter: blur(16px);
    pointer-events: auto;
  }
  .search-wrap::before { content: "⌕"; color: var(--cyan); font-size: 17px; }
  #search {
    width: 100%;
    padding: 10px 0;
    border: 0;
    outline: 0;
    color: #dffbff;
    background: transparent;
    font-size: 11px;
  }
  #search::placeholder { color: #52607d; }
  .tool-btn {
    padding: 9px 11px;
    border: 1px solid rgba(128, 157, 219, .2);
    border-radius: 9px;
    color: #94a3bf;
    background: rgba(8, 13, 28, .78);
    backdrop-filter: blur(16px);
    cursor: pointer;
    pointer-events: auto;
  }
  .tool-btn:hover { color: var(--cyan); border-color: rgba(57, 247, 255, .34); }
  #focus-toggle { margin-left: auto; }
  .graph-status {
    position: absolute;
    z-index: 4;
    left: 18px;
    bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
    color: #54627e;
    font: 9px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace;
    letter-spacing: .08em;
    pointer-events: none;
  }
  .live-dot { width: 6px; height: 6px; border-radius: 50%; background: #35df83; box-shadow: 0 0 12px #35df83; animation: blink 1.8s ease-in-out infinite; }
  @keyframes blink { 50% { opacity: .35; } }
  .link-path { fill: none; stroke: #3b4967; stroke-width: 1.15; stroke-opacity: .62; transition: .18s ease; }
  .link-path.active { stroke: var(--cyan); stroke-width: 1.8; stroke-opacity: .82; filter: url(#edgeGlow); }
  .link-hit { fill: none; stroke: transparent; stroke-width: 12; cursor: help; }
  .edge-label {
    fill: #b5eaff;
    font-size: 9px;
    font-weight: 650;
    paint-order: stroke;
    stroke: #07101f;
    stroke-width: 4px;
    stroke-linejoin: round;
    opacity: 0;
    pointer-events: none;
    transition: opacity .18s;
  }
  .edge-label.active { opacity: 1; }
  .node { cursor: pointer; outline: none; pointer-events: bounding-box; transition: opacity .2s; }
  .node-shape { stroke: rgba(255, 255, 255, .82); stroke-width: 1.2; filter: url(#nodeGlow); transition: .2s ease; }
  .node:hover .node-shape, .node:focus .node-shape { stroke: #fff; stroke-width: 2.3; filter: url(#strongGlow); }
  .node.selected .node-shape { stroke: #fff; stroke-width: 2.6; filter: url(#strongGlow); }
  .node-ring { fill: none; stroke: var(--cyan); stroke-width: 1; stroke-dasharray: 3 5; opacity: 0; }
  .node.selected .node-ring { opacity: .85; animation: spin 11s linear infinite; transform-box: fill-box; transform-origin: center; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .node-label {
    fill: #c4d2ec;
    font-size: 10px;
    font-weight: 620;
    paint-order: stroke;
    stroke: rgba(5, 8, 19, .96);
    stroke-width: 3px;
    pointer-events: auto;
  }
  .node.ghost, .link-path.ghost, .link-hit.ghost { opacity: .07; }
  .edge-label.ghost { opacity: 0; }
  .inspector { padding: 22px 22px 38px; }
  .panel-empty { margin-top: 42%; color: #596783; text-align: center; font-size: 12px; line-height: 1.8; }
  .entity-head { position: relative; padding-bottom: 19px; border-bottom: 1px solid var(--line); }
  .entity-type { display: inline-flex; align-items: center; gap: 7px; color: var(--entity-color); font: 700 9px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .14em; }
  .entity-type::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--entity-color); box-shadow: 0 0 12px var(--entity-color); }
  .entity-head h2 { margin: 15px 0 8px; color: #fff; font-size: 27px; line-height: 1.18; letter-spacing: -.02em; }
  .entity-path { color: #53617e; font: 9px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  .description { margin: 16px 0 0; color: #a9b7d0; font-size: 12px; line-height: 1.8; }
  .aliases { display: flex; flex-wrap: wrap; gap: 6px; margin: 14px 0 0; }
  .alias { padding: 4px 7px; border: 1px solid rgba(168, 139, 255, .22); border-radius: 999px; color: #b9a9ff; background: rgba(168, 139, 255, .08); font-size: 9px; }
  .panel-block { margin-top: 22px; }
  .panel-title { margin: 0 0 10px; color: #687796; font: 750 9px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .17em; }
  .entity-body h2, .entity-body h3, .entity-body h4, .entity-body h5, .entity-body h6 { margin: 18px 0 8px; color: #d8e5f8; font-size: 13px; }
  .entity-body p, .entity-body li { color: #96a5bf; font-size: 11.5px; line-height: 1.75; }
  .entity-body ul { padding-left: 19px; }
  .entity-body code { padding: 2px 5px; border-radius: 4px; color: var(--cyan); background: rgba(57, 247, 255, .08); }
  .entity-jump {
    display: inline;
    padding: 0;
    border: 0;
    color: var(--cyan);
    background: none;
    text-decoration: underline;
    text-decoration-color: rgba(57, 247, 255, .28);
    text-underline-offset: 3px;
    cursor: pointer;
  }
  .source-ref { color: #a99bf1; }
  .relation-card {
    margin: 7px 0;
    padding: 10px 11px;
    border: 1px solid rgba(128, 157, 219, .12);
    border-radius: 9px;
    background: rgba(18, 27, 51, .36);
  }
  .relation-main { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; }
  .direction { color: var(--cyan); font: 700 11px/1 monospace; }
  .relation-copy span { display: block; color: #aebbd4; font-size: 10.5px; }
  .relation-copy code { display: block; margin-top: 4px; color: #52617d; font-size: 8.5px; }
  .relation-target { padding: 5px 7px; border: 1px solid rgba(57, 247, 255, .16); border-radius: 6px; color: #dffcff; background: rgba(57, 247, 255, .055); font-size: 9.5px; cursor: pointer; }
  .relation-card details { margin-top: 8px; color: #596783; font-size: 9px; }
  .relation-card summary { cursor: pointer; }
  .evidence-list { margin: 7px 0 0; padding-left: 17px; }
  .evidence-list li { margin: 5px 0; overflow-wrap: anywhere; color: #667592; font: 8.5px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  .source-list { margin: 0; padding: 0; list-style: none; }
  .source-list li { margin: 6px 0; padding: 8px 9px; overflow-wrap: anywhere; border-left: 2px solid rgba(168, 139, 255, .52); color: #64728e; background: rgba(168, 139, 255, .045); font: 8.5px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; }
  #d3-error { display: none; position: absolute; inset: 0; z-index: 10; place-items: center; padding: 30px; color: #ff8b9b; background: #070b16; text-align: center; line-height: 1.8; }
  @media (max-width: 1100px) {
    .workspace { grid-template-columns: minmax(460px, 1fr) 360px; }
    .rail { display: none; }
  }
  @media (max-width: 760px) {
    body { overflow: auto; }
    .app { height: auto; min-height: 100%; grid-template-rows: auto auto; }
    .topbar { min-height: 72px; padding: 12px 14px; }
    .source-tag, .telemetry .stat:nth-child(n+2) { display: none; }
    .workspace { display: block; }
    .graph-shell { height: 62vh; min-height: 440px; }
    .inspector { min-height: 50vh; }
    .search-wrap { width: 58%; }
  }
</style>
</head>
<body>
<div class="app">
  <header class="topbar">
    <span class="brand-orbit" aria-hidden="true"></span>
    <div class="brand-copy">
      <div class="kicker">OPENVIKING // KG EXPLORER</div>
      <h1>__TITLE__</h1>
    </div>
    <div class="source-tag">SOURCE · __SOURCE_NAME__</div>
    <div class="telemetry">
      <div class="stat"><b>__NODE_COUNT__</b><span>ENTITIES</span></div>
      <div class="stat"><b>__LINK_COUNT__</b><span>RELATIONS</span></div>
      <div class="stat"><b>__TYPE_COUNT__</b><span>TYPE SIGNALS</span></div>
    </div>
  </header>
  <main class="workspace">
    <aside class="rail">
      <div class="section-label">ENTITY SIGNALS</div>
      <div id="type-filters">__TYPE_FILTERS__</div>
      <div class="rail-divider"></div>
      <div class="section-label">RELATION CODEX</div>
      <div>__RELATION_LEGEND__</div>
    </aside>
    <section class="graph-shell">
      <div class="graph-tools">
        <label class="search-wrap"><input id="search" type="search" placeholder="扫描实体名称、别名或描述" aria-label="搜索实体"></label>
        <button class="tool-btn" id="reset-zoom" type="button">重置视野</button>
        <button class="tool-btn" id="focus-toggle" type="button">聚焦邻居</button>
      </div>
      <svg id="graph" role="img" aria-label="Knowledge Graph 可视化"></svg>
      <div class="graph-status"><span class="live-dot"></span><span id="graph-status">GRAPH ONLINE · 全图信号</span></div>
      <div id="d3-error">无法加载 D3 图形库，请检查网络连接后重新打开 HTML。</div>
    </section>
    <aside class="inspector" id="inspector"><div class="panel-empty">选择图谱中的实体<br>查看属性、关系与证据链</div></aside>
  </main>
</div>
<script>
const DATA = __DATA_JSON__;

if (!window.d3) {
  document.getElementById("d3-error").style.display = "grid";
} else {
  const svg = d3.select("#graph");
  let width = svg.node().clientWidth;
  let height = svg.node().clientHeight;
  let selectedId = null;
  let activeType = "all";
  let query = "";
  let focusMode = false;
  svg.attr("viewBox", [0, 0, width, height]);

  const defs = svg.append("defs");
  defs.append("filter").attr("id", "nodeGlow").attr("x", "-100%").attr("y", "-100%")
    .attr("width", "300%").attr("height", "300%").html('<feGaussianBlur stdDeviation="2.2" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>');
  defs.append("filter").attr("id", "strongGlow").attr("x", "-150%").attr("y", "-150%")
    .attr("width", "400%").attr("height", "400%").html('<feGaussianBlur stdDeviation="4" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>');
  defs.append("filter").attr("id", "edgeGlow").attr("x", "-50%").attr("y", "-50%")
    .attr("width", "200%").attr("height", "200%").html('<feGaussianBlur stdDeviation="1.4" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>');
  defs.append("marker").attr("id", "arrow").attr("viewBox", "0 -5 10 10")
    .attr("refX", 20).attr("refY", 0).attr("markerWidth", 6).attr("markerHeight", 6)
    .attr("orient", "auto").append("path").attr("d", "M0,-4L9,0L0,4").attr("fill", "#52617d");
  defs.append("marker").attr("id", "arrow-active").attr("viewBox", "0 -5 10 10")
    .attr("refX", 20).attr("refY", 0).attr("markerWidth", 6).attr("markerHeight", 6)
    .attr("orient", "auto").append("path").attr("d", "M0,-4L9,0L0,4").attr("fill", "#39f7ff");

  const layer = svg.append("g");
  const zoom = d3.zoom().scaleExtent([.25, 4]).on("zoom", event => layer.attr("transform", event.transform));
  svg.call(zoom);
  const nodeById = new Map(DATA.nodes.map(node => [node.id, node]));
  const adjacency = new Map(DATA.nodes.map(node => [node.id, new Set()]));
  DATA.links.forEach(link => {
    adjacency.get(link.source)?.add(link.target);
    adjacency.get(link.target)?.add(link.source);
  });

  const pairGroups = d3.group(DATA.links, link => [link.source, link.target].sort().join("\u0000"));
  pairGroups.forEach(group => group.forEach((link, index) => {
    link.curve = (index - (group.length - 1) / 2) * .23;
  }));

  const simulation = d3.forceSimulation(DATA.nodes)
    .force("link", d3.forceLink(DATA.links).id(node => node.id).distance(150).strength(.42))
    .force("charge", d3.forceManyBody().strength(-610))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide().radius(node => nodeRadius(node) + 25).iterations(2));

  const linkPath = layer.append("g").selectAll("path")
    .data(DATA.links).join("path").attr("class", "link-path").attr("marker-end", "url(#arrow)");
  const linkHit = layer.append("g").selectAll("path")
    .data(DATA.links).join("path").attr("class", "link-hit");
  linkHit.append("title").text(link => link.label + " · " + link.relation);
  const edgeLabel = layer.append("g").selectAll("text")
    .data(DATA.links).join("text").attr("class", "edge-label").text(link => link.label);

  const node = layer.append("g").selectAll("g")
    .data(DATA.nodes).join("g").attr("class", "node").attr("tabindex", 0)
    .attr("role", "button").attr("aria-label", item => item.title)
    .on("click", (event, item) => selectNode(item.id, true))
    .on("keydown", (event, item) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode(item.id, true);
      }
    }).call(drag(simulation));
  node.append("circle").attr("class", "node-ring").attr("r", item => nodeRadius(item) + 9);
  node.append("path").attr("class", "node-shape").attr("fill", item => item.color)
    .attr("d", item => symbolPath(item));
  node.append("text").attr("class", "node-label")
    .attr("x", item => nodeRadius(item) + 8).attr("y", 4).text(item => compactTitle(item.title));
  node.append("title").text(item => item.title + " · " + item.type_label);

  simulation.on("tick", () => {
    DATA.nodes.forEach(item => {
      item.x = Math.max(52, Math.min(width - 110, item.x));
      item.y = Math.max(88, Math.min(height - 48, item.y));
    });
    linkPath.attr("d", pathFor);
    linkHit.attr("d", pathFor);
    edgeLabel.attr("x", link => labelPosition(link).x).attr("y", link => labelPosition(link).y);
    node.attr("transform", item => "translate(" + item.x + "," + item.y + ")");
  });

  function nodeRadius(item) {
    return 8 + Math.sqrt(Math.max(item.degree, 1)) * 2.4;
  }

  function symbolPath(item) {
    const symbols = {
      circle: d3.symbolCircle,
      triangle: d3.symbolTriangle,
      diamond: d3.symbolDiamond,
      square: d3.symbolSquare,
      star: d3.symbolStar,
      hexagon: d3.symbolWye
    };
    return d3.symbol().type(symbols[item.symbol] || d3.symbolCircle)
      .size(Math.pow(nodeRadius(item), 2) * 3.05)();
  }

  function compactTitle(title) {
    const chars = Array.from(title);
    return chars.length > 9 ? chars.slice(0, 8).join("") + "…" : title;
  }

  function endpointId(endpoint) {
    return typeof endpoint === "object" ? endpoint.id : endpoint;
  }

  function curveData(link) {
    const source = link.source;
    const target = link.target;
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const length = Math.sqrt(dx * dx + dy * dy) || 1;
    const offset = length * (link.curve || 0);
    return {
      cx: (source.x + target.x) / 2 - dy / length * offset,
      cy: (source.y + target.y) / 2 + dx / length * offset
    };
  }

  function pathFor(link) {
    const control = curveData(link);
    return "M" + link.source.x + "," + link.source.y + " Q" + control.cx + "," + control.cy + " " + link.target.x + "," + link.target.y;
  }

  function labelPosition(link) {
    const control = curveData(link);
    return {
      x: .25 * link.source.x + .5 * control.cx + .25 * link.target.x,
      y: .25 * link.source.y + .5 * control.cy + .25 * link.target.y - 5
    };
  }

  function escapeHTML(value) {
    return String(value).replace(/[&<>"']/g, character => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
    })[character]);
  }

  function matchesQuery(item) {
    if (!query) return true;
    return [item.title, item.description, ...(item.aliases || [])]
      .join(" ").toLocaleLowerCase().includes(query);
  }

  function currentNeighborhood() {
    if (!selectedId || !focusMode) return null;
    return new Set([selectedId, ...(adjacency.get(selectedId) || [])]);
  }

  function applyViewState() {
    const neighborhood = currentNeighborhood();
    const visible = new Set(DATA.nodes.filter(item =>
      (activeType === "all" || item.entity_type === activeType) &&
      matchesQuery(item) && (!neighborhood || neighborhood.has(item.id))
    ).map(item => item.id));
    node.classed("ghost", item => !visible.has(item.id))
      .classed("selected", item => item.id === selectedId);
    linkPath.classed("ghost", link =>
      !visible.has(endpointId(link.source)) || !visible.has(endpointId(link.target)))
      .classed("active", link => selectedId &&
        (endpointId(link.source) === selectedId || endpointId(link.target) === selectedId))
      .attr("marker-end", link => selectedId &&
        (endpointId(link.source) === selectedId || endpointId(link.target) === selectedId)
        ? "url(#arrow-active)" : "url(#arrow)");
    linkHit.classed("ghost", link =>
      !visible.has(endpointId(link.source)) || !visible.has(endpointId(link.target)));
    edgeLabel.classed("active", link => selectedId &&
      (endpointId(link.source) === selectedId || endpointId(link.target) === selectedId))
      .classed("ghost", link =>
        !visible.has(endpointId(link.source)) || !visible.has(endpointId(link.target)));
    const status = neighborhood ? "聚焦 1 跳邻居" : activeType !== "all" ? "类型过滤中" : query ? "搜索扫描中" : "全图信号";
    document.getElementById("graph-status").textContent = "GRAPH ONLINE · " + status + " · " + visible.size + " ENTITIES";
    document.getElementById("focus-toggle").textContent = focusMode ? "显示全图" : "聚焦邻居";
  }

  function relationCard(link, outgoing) {
    const otherId = outgoing ? endpointId(link.target) : endpointId(link.source);
    const other = nodeById.get(otherId);
    const evidence = (link.evidence || []).map(item => "<li>" + escapeHTML(item) + "</li>").join("");
    return '<div class="relation-card"><div class="relation-main"><span class="direction">' +
      (outgoing ? "→" : "←") + '</span><div class="relation-copy"><span>' + escapeHTML(link.label) +
      '</span><code>' + escapeHTML(link.relation) + '</code></div><button class="relation-target" type="button" data-node="' +
      escapeHTML(otherId) + '">' + escapeHTML(other?.title || otherId) + '</button></div><details><summary>证据链 · ' +
      (link.evidence || []).length + '</summary><ul class="evidence-list">' + evidence + "</ul></details></div>";
  }

  function selectNode(id, shouldFocus) {
    const item = nodeById.get(id);
    if (!item) return;
    selectedId = id;
    if (shouldFocus) focusMode = true;
    applyViewState();
    const related = DATA.links.filter(link => endpointId(link.source) === id || endpointId(link.target) === id);
    const relationHTML = related.length ? related.map(link => relationCard(link, endpointId(link.source) === id)).join("")
      : '<div class="panel-empty" style="margin-top:20px">暂无结构化关系</div>';
    const aliases = (item.aliases || []).map(alias => '<span class="alias">' + escapeHTML(alias) + "</span>").join("");
    const sources = (item.sources || []).map(source => "<li>" + escapeHTML(source) + "</li>").join("");
    document.getElementById("inspector").innerHTML =
      '<div class="entity-head" style="--entity-color:' + item.color + '"><div class="entity-type">' +
      escapeHTML(item.type_label) + ' // ' + escapeHTML(item.entity_type.toUpperCase()) + '</div><h2>' +
      escapeHTML(item.title) + '</h2><div class="entity-path">' + escapeHTML(item.path) + '</div>' +
      (item.description ? '<p class="description">' + escapeHTML(item.description) + '</p>' : "") +
      (aliases ? '<div class="aliases">' + aliases + '</div>' : "") + '</div>' +
      (item.body_html ? '<section class="panel-block"><h3 class="panel-title">ENTITY PROFILE</h3><div class="entity-body">' + item.body_html + '</div></section>' : "") +
      '<section class="panel-block"><h3 class="panel-title">RELATION MATRIX · ' + related.length + '</h3>' + relationHTML + '</section>' +
      (sources ? '<section class="panel-block"><h3 class="panel-title">SOURCE SIGNALS</h3><ul class="source-list">' + sources + '</ul></section>' : "");
    document.querySelectorAll("#inspector [data-node]").forEach(element => {
      element.addEventListener("click", () => selectNode(element.dataset.node, true));
    });
    document.getElementById("inspector").scrollTop = 0;
  }

  document.querySelectorAll(".type-filter").forEach(button => {
    button.addEventListener("click", () => {
      activeType = button.dataset.type;
      document.querySelectorAll(".type-filter").forEach(item => item.classList.toggle("active", item === button));
      focusMode = false;
      applyViewState();
    });
  });

  document.getElementById("search").addEventListener("input", event => {
    query = event.target.value.trim().toLocaleLowerCase();
    focusMode = false;
    applyViewState();
  });
  document.getElementById("search").addEventListener("keydown", event => {
    if (event.key !== "Enter" || !query) return;
    const match = DATA.nodes.find(matchesQuery);
    if (match) selectNode(match.id, true);
  });
  document.getElementById("focus-toggle").addEventListener("click", () => {
    focusMode = !focusMode && Boolean(selectedId);
    applyViewState();
  });
  document.getElementById("reset-zoom").addEventListener("click", () => {
    svg.transition().duration(450).call(zoom.transform, d3.zoomIdentity);
  });

  function drag(activeSimulation) {
    return d3.drag()
      .on("start", (event, item) => {
        if (!event.active) activeSimulation.alphaTarget(.25).restart();
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

  const start = DATA.nodes.slice().sort((left, right) => right.degree - left.degree)[0];
  if (start) selectNode(start.id, false);
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
