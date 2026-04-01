"""Helpers for exporting and analyzing WeChat chat archives."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Sequence, Tuple


@dataclass
class WeChatArchiveExportStats:
    """Summary for one export run."""

    source_root: Path
    output_root: Path
    chats: int = 0
    message_files: int = 0
    messages: int = 0
    linked_docs: int = 0
    generated_files: int = 0
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExportedChatRecord:
    """Metadata for one exported chat."""

    chat_dir: str
    chat_name: str
    chat_id: str
    chat_type: str
    aliases: Tuple[str, ...]
    first_seen_ts: str
    last_seen_ts: str
    message_count: int
    overview_path: Path
    day_files: Tuple[Path, ...]

    @property
    def day_count(self) -> int:
        return len(self.day_files)


@dataclass(frozen=True)
class TextMatch:
    """One plain-text match in the exported corpus."""

    path: Path
    snippet: str


@dataclass(frozen=True)
class AnalysisSection:
    """One text section passed into archive analysis prompts."""

    title: str
    source_path: Path
    text: str


def export_wechat_archive(source_root: Path | str, output_root: Path | str) -> WeChatArchiveExportStats:
    """Convert a WeChat chat archive into Markdown files ready for indexing."""
    source_root = Path(source_root).expanduser().resolve()
    output_root = Path(output_root).expanduser().resolve()

    chats_root = source_root / "chats"
    if not chats_root.is_dir():
        raise FileNotFoundError(f"WeChat archive chats directory not found: {chats_root}")

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    stats = WeChatArchiveExportStats(source_root=source_root, output_root=output_root)
    copied_docs: Dict[Path, Path] = {}
    chat_summaries: List[Dict[str, Any]] = []

    for chat_dir in sorted(p for p in chats_root.iterdir() if p.is_dir()):
        summary = _export_chat(chat_dir, source_root, output_root, copied_docs, stats)
        if summary is not None:
            chat_summaries.append(summary)

    _write_text(output_root / "README.md", _render_root_readme(stats, chat_summaries))
    stats.generated_files += 1
    return stats


def list_exported_chats(export_root: Path | str) -> List[ExportedChatRecord]:
    """List chat overviews from an exported archive root."""
    export_root = _resolve_export_root(export_root)
    chats_root = export_root / "chats"
    records: List[ExportedChatRecord] = []
    for chat_dir in sorted(p for p in chats_root.iterdir() if p.is_dir()):
        overview_path = chat_dir / "chat.md"
        if not overview_path.exists():
            continue
        title, metadata = _parse_markdown_metadata(overview_path)
        day_files = tuple(sorted((chat_dir / "days").glob("*.md")))
        records.append(
            ExportedChatRecord(
                chat_dir=chat_dir.name,
                chat_name=title or chat_dir.name,
                chat_id=_strip_code_block(metadata.get("chat_id", "-")),
                chat_type=_strip_code_block(metadata.get("chat_type", "-")),
                aliases=_parse_aliases(metadata.get("aliases", "-")),
                first_seen_ts=_strip_code_block(metadata.get("first_seen_ts", "-")),
                last_seen_ts=_strip_code_block(metadata.get("last_seen_ts", "-")),
                message_count=_parse_int(metadata.get("message_count", "0")),
                overview_path=overview_path,
                day_files=day_files,
            )
        )

    return sorted(
        records,
        key=lambda item: (
            item.last_seen_ts or "",
            item.message_count,
            item.chat_name.lower(),
            item.chat_dir.lower(),
        ),
        reverse=True,
    )


def match_exported_chats(export_root: Path | str, query: str) -> List[ExportedChatRecord]:
    """Match chats by name, alias, directory, or chat id."""
    query_normalized = _normalize_query(query)
    if not query_normalized:
        return list_exported_chats(export_root)

    scored: List[Tuple[int, ExportedChatRecord]] = []
    for chat in list_exported_chats(export_root):
        score = _chat_match_score(chat, query_normalized)
        if score <= 0:
            continue
        scored.append((score, chat))

    return [
        item[1]
        for item in sorted(
            scored,
            key=lambda pair: (
                pair[0],
                pair[1].last_seen_ts or "",
                pair[1].message_count,
                pair[1].chat_name.lower(),
            ),
            reverse=True,
        )
    ]


def find_daily_markdown_files(
    export_root: Path | str,
    date: str,
    chat_query: str | None = None,
) -> List[Path]:
    """Find all exported daily markdown files for one date."""
    matched_paths: List[Path] = []
    for chat in _select_chats(export_root, chat_query):
        for day_file in chat.day_files:
            if day_file.stem == date:
                matched_paths.append(day_file)
    return matched_paths


def find_chat_day_files(
    chat: ExportedChatRecord,
    *,
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> List[Path]:
    """Filter a chat's exported daily files by date or date range."""
    matched: List[Path] = []
    for day_file in chat.day_files:
        day = day_file.stem
        if date and day != date:
            continue
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
            continue
        matched.append(day_file)
    return matched


def iter_export_markdown_files(
    export_root: Path | str,
    *,
    date: str | None = None,
    chat_query: str | None = None,
    include_overviews: bool = False,
) -> Iterator[Path]:
    """Iterate exported markdown files, optionally narrowed by date/chat."""
    for chat in _select_chats(export_root, chat_query):
        if include_overviews and chat.overview_path.exists():
            yield chat.overview_path
        if date:
            for day_file in find_chat_day_files(chat, date=date):
                yield day_file
            continue
        for day_file in chat.day_files:
            yield day_file


def find_text_matches(paths: Sequence[Path], query: str) -> List[TextMatch]:
    """Find plain-text matches across exported markdown files."""
    query_lower = query.lower()
    matches: List[TextMatch] = []
    for path in paths:
        text = _safe_read_text(path)
        if not text or query_lower not in text.lower():
            continue
        matches.append(TextMatch(path=path, snippet=first_matching_line(text, query_lower)))
    return matches


def collect_analysis_sections(
    paths: Sequence[Path],
    *,
    include_linked_docs: bool = True,
    max_total_chars: int = 30000,
    max_source_chars: int = 5000,
    max_linked_doc_chars: int = 3000,
    max_linked_docs: int = 8,
) -> List[AnalysisSection]:
    """Collect bounded text sections for downstream LLM analysis."""
    sections: List[AnalysisSection] = []
    seen_paths: set[Path] = set()
    total_chars = 0
    linked_doc_count = 0

    for path in paths:
        resolved_path = path.resolve()
        if resolved_path in seen_paths:
            continue
        seen_paths.add(resolved_path)

        text = _safe_read_text(path)
        if not text:
            continue

        clipped_text = _clip_text(text, max_source_chars)
        clipped_text, total_chars = _append_section(
            sections=sections,
            title=_extract_markdown_title(text, fallback=path.stem),
            source_path=path,
            text=clipped_text,
            total_chars=total_chars,
            max_total_chars=max_total_chars,
        )
        if clipped_text is None:
            break

        if not include_linked_docs:
            continue

        for doc_path in _extract_linked_doc_paths(path, text):
            if linked_doc_count >= max_linked_docs:
                return sections
            resolved_doc_path = doc_path.resolve()
            if resolved_doc_path in seen_paths:
                continue
            seen_paths.add(resolved_doc_path)

            doc_text = _safe_read_text(doc_path)
            if not doc_text:
                continue

            clipped_doc = _clip_text(doc_text, max_linked_doc_chars)
            clipped_doc, total_chars = _append_section(
                sections=sections,
                title=_extract_markdown_title(doc_text, fallback=doc_path.stem),
                source_path=doc_path,
                text=clipped_doc,
                total_chars=total_chars,
                max_total_chars=max_total_chars,
            )
            if clipped_doc is None:
                return sections
            linked_doc_count += 1

    return sections


def _export_chat(
    chat_dir: Path,
    source_root: Path,
    output_root: Path,
    copied_docs: Dict[Path, Path],
    stats: WeChatArchiveExportStats,
) -> Dict[str, Any] | None:
    meta_path = chat_dir / "chat_meta.json"
    meta = _load_json(meta_path, stats.warnings) if meta_path.exists() else {}

    message_dir = chat_dir / "messages"
    message_files = sorted(message_dir.glob("*.jsonl")) if message_dir.is_dir() else []

    message_count = 0
    type_counter: Counter[str] = Counter()
    available_days: List[str] = []
    copied_doc_count = 0
    copied_doc_paths: set[Path] = set()

    chat_output = output_root / "chats" / chat_dir.name
    days_output = chat_output / "days"
    days_output.mkdir(parents=True, exist_ok=True)

    for message_file in message_files:
        messages, warnings = _load_jsonl(message_file)
        stats.warnings.extend(warnings)
        if not messages:
            continue

        day = message_file.stem
        available_days.append(day)
        stats.message_files += 1
        message_count += len(messages)
        stats.messages += len(messages)

        linked_doc_map: Dict[str, str] = {}
        for message in messages:
            type_counter[_message_type_label(message)] += 1
            doc_path = _extract_document_path(message)
            if not doc_path:
                continue
            copied_path = _copy_linked_doc(
                Path(doc_path),
                source_root=source_root,
                output_root=output_root,
                copied_docs=copied_docs,
                stats=stats,
            )
            if copied_path is None:
                continue
            rel_link = os.path.relpath(copied_path, start=days_output).replace(os.sep, "/")
            linked_doc_map[doc_path] = rel_link
            resolved_doc_path = Path(doc_path).resolve()
            if resolved_doc_path not in copied_doc_paths:
                copied_doc_paths.add(resolved_doc_path)
                copied_doc_count += 1

        day_output = days_output / f"{day}.md"
        _write_text(day_output, _render_day_markdown(meta, day, messages, linked_doc_map))
        stats.generated_files += 1

    if not meta and not message_files:
        return None

    chat_output.mkdir(parents=True, exist_ok=True)
    _write_text(
        chat_output / "chat.md",
        _render_chat_markdown(
            chat_dir_name=chat_dir.name,
            meta=meta,
            available_days=available_days,
            message_count=message_count,
            type_counter=type_counter,
        ),
    )
    stats.generated_files += 1
    stats.chats += 1

    return {
        "chat_dir": chat_dir.name,
        "chat_name": meta.get("current_name") or chat_dir.name,
        "message_count": message_count,
        "days": len(available_days),
        "linked_docs": copied_doc_count,
        "last_seen_ts": meta.get("last_seen_ts", ""),
    }


def _resolve_export_root(export_root: Path | str) -> Path:
    export_root = Path(export_root).expanduser().resolve()
    if not export_root.exists():
        raise FileNotFoundError(f"WeChat export root not found: {export_root}")
    if not (export_root / "chats").is_dir():
        raise FileNotFoundError(f"WeChat export chats directory not found: {export_root / 'chats'}")
    return export_root


def _select_chats(export_root: Path | str, chat_query: str | None) -> List[ExportedChatRecord]:
    if chat_query:
        return match_exported_chats(export_root, chat_query)
    return list_exported_chats(export_root)


def _parse_markdown_metadata(path: Path) -> Tuple[str, Dict[str, str]]:
    title = ""
    metadata: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            continue
        if not line.startswith("- "):
            continue
        body = line[2:]
        if ": " not in body:
            continue
        key, value = body.split(": ", 1)
        metadata[key.strip()] = value.strip()
    return title, metadata


def _parse_aliases(value: str) -> Tuple[str, ...]:
    stripped = value.strip()
    if not stripped or stripped == "-":
        return ()
    return tuple(item.strip() for item in stripped.split(",") if item.strip())


def _strip_code_block(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("`") and stripped.endswith("`") and len(stripped) >= 2:
        return stripped[1:-1]
    return stripped


def _parse_int(value: str) -> int:
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


def _normalize_query(value: str) -> str:
    return " ".join(value.lower().split())


def _chat_match_score(chat: ExportedChatRecord, query: str) -> int:
    candidates = [chat.chat_name, chat.chat_dir, chat.chat_id, *chat.aliases]
    best = 0
    for candidate in candidates:
        normalized = _normalize_query(candidate)
        if not normalized:
            continue
        if normalized == query:
            best = max(best, 100)
        elif normalized.startswith(query):
            best = max(best, 90)
        elif query in normalized:
            best = max(best, 75)
    return best


def _append_section(
    *,
    sections: List[AnalysisSection],
    title: str,
    source_path: Path,
    text: str,
    total_chars: int,
    max_total_chars: int,
) -> Tuple[str | None, int]:
    if not text:
        return "", total_chars
    remaining_chars = max_total_chars - total_chars
    if remaining_chars <= 0:
        return None, total_chars
    if len(text) > remaining_chars:
        if sections:
            return None, total_chars
        text = _clip_text(text, remaining_chars)
    sections.append(AnalysisSection(title=title, source_path=source_path, text=text))
    return text, total_chars + len(text)


def _extract_linked_doc_paths(markdown_path: Path, text: str) -> List[Path]:
    doc_paths: List[Path] = []
    for relative_path in re.findall(r"\(([^)]+document\.md)\)", text):
        linked_doc = (markdown_path.parent / relative_path).resolve()
        if linked_doc.exists():
            doc_paths.append(linked_doc)
    return doc_paths


def _extract_markdown_title(text: str, *, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _clip_text(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    clipped = text[: max(limit - 3, 0)].rstrip()
    return f"{clipped}..." if clipped else ""


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _copy_linked_doc(
    doc_path: Path,
    *,
    source_root: Path,
    output_root: Path,
    copied_docs: Dict[Path, Path],
    stats: WeChatArchiveExportStats,
) -> Path | None:
    doc_path = doc_path.expanduser().resolve()
    if not doc_path.exists():
        stats.warnings.append(f"Linked document missing: {doc_path}")
        return None

    existing = copied_docs.get(doc_path)
    if existing is not None:
        return existing

    try:
        rel_path = doc_path.relative_to(source_root)
        target_path = output_root / rel_path
    except ValueError:
        target_path = output_root / "external_link_docs" / doc_path.name

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(doc_path, target_path)
    copied_docs[doc_path] = target_path
    stats.linked_docs += 1
    stats.generated_files += 1
    return target_path


def _render_root_readme(
    stats: WeChatArchiveExportStats,
    chat_summaries: Iterable[Dict[str, Any]],
) -> str:
    chat_summaries = sorted(
        chat_summaries,
        key=lambda item: item.get("last_seen_ts", ""),
        reverse=True,
    )
    generated_at = datetime.now().isoformat(timespec="seconds")
    lines = [
        "# WeChat Archive Export",
        "",
        f"- Generated at: {generated_at}",
        f"- Source root: `{stats.source_root}`",
        f"- Chats: {stats.chats}",
        f"- Message files: {stats.message_files}",
        f"- Messages: {stats.messages}",
        f"- Linked docs copied: {stats.linked_docs}",
        "",
        "## Chats",
        "",
    ]
    if not chat_summaries:
        lines.append("No chats were exported.")
    else:
        for item in chat_summaries:
            lines.append(
                f"- [{item['chat_name']}](chats/{item['chat_dir']}/chat.md)"
                f" | messages={item['message_count']}"
                f" | days={item['days']}"
            )

    if stats.warnings:
        lines.extend(
            [
                "",
                "## Warnings",
                "",
            ]
        )
        for warning in stats.warnings:
            lines.append(f"- {warning}")

    return "\n".join(lines) + "\n"


def _render_chat_markdown(
    *,
    chat_dir_name: str,
    meta: Dict[str, Any],
    available_days: List[str],
    message_count: int,
    type_counter: Counter[str],
) -> str:
    chat_name = meta.get("current_name") or chat_dir_name
    aliases = ", ".join(meta.get("aliases") or []) or "-"
    lines = [
        f"# {chat_name}",
        "",
        f"- chat_id: `{meta.get('chat_id', '-')}`",
        f"- chat_type: `{meta.get('chat_type', '-')}`",
        f"- first_seen_ts: `{meta.get('first_seen_ts', '-')}`",
        f"- last_seen_ts: `{meta.get('last_seen_ts', '-')}`",
        f"- aliases: {aliases}",
        f"- message_count: {message_count}",
        "",
        "## Message Types",
        "",
    ]
    if type_counter:
        for label, count in type_counter.most_common():
            lines.append(f"- {label}: {count}")
    else:
        lines.append("- No messages exported")

    lines.extend(
        [
            "",
            "## Daily Files",
            "",
        ]
    )
    if available_days:
        for day in available_days:
            lines.append(f"- [{day}](days/{day}.md)")
    else:
        lines.append("- No daily message files found")

    return "\n".join(lines) + "\n"


def _render_day_markdown(
    meta: Dict[str, Any],
    day: str,
    messages: List[Dict[str, Any]],
    linked_doc_map: Dict[str, str],
) -> str:
    chat_name = meta.get("current_name") or meta.get("dir_name") or "Unknown Chat"
    lines = [
        f"# {chat_name} · {day}",
        "",
        f"- chat_id: `{meta.get('chat_id', '-')}`",
        f"- day: `{day}`",
        f"- messages: {len(messages)}",
        "",
    ]

    for index, message in enumerate(messages, start=1):
        sender = message.get("sender") or "Unknown"
        lines.extend(
            [
                f"## {index}. {_format_message_timestamp(message)} · {sender} · {_message_type_label(message)}",
                "",
            ]
        )

        content = str(message.get("content") or "").strip()
        if content:
            lines.append(content)
            lines.append("")

        details = message.get("details") or {}
        analysis = message.get("analysis") or {}
        document = analysis.get("document") or {}

        bullet_lines: List[str] = [
            f"- message_key: `{message.get('message_key', '-')}`",
            f"- event_kind: `{message.get('event_kind', '-')}`",
            f"- first_seen_ts: `{message.get('first_seen_ts', '-')}`",
            f"- processed_ts: `{message.get('processed_ts', '-')}`",
        ]

        detail_title = str(details.get("title") or "").strip()
        if detail_title and detail_title != content:
            bullet_lines.append(f"- detail_title: {detail_title}")

        url = _extract_primary_url(message)
        if url:
            bullet_lines.append(f"- url: {url}")

        analysis_text = str(analysis.get("analysis_text") or "").strip()
        if analysis_text:
            bullet_lines.append(f"- analysis: {analysis_text}")

        doc_summary = str(document.get("summary") or "").strip()
        if doc_summary and doc_summary not in {content, detail_title}:
            bullet_lines.append(f"- linked_doc_summary: {doc_summary}")

        doc_path = _extract_document_path(message)
        if doc_path and doc_path in linked_doc_map:
            bullet_lines.append(f"- linked_doc: [{Path(doc_path).name}]({linked_doc_map[doc_path]})")

        lines.extend(bullet_lines)
        lines.append("")

    return "\n".join(lines) + "\n"


def _load_json(path: Path, warnings: List[str]) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"Failed to read JSON {path}: {exc}")
        return {}


def _load_jsonl(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    messages: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError as exc:
            warnings.append(f"Failed to parse {path}:{line_no}: {exc}")
    return messages, warnings


def _extract_primary_url(message: Dict[str, Any]) -> str:
    details = message.get("details") or {}
    if details.get("url"):
        return str(details["url"])
    analysis = message.get("analysis") or {}
    url_list = analysis.get("url_list") or []
    if url_list:
        return str(url_list[0])
    return ""


def _extract_document_path(message: Dict[str, Any]) -> str:
    analysis = message.get("analysis") or {}
    document = analysis.get("document") or {}
    doc_path = str(document.get("doc_path") or "").strip()
    return doc_path


def _message_type_label(message: Dict[str, Any]) -> str:
    label = str(message.get("type_label") or "unknown")
    base_type = message.get("base_type")
    sub_type = message.get("sub_type")
    return f"{label} ({base_type}/{sub_type})"


def _format_message_timestamp(message: Dict[str, Any]) -> str:
    timestamp = message.get("message_ts")
    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(timestamp).isoformat(sep=" ", timespec="seconds")
    return str(message.get("first_seen_ts") or "-")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def first_matching_line(text: str, query_lower: str) -> str:
    """Return the first non-empty line containing the query."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if query_lower in stripped.lower():
            return stripped
    return ""
