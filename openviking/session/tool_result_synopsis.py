# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Deterministic synopsis generation for externalized tool results."""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Literal, Optional

import yaml

ToolResultKind = Literal["json", "csv", "tsv", "yaml", "xml", "code", "text", "unknown"]

_CODE_PATTERNS = [
    re.compile(r"^\s*(from\s+\S+\s+import|import\s+\S+)", re.MULTILINE),
    re.compile(
        r"^\s*(class|def|async\s+def|function|export\s+function|const|let|var)\s+\w+",
        re.MULTILINE,
    ),
    re.compile(r"^\s*(package|use|pub\s+fn|fn|public\s+class)\s+\w+", re.MULTILINE),
]
_TEXT_HEADER_LIMIT = 18
_TEXT_EXCERPT_CHARS = 500
_JSON_MAX_DEPTH = 2
_JSON_ARRAY_SAMPLE_LIMIT = 3
_JSON_OBJECT_KEY_LIMIT = 10
_YAML_KEY_LIMIT = 30
_XML_CHILD_TAG_LIMIT = 30
_TABLE_FIRST_ROW_SAMPLE_CHARS = 180
_CODE_IMPORT_LIMIT = 12
_CODE_SYMBOL_LIMIT = 24
_CODE_IMPORT_LINE_CHARS = 180
_CODE_SYMBOL_LINE_CHARS = 200


@dataclass(frozen=True)
class ToolResultSynopsis:
    kind: ToolResultKind
    title: str
    summary: List[str]
    structure: List[str]
    notable_items: List[str]
    sample: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolResultSynopsis":
        return cls(
            kind=data.get("kind", "unknown"),
            title=str(data.get("title", "")),
            summary=[str(item) for item in data.get("summary", [])],
            structure=[str(item) for item in data.get("structure", [])],
            notable_items=[str(item) for item in data.get("notable_items", [])],
            sample=str(data.get("sample", "")),
        )


def _clip(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _normalize_text_for_line(text: str, max_len: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return _clip(compact, max_len)


def _head_tail_sample(content: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(content) <= limit:
        return content
    half = max(1, limit // 2)
    return (
        "--- BEGIN SAMPLE HEAD ---\n"
        f"{content[:half]}\n"
        "--- END SAMPLE HEAD ---\n\n"
        "--- BEGIN SAMPLE TAIL ---\n"
        f"{content[-half:]}\n"
        "--- END SAMPLE TAIL ---"
    )


def _looks_binary(content: str) -> bool:
    if not content:
        return False
    if "\x00" in content:
        return True
    control_chars = sum(1 for ch in content[:1000] if ord(ch) < 32 and ch not in {"\n", "\r", "\t"})
    return control_chars / max(1, min(len(content), 1000)) > 0.05


def _type_name(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _json_scalar_examples(value: Any, *, prefix: str = "", limit: int = 5) -> List[str]:
    examples: List[str] = []
    if len(examples) >= limit:
        return examples
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            examples.extend(_json_scalar_examples(child, prefix=child_prefix, limit=limit))
            if len(examples) >= limit:
                break
    elif isinstance(value, list):
        if value:
            examples.extend(_json_scalar_examples(value[0], prefix=f"{prefix}[0]", limit=limit))
    else:
        examples.append(f"{prefix}: {_clip(repr(value), 80)}")
    return examples[:limit]


def _json_shape(value: Any, depth: int = 0) -> str:
    if depth >= _JSON_MAX_DEPTH:
        return "..."
    if isinstance(value, list):
        samples = [_json_shape(item, depth + 1) for item in value[:_JSON_ARRAY_SAMPLE_LIMIT]]
        sample_text = f", sample=[{', '.join(samples)}]" if samples else ""
        return f"array(len={len(value)}{sample_text})"
    if isinstance(value, dict):
        keys = [str(key) for key in list(value.keys())[:_JSON_OBJECT_KEY_LIMIT]]
        key_text = f": {', '.join(keys)}" if keys else ""
        return f"object(keys={len(value)}{key_text})"
    return _type_name(value)


def _summarize_json(value: Any, *, trailing_chars: int = 0) -> ToolResultSynopsis:
    summary = [f"JSON {_type_name(value)}."]
    structure: List[str] = []
    notable_items: List[str] = []
    if isinstance(value, dict):
        keys = [str(key) for key in value.keys()]
        summary.append(f"top-level keys: {', '.join(keys[:_JSON_OBJECT_KEY_LIMIT])}")
        structure.append(f"shape: {_json_shape(value)}")
        for key, child in list(value.items())[:_JSON_OBJECT_KEY_LIMIT]:
            if isinstance(child, list):
                structure.append(f"{key}: array length: {len(child)}")
            elif isinstance(child, dict):
                child_keys = ", ".join(str(k) for k in list(child.keys())[:_JSON_OBJECT_KEY_LIMIT])
                structure.append(f"{key}: object keys: {child_keys}")
            else:
                structure.append(f"{key}: {_type_name(child)}")
    elif isinstance(value, list):
        summary.append(f"array length: {len(value)}")
        structure.append(f"shape: {_json_shape(value)}")
        if value:
            structure.append(f"first item type: {_type_name(value[0])}")
    notable_items.extend(_json_scalar_examples(value))
    if trailing_chars:
        notable_items.append(f"trailing_chars_after_first_json_value: {trailing_chars}")
    return ToolResultSynopsis(
        kind="json",
        title="JSON output",
        summary=summary,
        structure=structure,
        notable_items=notable_items,
        sample="",
    )


def _summarize_yaml(value: Any) -> ToolResultSynopsis:
    summary = [f"YAML {_type_name(value)}."]
    structure: List[str] = []
    if isinstance(value, dict):
        keys = [str(key) for key in value.keys()]
        summary.append(f"top-level keys: {', '.join(keys[:_YAML_KEY_LIMIT])}")
        for key, child in list(value.items())[:_YAML_KEY_LIMIT]:
            structure.append(f"{key}: {_type_name(child)}")
    elif isinstance(value, list):
        summary.append(f"array length: {len(value)}")
    return ToolResultSynopsis(
        kind="yaml",
        title="YAML output",
        summary=summary,
        structure=structure,
        notable_items=[],
        sample="",
    )


def _summarize_xml(root: ET.Element) -> ToolResultSynopsis:
    child_counts = Counter(child.tag for child in list(root))
    structure = [f"root: {root.tag}", f"attributes: {len(root.attrib)}"]
    structure.extend(
        f"{tag}: {count}" for tag, count in child_counts.most_common(_XML_CHILD_TAG_LIMIT)
    )
    return ToolResultSynopsis(
        kind="xml",
        title="XML output",
        summary=[f"XML document with root: {root.tag}."],
        structure=structure,
        notable_items=[],
        sample="",
    )


def _try_table(
    content: str,
    delimiter: str,
    kind: ToolResultKind,
    preview_chars: int,
) -> Optional[ToolResultSynopsis]:
    try:
        rows = list(csv.reader(io.StringIO(content), delimiter=delimiter))
    except csv.Error:
        return None
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if len(rows) < 2 or len(rows[0]) < 2:
        return None
    column_count = len(rows[0])
    if any(len(row) != column_count for row in rows[1 : min(len(rows), 10)]):
        return None
    columns = [cell.strip() or f"column_{idx + 1}" for idx, cell in enumerate(rows[0])]
    data_rows = len(rows) - 1
    first_data = (
        _normalize_text_for_line(delimiter.join(rows[1]), _TABLE_FIRST_ROW_SAMPLE_CHARS)
        if len(rows) > 1
        else "(no data rows)"
    )
    return ToolResultSynopsis(
        kind=kind,
        title=f"{kind.upper()} table output",
        summary=[f"{kind.upper()} table with rows: {data_rows} and columns: {column_count}."],
        structure=[
            f"columns: {', '.join(columns)}",
            f"rows: {data_rows}",
            f"first row sample: {first_data}",
        ],
        notable_items=[],
        sample="",
    )


def _looks_yaml(content: str) -> bool:
    stripped = content.lstrip()
    if stripped.startswith("---"):
        return True
    if any(pattern.search(content) for pattern in _CODE_PATTERNS):
        return False
    lines = [line for line in content.splitlines() if line.strip()]
    for idx, line in enumerate(lines[:-1]):
        if re.match(r"^[A-Za-z0-9_.-]+:\s*(?:#.*)?$", line):
            return lines[idx + 1].startswith((" ", "\t"))
    return False


def _try_yaml(content: str) -> Optional[Any]:
    if not _looks_yaml(content):
        return None
    try:
        value = yaml.safe_load(content)
    except Exception:
        return None
    if isinstance(value, (dict, list)):
        return value
    return None


def _summarize_code(content: str, preview_chars: int) -> ToolResultSynopsis:
    lines = content.splitlines()
    imports = []
    symbols = []
    for line in lines:
        stripped = line.strip()
        import_match = re.match(r"^(?:from\s+(\S+)\s+import|import\s+(\S+))", stripped)
        if import_match:
            imports.append(
                _normalize_text_for_line(
                    import_match.group(1) or import_match.group(2) or "",
                    _CODE_IMPORT_LINE_CHARS,
                )
            )
            continue
        symbol_match = re.match(
            r"^(class|def|async\s+def|function|export\s+function|pub\s+fn|fn)\s+([A-Za-z_]\w*)",
            stripped,
        )
        if symbol_match:
            symbols.append(
                _normalize_text_for_line(
                    f"{symbol_match.group(1)} {symbol_match.group(2)}",
                    _CODE_SYMBOL_LINE_CHARS,
                )
            )
    structure = [f"line_count: {len(lines)}"]
    if imports:
        structure.append(f"imports: {', '.join(imports[:_CODE_IMPORT_LIMIT])}")
    return ToolResultSynopsis(
        kind="code",
        title="Code output",
        summary=[f"Code-like output with {len(lines)} lines."],
        structure=structure,
        notable_items=symbols[:_CODE_SYMBOL_LIMIT],
        sample="",
    )


def _summarize_text(content: str, preview_chars: int) -> ToolResultSynopsis:
    lines = content.splitlines()
    normalized = re.sub(r"\s+", " ", content).strip()
    headers = []
    seen_headers = set()
    for line in lines:
        stripped = line.strip()
        if len(stripped) <= 1:
            continue
        if not (
            re.match(r"^#{1,6}\s+", stripped) or re.match(r"^[A-Z0-9][A-Z0-9\s:_-]{6,}$", stripped)
        ):
            continue
        header = _normalize_text_for_line(stripped, 160)
        if header in seen_headers:
            continue
        seen_headers.add(header)
        headers.append(header)
        if len(headers) >= _TEXT_HEADER_LIMIT:
            break

    word_count = len(normalized.split()) if normalized else 0
    excerpt_chars = _TEXT_EXCERPT_CHARS
    first = _normalize_text_for_line(content[:excerpt_chars], excerpt_chars)
    last = (
        _normalize_text_for_line(content[-excerpt_chars:], excerpt_chars) if excerpt_chars else ""
    )
    return ToolResultSynopsis(
        kind="text",
        title="Text output",
        summary=[
            "Text exploration summary:",
            f"Characters: {len(content)}.",
            f"Words: {word_count}.",
            f"Lines: {len(lines)}.",
            f"Detected section headers: {' | '.join(headers) if headers else 'none detected'}.",
            f"Opening excerpt: {first or '(empty)'}.",
            f"Closing excerpt: {last or '(empty)'}.",
        ],
        structure=[],
        notable_items=[],
        sample="",
    )


def generate_tool_result_synopsis(
    content: str,
    *,
    preview_chars: int,
    tool_name: str = "",
    mime_type: str = "text/plain",
) -> ToolResultSynopsis:
    if not content:
        return ToolResultSynopsis(
            kind="unknown",
            title="Empty output",
            summary=["Output is empty."],
            structure=[],
            notable_items=[],
            sample="",
        )
    if _looks_binary(content):
        return ToolResultSynopsis(
            kind="unknown",
            title="Binary-like output",
            summary=[f"Contains {len(content)} characters and non-text control bytes."],
            structure=[],
            notable_items=[],
            sample=_head_tail_sample(content, preview_chars),
        )

    stripped = content.strip()
    lower_mime = (mime_type or "").lower()
    if "json" in lower_mime or stripped.startswith(("{", "[")):
        try:
            decoder = json.JSONDecoder()
            value, end = decoder.raw_decode(stripped)
            trailing_chars = len(stripped[end:].strip())
            return _summarize_json(value, trailing_chars=trailing_chars)
        except Exception:
            if "json" in lower_mime:
                return ToolResultSynopsis(
                    kind="unknown",
                    title="Unparsed JSON-like output",
                    summary=["JSON-like output failed to parse."],
                    structure=[],
                    notable_items=[],
                    sample=_head_tail_sample(content, preview_chars),
                )
    if "xml" in lower_mime or stripped.startswith("<"):
        try:
            return _summarize_xml(ET.fromstring(content))
        except Exception:
            if "xml" in lower_mime:
                return ToolResultSynopsis(
                    kind="unknown",
                    title="Unparsed XML-like output",
                    summary=["XML-like output failed to parse."],
                    structure=[],
                    notable_items=[],
                    sample=_head_tail_sample(content, preview_chars),
                )
    if "\t" in content:
        table = _try_table(content, "\t", "tsv", preview_chars)
        if table:
            return table
    if "," in content:
        table = _try_table(content, ",", "csv", preview_chars)
        if table:
            return table
    yaml_value = _try_yaml(content)
    if yaml_value is not None:
        return _summarize_yaml(yaml_value)
    if any(pattern.search(content) for pattern in _CODE_PATTERNS):
        return _summarize_code(content, preview_chars)
    return _summarize_text(content, preview_chars)


def render_tool_result_stub(
    synopsis: ToolResultSynopsis,
    *,
    ref: str = "",
    tool_name: str = "",
    sha256: str = "",
    reason: str = "",
    original_chars: int,
    preview_chars: int,
) -> str:
    header = [
        "[OpenViking tool result externalized]",
        f"tool_name: {tool_name or 'tool'}",
        f"kind: {synopsis.kind}",
        f"original_chars: {original_chars}",
        f"preview_chars: {max(preview_chars, 0)}",
    ]
    if ref:
        header.append(f"ref: {ref}")
    if sha256:
        header.append(f"sha256: {sha256[:16]}")
    if reason:
        header.append(f"reason: {reason}")

    body = ["Synopsis:"]
    body.extend(f"- {line}" for line in synopsis.summary)
    if synopsis.structure:
        body.append("")
        body.append("Structure:")
        body.extend(f"- {line}" for line in synopsis.structure)
    if synopsis.notable_items:
        body.append("")
        body.append("Notable items:")
        body.extend(f"- {line}" for line in synopsis.notable_items)
    if synopsis.sample:
        body.append("")
        body.append("Sample:")
        body.append(synopsis.sample)
    if ref:
        body.append("")
        body.append("Explore:")
        body.append(
            f"- Use openviking_tool_result_search with ref={ref} to find relevant raw snippets."
        )
        body.append(
            f"- Use openviking_tool_result_read with ref={ref} and offset/limit to inspect raw output."
        )
        body.append(
            "- Use openviking_tool_result_list to discover other externalized outputs in this session."
        )
    return "\n".join(header) + "\n\n" + "\n".join(body)
