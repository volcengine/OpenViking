#!/usr/bin/env python3
"""Unified CLI for indexing and analyzing a WeChat archive export."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Sequence
from urllib.parse import urlparse

# Add repo root to sys.path for local development usage.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openviking as ov
import requests

from openviking.utils.wechat_archive import (
    AnalysisSection,
    collect_analysis_sections,
    export_wechat_archive,
    find_chat_day_files,
    find_daily_markdown_files,
    find_text_matches,
    first_matching_line,
    iter_export_markdown_files,
    list_exported_chats,
    match_exported_chats,
)
from openviking_cli.utils.config import get_openviking_config


DEFAULT_SOURCE = "/home/nx/chat_archive"
DEFAULT_EXPORT_ROOT = "/home/nx/chat_archive/.openviking_export"
DEFAULT_TARGET = "viking://resources/wechat_archive"
DEFAULT_WORKSPACE = "/home/nx/.openviking-wechat-archive-local-gpu"
DEFAULT_LOCAL_HTTP_URL = "http://127.0.0.1:1934"
DEFAULT_LOCAL_SERVER_CONFIG = "/home/nx/.openviking/wechat_archive_local_gpu_server.conf"
DEFAULT_LOCAL_SERVER_LOG = "/home/nx/.openviking/log/wechat_archive_local_gpu_server.log"
READ_ONLY_HTTP_COMMANDS = {
    "search",
    "daily-summary",
    "chat-summary",
    "topic-report",
    "hotspots",
    "compare-days",
    "timeline-report",
    "sender-report",
    "top-articles",
    "topic-memory-card",
    "watchlist-alerts",
}
DEFAULT_ANALYSIS_BACKEND = "codex"
DEFAULT_CODEX_MODEL = "gpt-5.1-codex-mini"
DEFAULT_ANALYSIS_TIMEOUT = 900.0
DEFAULT_DERIVED_ROOT = "/home/nx/chat_archive/index/derived"
DEFAULT_DERIVED_TARGET = "viking://resources/wechat_archive_reports"
DEFAULT_WATCHLIST_FILE = "/home/nx/chat_archive/index/watchlist_topics.txt"


@dataclass(frozen=True)
class PromptSection:
    """Bounded prompt input for archive analysis."""

    title: str
    source: str
    text: str

@dataclass(frozen=True)
class DaySourceSummary:
    """Compact coverage stats for one exported day file."""

    path: Path
    title: str
    messages: int
    linked_docs: int
    has_text_message: bool
    has_shared_link: bool
    has_voice_message: bool
    has_system_notice: bool


@dataclass(frozen=True)
class MessageEntry:
    """One exported chat message parsed from a day markdown file."""

    timestamp: str
    sender: str
    message_type: str
    body: str
    source_path: Path
    chat_title: str
    linked_doc_path: Path | None


@dataclass(frozen=True)
class ArticleCandidate:
    """Candidate linked article for recommendation output."""

    doc_path: Path
    title: str
    mention_count: int
    source_count: int
    sources: tuple[str, ...]
    days: tuple[str, ...]


@dataclass(frozen=True)
class WatchlistTopic:
    """One persisted watchlist topic definition."""

    label: str
    query: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Index and analyze an exported WeChat archive with one CLI."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Refresh export files and queue indexing")
    _add_common_storage_args(index_parser)
    index_parser.add_argument(
        "--reason",
        default="WeChat archive export for semantic retrieval",
        help="Reason passed into OpenViking add_resource",
    )
    index_parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Wait timeout in seconds when --wait is enabled",
    )
    index_parser.add_argument(
        "--wait",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Wait for indexing to complete before returning",
    )
    index_parser.add_argument(
        "--watch-interval",
        type=float,
        default=0.0,
        help="Optional watch interval in minutes for the export root",
    )
    index_parser.add_argument(
        "--semantic-concurrency",
        type=int,
        default=2,
        help="Maximum concurrent semantic LLM tasks for this workflow",
    )
    index_parser.add_argument(
        "--embedding-concurrency",
        type=int,
        default=4,
        help="Maximum concurrent embedding requests for this workflow",
    )
    index_parser.add_argument(
        "--semantic-llm-timeout",
        type=float,
        default=180.0,
        help="Timeout in seconds for one semantic LLM call before fallback",
    )
    index_parser.add_argument(
        "--embedding-text-source",
        choices=("summary_first", "summary_only", "content_only"),
        default="content_only",
        help="Text source used for file embeddings during archive indexing",
    )

    search_parser = subparsers.add_parser("search", help="Run semantic search against the archive")
    _add_common_storage_args(search_parser)
    search_parser.add_argument("query", help="Semantic query text")
    search_parser.add_argument("--limit", type=int, default=5, help="Maximum number of results")
    search_parser.add_argument(
        "--text-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include export text matches when semantic hits are empty or sparse",
    )

    daily_parser = subparsers.add_parser(
        "daily-summary", help="Summarize one day across the exported archive"
    )
    _add_common_storage_args(daily_parser)
    daily_parser.add_argument("date", help="Date in YYYY-MM-DD format")
    daily_parser.add_argument(
        "--chat",
        default=None,
        help="Optional chat filter by name, alias, or chat_id",
    )
    daily_parser.add_argument(
        "--output",
        default=None,
        help="Optional file path to write the Markdown report",
    )
    _add_analysis_args(daily_parser)

    chat_parser = subparsers.add_parser(
        "chat-summary", help="Summarize one chat for a date or date range"
    )
    _add_common_storage_args(chat_parser)
    chat_parser.add_argument("chat_query", help="Chat name, alias, or chat_id")
    chat_parser.add_argument("--date", default=None, help="Single date in YYYY-MM-DD format")
    chat_parser.add_argument("--start-date", default=None, help="Range start in YYYY-MM-DD")
    chat_parser.add_argument("--end-date", default=None, help="Range end in YYYY-MM-DD")
    chat_parser.add_argument(
        "--output",
        default=None,
        help="Optional file path to write the Markdown report",
    )
    _add_analysis_args(chat_parser)

    topic_parser = subparsers.add_parser(
        "topic-report", help="Analyze one topic across semantic hits and export matches"
    )
    _add_common_storage_args(topic_parser)
    topic_parser.add_argument("query", help="Topic or keyword to analyze")
    topic_parser.add_argument("--limit", type=int, default=8, help="Maximum semantic hits")
    topic_parser.add_argument("--date", default=None, help="Optional date in YYYY-MM-DD format")
    topic_parser.add_argument(
        "--chat",
        default=None,
        help="Optional chat filter by name, alias, or chat_id",
    )
    topic_parser.add_argument(
        "--output",
        default=None,
        help="Optional file path to write the Markdown report",
    )
    _add_analysis_args(topic_parser)

    hotspots_parser = subparsers.add_parser(
        "hotspots", help="Identify recurring hotspots across one date or date range"
    )
    _add_common_storage_args(hotspots_parser)
    hotspots_parser.add_argument("--date", default=None, help="Single date in YYYY-MM-DD format")
    hotspots_parser.add_argument("--start-date", default=None, help="Range start in YYYY-MM-DD")
    hotspots_parser.add_argument("--end-date", default=None, help="Range end in YYYY-MM-DD")
    hotspots_parser.add_argument(
        "--chat",
        default=None,
        help="Optional chat filter by name, alias, or chat_id",
    )
    hotspots_parser.add_argument(
        "--output",
        default=None,
        help="Optional file path to write the Markdown report",
    )
    _add_analysis_args(hotspots_parser)

    compare_parser = subparsers.add_parser(
        "compare-days", help="Compare two days across the exported archive"
    )
    _add_common_storage_args(compare_parser)
    compare_parser.add_argument("day1", help="First date in YYYY-MM-DD format")
    compare_parser.add_argument("day2", help="Second date in YYYY-MM-DD format")
    compare_parser.add_argument(
        "--chat",
        default=None,
        help="Optional chat filter by name, alias, or chat_id",
    )
    compare_parser.add_argument(
        "--output",
        default=None,
        help="Optional file path to write the Markdown report",
    )
    _add_analysis_args(compare_parser)

    timeline_parser = subparsers.add_parser(
        "timeline-report", help="Build a chronological topic timeline"
    )
    _add_common_storage_args(timeline_parser)
    timeline_parser.add_argument("query", help="Topic or keyword to analyze chronologically")
    timeline_parser.add_argument("--limit", type=int, default=10, help="Maximum semantic hits")
    timeline_parser.add_argument("--date", default=None, help="Optional date in YYYY-MM-DD format")
    timeline_parser.add_argument("--start-date", default=None, help="Range start in YYYY-MM-DD")
    timeline_parser.add_argument("--end-date", default=None, help="Range end in YYYY-MM-DD")
    timeline_parser.add_argument(
        "--chat",
        default=None,
        help="Optional chat filter by name, alias, or chat_id",
    )
    timeline_parser.add_argument(
        "--output",
        default=None,
        help="Optional file path to write the Markdown report",
    )
    _add_analysis_args(timeline_parser)

    sender_parser = subparsers.add_parser(
        "sender-report", help="Analyze who is contributing content in one chat"
    )
    _add_common_storage_args(sender_parser)
    sender_parser.add_argument("chat_query", help="Chat name, alias, or chat_id")
    sender_parser.add_argument("--date", default=None, help="Single date in YYYY-MM-DD format")
    sender_parser.add_argument("--start-date", default=None, help="Range start in YYYY-MM-DD")
    sender_parser.add_argument("--end-date", default=None, help="Range end in YYYY-MM-DD")
    sender_parser.add_argument(
        "--query",
        default=None,
        help="Optional topic keyword used to filter relevant sender messages",
    )
    sender_parser.add_argument(
        "--output",
        default=None,
        help="Optional file path to write the Markdown report",
    )
    _add_analysis_args(sender_parser)

    top_articles_parser = subparsers.add_parser(
        "top-articles", help="Recommend the most worthwhile linked articles"
    )
    _add_common_storage_args(top_articles_parser)
    top_articles_parser.add_argument("--date", default=None, help="Single date in YYYY-MM-DD format")
    top_articles_parser.add_argument("--start-date", default=None, help="Range start in YYYY-MM-DD")
    top_articles_parser.add_argument("--end-date", default=None, help="Range end in YYYY-MM-DD")
    top_articles_parser.add_argument(
        "--chat",
        default=None,
        help="Optional chat filter by name, alias, or chat_id",
    )
    top_articles_parser.add_argument(
        "--query",
        default=None,
        help="Optional topic keyword used to narrow article candidates",
    )
    top_articles_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of recommended articles",
    )
    top_articles_parser.add_argument(
        "--output",
        default=None,
        help="Optional file path to write the Markdown report",
    )
    _add_analysis_args(top_articles_parser)

    topic_card_parser = subparsers.add_parser(
        "topic-memory-card",
        help="Build a durable topic memory card and index it back into OpenViking",
    )
    _add_common_storage_args(topic_card_parser)
    topic_card_parser.add_argument("query", help="Topic or keyword to persist as a memory card")
    topic_card_parser.add_argument("--limit", type=int, default=8, help="Maximum semantic hits")
    topic_card_parser.add_argument("--date", default=None, help="Optional date in YYYY-MM-DD format")
    topic_card_parser.add_argument("--start-date", default=None, help="Range start in YYYY-MM-DD")
    topic_card_parser.add_argument("--end-date", default=None, help="Range end in YYYY-MM-DD")
    topic_card_parser.add_argument(
        "--chat",
        default=None,
        help="Optional chat filter by name, alias, or chat_id",
    )
    topic_card_parser.add_argument(
        "--slug",
        default=None,
        help="Optional stable slug used for the saved memory card filename",
    )
    topic_card_parser.add_argument(
        "--output",
        default=None,
        help="Optional file path to write the Markdown report",
    )
    _add_persist_args(topic_card_parser)
    _add_analysis_args(topic_card_parser)

    watchlist_parser = subparsers.add_parser(
        "watchlist-alerts",
        help="Build one watchlist alert digest and index it back into OpenViking",
    )
    _add_common_storage_args(watchlist_parser)
    watchlist_parser.add_argument(
        "topics",
        nargs="*",
        help="Optional watchlist topics; when omitted, load topics from --watchlist-file",
    )
    watchlist_parser.add_argument("--date", default=None, help="Optional date in YYYY-MM-DD format")
    watchlist_parser.add_argument("--start-date", default=None, help="Range start in YYYY-MM-DD")
    watchlist_parser.add_argument("--end-date", default=None, help="Range end in YYYY-MM-DD")
    watchlist_parser.add_argument(
        "--chat",
        default=None,
        help="Optional chat filter by name, alias, or chat_id",
    )
    watchlist_parser.add_argument(
        "--watchlist-file",
        default=DEFAULT_WATCHLIST_FILE,
        help="Text file with one watchlist topic per line; supports `label: query`",
    )
    watchlist_parser.add_argument(
        "--limit-per-topic",
        type=int,
        default=6,
        help="Maximum source snippets collected for each watchlist topic",
    )
    watchlist_parser.add_argument(
        "--output",
        default=None,
        help="Optional file path to write the Markdown report",
    )
    _add_persist_args(watchlist_parser)
    _add_analysis_args(watchlist_parser)

    return parser


def _add_common_storage_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Original WeChat archive root")
    parser.add_argument("--export-root", default=DEFAULT_EXPORT_ROOT, help="Export root")
    parser.add_argument("--target", default=DEFAULT_TARGET, help="OpenViking target URI")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE, help="Embedded workspace path")
    parser.add_argument(
        "--http-url",
        default=None,
        help="Optional OpenViking HTTP URL. When set, use HTTP mode instead of embedded mode.",
    )


def _add_analysis_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--analysis-backend",
        choices=("codex", "vlm", "auto"),
        default=DEFAULT_ANALYSIS_BACKEND,
        help="Backend used for Markdown analysis commands",
    )
    parser.add_argument(
        "--codex-model",
        default=DEFAULT_CODEX_MODEL,
        help="Codex CLI model used when analysis backend is codex or auto",
    )
    parser.add_argument(
        "--analysis-timeout",
        type=float,
        default=DEFAULT_ANALYSIS_TIMEOUT,
        help="Timeout in seconds for one analysis generation",
    )


def _add_persist_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--report-root",
        default=DEFAULT_DERIVED_ROOT,
        help="Local directory used to save derived Markdown reports",
    )
    parser.add_argument(
        "--report-target",
        default=DEFAULT_DERIVED_TARGET,
        help="OpenViking target URI used for derived reports",
    )
    parser.add_argument(
        "--sync",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Index the generated report back into OpenViking",
    )


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _extract_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _clip_text(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _response_text(response: object) -> str:
    if hasattr(response, "content"):
        return str(getattr(response, "content") or "").strip()
    return str(response).strip()


def _clean_analysis_markdown(text: str) -> str:
    cleaned = re.sub(r"cite[^]+", "", text)
    cleaned = re.sub(r"[ \t]+(\n|$)", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _silence_openviking_warnings(level: int = logging.ERROR) -> None:
    logging.getLogger().setLevel(level)
    manager = logging.Logger.manager
    for name in ["openviking", *list(manager.loggerDict.keys())]:
        if name.startswith("openviking"):
            logging.getLogger(name).setLevel(level)


def _apply_runtime_config(args: argparse.Namespace, quiet: bool = True) -> None:
    config = get_openviking_config()
    if quiet:
        config.log.level = "ERROR"
        _silence_openviking_warnings(logging.ERROR)
    if hasattr(args, "semantic_concurrency") and args.semantic_concurrency > 0:
        config.vlm.max_concurrent = args.semantic_concurrency
    if hasattr(args, "embedding_concurrency") and args.embedding_concurrency > 0:
        config.embedding.max_concurrent = args.embedding_concurrency
    if hasattr(args, "semantic_llm_timeout") and args.semantic_llm_timeout > 0:
        config.semantic.llm_timeout_seconds = args.semantic_llm_timeout
    if hasattr(args, "embedding_text_source") and args.embedding_text_source:
        config.embedding.text_source = args.embedding_text_source


def _resolve_http_url(args: argparse.Namespace) -> str | None:
    if getattr(args, "http_url", None):
        return args.http_url
    if getattr(args, "command", "") not in READ_ONLY_HTTP_COMMANDS:
        return None
    if getattr(args, "workspace", None) != DEFAULT_WORKSPACE:
        return None
    return DEFAULT_LOCAL_HTTP_URL


def _http_server_is_healthy(base_url: str, timeout: float = 0.5) -> bool:
    health_url = f"{base_url.rstrip('/')}/health"
    try:
        response = requests.get(health_url, timeout=timeout)
        return response.ok
    except requests.RequestException:
        return False


def _prepare_local_server_config(workspace: str, base_url: str) -> Path:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 1933

    source_path = Path(os.environ.get("OPENVIKING_CONFIG_FILE", "/home/nx/.openviking/ov.conf"))
    config = json.loads(source_path.read_text())
    config.setdefault("storage", {})["workspace"] = workspace
    server = config.setdefault("server", {})
    server["host"] = host
    server["port"] = port

    target_path = Path(DEFAULT_LOCAL_SERVER_CONFIG)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return target_path


def _maybe_start_local_http_server(base_url: str, workspace: str) -> bool:
    if _http_server_is_healthy(base_url, timeout=0.3):
        return True

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = str(parsed.port or 1933)
    config_path = _prepare_local_server_config(workspace, base_url)

    log_path = Path(DEFAULT_LOCAL_SERVER_LOG)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "openviking.server.bootstrap",
                "--config",
                str(config_path),
                "--host",
                host,
                "--port",
                port,
            ],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,
        )
    finally:
        os.close(log_fd)

    deadline = time.time() + 30.0
    while time.time() < deadline:
        if _http_server_is_healthy(base_url, timeout=0.5):
            return True
        time.sleep(0.25)
    return False


def _open_client(args: argparse.Namespace, quiet: bool = True):
    _apply_runtime_config(args, quiet=quiet)
    try:
        http_url = _resolve_http_url(args)
        if http_url:
            if getattr(args, "http_url", None):
                if not _http_server_is_healthy(http_url, timeout=1.0):
                    raise SystemExit(f"OpenViking HTTP server unavailable: {http_url}")
            elif not _http_server_is_healthy(http_url, timeout=0.3):
                if _maybe_start_local_http_server(http_url, args.workspace):
                    print(f"[wechat-archive-agent] started local OpenViking server at {http_url}", file=sys.stderr)
                else:
                    print(
                        f"[wechat-archive-agent] local OpenViking server did not become healthy in time, fallback to embedded mode: {http_url}",
                        file=sys.stderr,
                    )
                    http_url = None
            if http_url:
                client = ov.SyncHTTPClient(url=http_url)
                client.initialize()
                return client
        client = ov.OpenViking(path=args.workspace) if args.workspace else ov.OpenViking()
        client.initialize()
        return client
    except RuntimeError as exc:
        message = str(exc)
        if "AGFS port" in message and "already in use" in message:
            raise SystemExit(
                "Embedded OpenViking is already running in another process. "
                "Wait for that job to finish, or use --http-url to reuse an existing HTTP server."
            ) from exc
        raise


def _ensure_export_root(export_root: str) -> Path:
    root = Path(export_root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(
            f"Export root not found: {root}\n"
            "Run `index` first to build the Markdown export and semantic index."
        )
    return root


def _analysis_sections_to_prompt_sections(sections: Sequence[AnalysisSection]) -> List[PromptSection]:
    return [
        PromptSection(
            title=section.title,
            source=str(section.source_path),
            text=section.text,
        )
        for section in sections
    ]


def _render_prompt_sections(sections: Sequence[PromptSection]) -> str:
    blocks: List[str] = []
    for index, section in enumerate(sections, start=1):
        blocks.append(
            "\n".join(
                [
                    f"## Source {index}",
                    f"Title: {section.title}",
                    f"Path: {section.source}",
                    "",
                    section.text.strip(),
                ]
            )
        )
    return "\n\n".join(blocks)


def _read_text_quiet(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _parse_day_source_summary(path: Path) -> DaySourceSummary:
    text = _read_text_quiet(path)
    title = _extract_title(text, path.stem)
    messages_match = re.search(r"^- messages: `(\d+)`$", text, flags=re.MULTILINE)
    messages = int(messages_match.group(1)) if messages_match else 0
    linked_docs = len(re.findall(r"^- linked_doc:", text, flags=re.MULTILINE))
    has_text_message = " · 文本 " in text
    has_shared_link = " · 分享链接 " in text
    has_voice_message = " · 语音 " in text
    has_system_notice = " · 系统通知 " in text
    return DaySourceSummary(
        path=path,
        title=title.split(" · ", 1)[0].strip() or title,
        messages=messages,
        linked_docs=linked_docs,
        has_text_message=has_text_message,
        has_shared_link=has_shared_link,
        has_voice_message=has_voice_message,
        has_system_notice=has_system_notice,
    )


def _rank_day_paths(paths: Sequence[Path]) -> List[Path]:
    summaries = [_parse_day_source_summary(path) for path in paths]
    ranked = sorted(
        summaries,
        key=lambda item: (
            item.messages,
            item.linked_docs,
            item.title.lower(),
        ),
        reverse=True,
    )
    return [item.path for item in ranked]


def _build_daily_coverage_section(date: str, paths: Sequence[Path]) -> PromptSection:
    summaries = [_parse_day_source_summary(path) for path in paths]
    if not summaries:
        return PromptSection(
            title=f"当日覆盖概况 {date}",
            source=f"virtual://daily-coverage/{date}",
            text=f"# 当日覆盖概况 {date}\n\n- total_sources: 0\n",
        )

    total_sources = len(summaries)
    total_messages = sum(item.messages for item in summaries)
    total_linked_docs = sum(item.linked_docs for item in summaries)
    single_message_sources = sum(1 for item in summaries if item.messages <= 1)
    text_message_sources = sum(1 for item in summaries if item.has_text_message)
    shared_link_sources = sum(1 for item in summaries if item.has_shared_link)
    voice_message_sources = sum(1 for item in summaries if item.has_voice_message)
    system_notice_sources = sum(1 for item in summaries if item.has_system_notice)
    mixed_mode_sources = sum(
        1
        for item in summaries
        if sum(
            [
                item.has_text_message,
                item.has_shared_link,
                item.has_voice_message,
                item.has_system_notice,
            ]
        ) >= 2
    )
    top_active = sorted(
        summaries,
        key=lambda item: (item.messages, item.linked_docs, item.title.lower()),
        reverse=True,
    )[:12]
    top_linked = [item for item in top_active if item.linked_docs > 0][:8]

    lines = [
        f"# 当日覆盖概况 {date}",
        "",
        f"- text_message_sources: {text_message_sources}",
        f"- shared_link_sources: {shared_link_sources}",
        f"- voice_message_sources: {voice_message_sources}",
        f"- system_notice_sources: {system_notice_sources}",
        f"- mixed_mode_sources: {mixed_mode_sources}",
        f"- total_sources: {total_sources}",
        f"- total_messages: {total_messages}",
        f"- total_linked_docs: {total_linked_docs}",
        f"- single_message_sources: {single_message_sources}",
        "",
        "## Active Sources",
        "",
    ]
    for item in top_active:
        lines.append(
            f"- {item.title} | messages={item.messages} | linked_docs={item.linked_docs}"
        )
    if top_linked:
        lines.extend(["", "## Linked Document Heavy Sources", ""])
        for item in top_linked:
            lines.append(
                f"- {item.title} | messages={item.messages} | linked_docs={item.linked_docs}"
            )
    return PromptSection(
        title=f"当日覆盖概况 {date}",
        source=f"virtual://daily-coverage/{date}",
        text="\n".join(lines),
    )


def _merge_prompt_sections(*groups: Sequence[PromptSection]) -> List[PromptSection]:
    merged: List[PromptSection] = []
    seen_sources: set[str] = set()
    for group in groups:
        for section in group:
            if section.source in seen_sources:
                continue
            seen_sources.add(section.source)
            merged.append(section)
    return merged


def _fallback_report(title: str, sections: Sequence[PromptSection], *, query: str | None = None) -> str:
    lines = [f"# {title}", ""]
    if not sections:
        lines.append("No matching content found.")
        return "\n".join(lines) + "\n"
    lines.append(f"- Sources: {len(sections)}")
    lines.append("")
    lines.append("## 重点")
    lines.append("")
    query_lower = query.lower() if query else ""
    for section in sections[:10]:
        snippet = (
            first_matching_line(section.text, query_lower)
            if query_lower
            else next((line.strip() for line in section.text.splitlines() if line.strip()), "")
        )
        snippet = _clip_text(snippet or section.text, 180)
        lines.append(f"- {section.title} | {snippet}")
    return "\n".join(lines) + "\n"


def _fallback_compare_report(
    title: str,
    *,
    day1: str,
    day2: str,
    sections1: Sequence[PromptSection],
    sections2: Sequence[PromptSection],
) -> str:
    titles1 = [section.title for section in sections1]
    titles2 = [section.title for section in sections2]
    counter1 = Counter(titles1)
    counter2 = Counter(titles2)
    common = [title for title in counter1 if title in counter2]
    only_day1 = [title for title in counter1 if title not in counter2]
    only_day2 = [title for title in counter2 if title not in counter1]

    lines = [
        f"# {title}",
        "",
        f"- {day1} sources: {len(sections1)}",
        f"- {day2} sources: {len(sections2)}",
        "",
        "## 重点",
        "",
        f"- 两天共有 {len(common)} 个重复来源标题，说明存在持续话题。",
        f"- {day2} 独有来源 {len(only_day2)} 个，{day1} 独有来源 {len(only_day1)} 个。",
    ]
    if common:
        lines.append(f"- 持续出现的话题示例：{'; '.join(common[:5])}")
    if only_day2:
        lines.append(f"- {day2} 新出现的话题示例：{'; '.join(only_day2[:5])}")
    if only_day1:
        lines.append(f"- {day1} 出现但 {day2} 未延续的话题示例：{'; '.join(only_day1[:5])}")

    lines.extend(["", "## 来源", ""])
    for label, sections in ((day1, sections1), (day2, sections2)):
        for section in sections[:5]:
            lines.append(f"- {label} | {section.title}")
    return "\n".join(lines) + "\n"


def _fallback_timeline_report(
    title: str,
    sections: Sequence[PromptSection],
    *,
    query: str,
) -> str:
    lines = [f"# {title}", "", f"- Sources: {len(sections)}", "", "## 时间线", ""]
    for section in _sort_prompt_sections_by_date(sections)[:12]:
        day = _extract_date_string(section.source) or _extract_date_string(section.title) or "未知日期"
        snippet = first_matching_line(section.text, query.lower())
        snippet = _clip_text(snippet or section.text, 160)
        lines.append(f"- {day} | {section.title} | {snippet}")
    lines.extend(["", "## 来源", ""])
    for section in _sort_prompt_sections_by_date(sections)[:12]:
        lines.append(f"- {section.source}")
    return "\n".join(lines) + "\n"


def _run_markdown_analysis(
    *,
    title: str,
    task: str,
    sections: Sequence[PromptSection],
    query: str | None = None,
    analysis_backend: str = DEFAULT_ANALYSIS_BACKEND,
    codex_model: str = DEFAULT_CODEX_MODEL,
    analysis_timeout: float = DEFAULT_ANALYSIS_TIMEOUT,
    prompt_rules: Sequence[str] | None = None,
    fallback_builder=None,
) -> str:
    if fallback_builder is None:
        fallback_builder = lambda: _fallback_report(title, sections, query=query)
    if not sections:
        return fallback_builder()
    if prompt_rules is None:
        prompt_rules = [
            "Structure the answer as: one short summary paragraph, then `## 重点`, then `## 来源`.",
            "Under `## 重点`, keep 4-8 flat bullet points.",
        ]

    prompt = "\n\n".join(
        [
            "You are analyzing a WeChat archive export.",
            "Return concise Markdown in Chinese-simplified.",
            "Do not invent facts. Base every statement on the provided sources only.",
            "Preserve concrete numbers, dates, percentages, and money amounts exactly as written in the sources. Do not drop decimal points or magnify values.",
            "Do not run shell commands or inspect repository files. Use only the provided sources.",
            "Do not emit citation tokens, XML tags, or pseudo reference markers.",
            *prompt_rules,
            f"Task: {task}",
            "",
            _render_prompt_sections(sections),
        ]
    )

    backend = (analysis_backend or DEFAULT_ANALYSIS_BACKEND).lower()
    if backend in {"codex", "auto"}:
        try:
            text = _run_codex_cli_analysis(
                prompt=prompt,
                model=codex_model,
                timeout=analysis_timeout,
            )
            if text:
                return _clean_analysis_markdown(text)
        except Exception as exc:
            print(
                f"[wechat-archive-agent] Codex CLI analysis failed, falling back: {exc}",
                file=sys.stderr,
            )

    config = get_openviking_config()
    try:
        if not config.vlm.is_available():
            return fallback_builder()
        _ensure_vlm_runtime_ready(config)
        original_max_tokens = getattr(config.vlm, "max_tokens", None)
        if original_max_tokens is None or original_max_tokens <= 0:
            config.vlm.max_tokens = 700
        else:
            config.vlm.max_tokens = min(original_max_tokens, 700)
        response = config.vlm.get_completion(prompt=prompt)
        text = _response_text(response)
        return _clean_analysis_markdown(text) if text else fallback_builder()
    except Exception:
        return fallback_builder()
    finally:
        if "original_max_tokens" in locals():
            config.vlm.max_tokens = original_max_tokens


def _run_codex_cli_analysis(*, prompt: str, model: str, timeout: float) -> str:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise RuntimeError("`codex` executable not found in PATH")

    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".md", delete=False) as handle:
        output_path = Path(handle.name)

    try:
        command = [
            codex_bin,
            "exec",
            "-c",
            "model_reasoning_effort=\"high\"",
            "--ephemeral",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--sandbox",
            "read-only",
            "--model",
            model,
            "--output-last-message",
            str(output_path),
            "-",
        ]
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=repo_root,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            detail = _clip_text(detail, 400)
            raise RuntimeError(f"exit={completed.returncode} detail={detail}")

        text = output_path.read_text(encoding="utf-8").strip()
        if not text:
            raise RuntimeError("Codex CLI returned an empty final message")
        return text
    finally:
        output_path.unlink(missing_ok=True)


def _analysis_kwargs(args: argparse.Namespace) -> dict:
    return {
        "analysis_backend": getattr(args, "analysis_backend", DEFAULT_ANALYSIS_BACKEND),
        "codex_model": getattr(args, "codex_model", DEFAULT_CODEX_MODEL),
        "analysis_timeout": getattr(args, "analysis_timeout", DEFAULT_ANALYSIS_TIMEOUT),
    }


def _admin_base_from_api_base(api_base: str | None) -> str | None:
    if not api_base:
        return None
    try:
        parsed = urlparse(api_base)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return parsed._replace(path=path, params="", query="", fragment="").geturl().rstrip("/")


def _model_pool_entry(model_pool: dict, model_name: str | None) -> dict | None:
    if not model_name:
        return None
    for item in model_pool.get("models", []):
        if not isinstance(item, dict):
            continue
        if item.get("model_id") == model_name or item.get("model_alias") == model_name:
            return item
    return None


def _ensure_vlm_runtime_ready(config) -> None:
    if getattr(config.vlm, "provider", None) != "openai":
        return

    model_name = getattr(config.vlm, "model", None)
    admin_base = _admin_base_from_api_base(getattr(config.vlm, "api_base", None))
    if not model_name or not admin_base:
        return

    try:
        runtime_response = requests.get(
            f"{admin_base}/admin/api/runtime",
            timeout=5,
        )
        runtime_response.raise_for_status()
        runtime = runtime_response.json()
        model_pool = runtime.get("model_pool")
        backend = runtime.get("backend")
        if not isinstance(model_pool, dict) or not isinstance(backend, dict):
            return

        entry = _model_pool_entry(model_pool, model_name)
        if not isinstance(entry, dict):
            return

        diagnostics = (backend.get("details") or {}).get("diagnostics") or {}
        default_model_id = model_pool.get("default_model_id")
        managed_process_running = diagnostics.get("managed_process_running")

        should_load = not bool(entry.get("loaded"))
        stale_default_runtime = (
            entry.get("model_id") == default_model_id
            and bool(entry.get("loaded"))
            and managed_process_running is False
        )
        if not should_load and not stale_default_runtime:
            return

        session = requests.Session()
        if stale_default_runtime:
            unload_response = session.post(
                f"{admin_base}/admin/api/runtime/model-pool/unload",
                json={"model_id": entry.get("model_id") or model_name},
                timeout=60,
            )
            unload_response.raise_for_status()

        load_response = session.post(
            f"{admin_base}/admin/api/runtime/model-pool/load",
            json={"model_id": entry.get("model_id") or model_name},
            timeout=360,
        )
        load_response.raise_for_status()
        print(
            f"[wechat-archive-agent] repaired VLM runtime for {entry.get('model_id') or model_name}",
            file=sys.stderr,
        )
    except Exception:
        return


def _selected_chats(export_root: Path, chat_query: str | None):
    if not chat_query:
        return list_exported_chats(export_root)
    matches = match_exported_chats(export_root, chat_query)
    if not matches:
        raise SystemExit(f"No chat matched query: {chat_query}")
    return matches


def _collect_day_paths(
    export_root: Path,
    *,
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    chat_query: str | None = None,
) -> List[Path]:
    paths: List[Path] = []
    for chat in _selected_chats(export_root, chat_query):
        paths.extend(
            find_chat_day_files(
                chat,
                date=date,
                start_date=start_date,
                end_date=end_date,
            )
        )
    return sorted(set(paths))


def _range_label(*, date: str | None = None, start_date: str | None = None, end_date: str | None = None) -> str:
    if date:
        return date
    if start_date and end_date:
        return f"{start_date} ~ {end_date}"
    if start_date:
        return f"{start_date} ~ latest"
    if end_date:
        return f"start ~ {end_date}"
    return "all dates"


def _prefix_prompt_sections(prefix: str, sections: Sequence[PromptSection]) -> List[PromptSection]:
    return [
        PromptSection(
            title=f"{prefix} | {section.title}",
            source=f"{prefix} | {section.source}",
            text=section.text,
        )
        for section in sections
    ]


def _extract_date_string(value: str) -> str:
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", value)
    return match.group(0) if match else ""


def _sort_prompt_sections_by_date(sections: Sequence[PromptSection]) -> List[PromptSection]:
    return sorted(
        sections,
        key=lambda section: (
            _extract_date_string(section.source) or _extract_date_string(section.title) or "9999-99-99",
            section.source,
            section.title,
        ),
    )


def _extract_doc_path_from_block(day_path: Path, block: str) -> Path | None:
    match = re.search(r"^- linked_doc: \[document\.md\]\(([^)]+)\)$", block, flags=re.MULTILINE)
    if not match:
        return None
    doc_path = (day_path.parent / match.group(1)).resolve()
    return doc_path if doc_path.exists() else None


def _extract_doc_title(doc_path: Path, fallback: str) -> str:
    text = _read_text_quiet(doc_path)
    return _extract_title(text, fallback) if text else fallback


def _parse_day_messages(path: Path) -> List[MessageEntry]:
    text = _read_text_quiet(path)
    if not text:
        return []

    chat_title = _extract_title(text, path.stem).split(" · ", 1)[0].strip() or path.stem
    pattern = re.compile(
        r"^##\s+\d+\.\s+(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+·\s+(?P<sender>.+?)\s+·\s+(?P<message_type>.+?)\s+\([^)]+\)\n\n(?P<content>.*?)(?=^##\s+\d+\.\s+|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    entries: List[MessageEntry] = []
    for match in pattern.finditer(text):
        block = match.group("content").strip()
        body = block.split("\n\n- message_key:", 1)[0].strip()
        entries.append(
            MessageEntry(
                timestamp=match.group("timestamp"),
                sender=match.group("sender").strip(),
                message_type=match.group("message_type").strip(),
                body=body,
                source_path=path,
                chat_title=chat_title,
                linked_doc_path=_extract_doc_path_from_block(path, block),
            )
        )
    return entries


def _entry_matches_query(entry: MessageEntry, query: str) -> bool:
    query_lower = query.lower()
    haystacks = [entry.sender.lower(), entry.message_type.lower(), entry.body.lower()]
    if entry.linked_doc_path is not None:
        haystacks.append(_read_text_quiet(entry.linked_doc_path).lower())
    return any(query_lower in haystack for haystack in haystacks)


def _collect_entries(paths: Sequence[Path]) -> List[MessageEntry]:
    entries: List[MessageEntry] = []
    for path_item in paths:
        entries.extend(_parse_day_messages(path_item))
    return entries


def _fallback_sender_report(
    title: str,
    *,
    sender_rows: Sequence[tuple[str, dict]],
    query: str | None,
) -> str:
    lines = [f"# {title}", ""]
    if query:
        lines.append(f"- Query: {query}")
    lines.append(f"- Senders: {len(sender_rows)}")
    lines.extend(["", "## 活跃发送者", ""])
    for sender, stats in sender_rows[:8]:
        lines.append(
            f"- {sender} | messages={stats['messages']} | links={stats['links']} | text={stats['texts']} | voice={stats['voices']} | days={len(stats['days'])}"
        )
    return "\n".join(lines) + "\n"


def _build_sender_sections(
    *,
    chat_title: str,
    entries: Sequence[MessageEntry],
    query: str | None,
) -> tuple[List[PromptSection], List[tuple[str, dict]]]:
    sender_stats: dict[str, dict] = {}
    for entry in entries:
        if not query and entry.sender == "系统":
            continue
        if query and not _entry_matches_query(entry, query):
            continue
        stats = sender_stats.setdefault(
            entry.sender,
            {
                "messages": 0,
                "links": 0,
                "texts": 0,
                "voices": 0,
                "days": set(),
                "samples": [],
            },
        )
        stats["messages"] += 1
        stats["days"].add(entry.source_path.stem)
        if "分享链接" in entry.message_type:
            stats["links"] += 1
        if "文本" in entry.message_type:
            stats["texts"] += 1
        if "语音" in entry.message_type:
            stats["voices"] += 1
        sample_text = entry.body or entry.message_type
        sample_text = _clip_text(" ".join(sample_text.split()), 160)
        if len(stats["samples"]) < 4 and sample_text:
            stats["samples"].append(f"- {entry.timestamp} | {entry.message_type} | {sample_text}")

    sender_rows = sorted(
        sender_stats.items(),
        key=lambda item: (item[1]["messages"], item[1]["links"], item[0].lower()),
        reverse=True,
    )
    coverage_lines = [
        f"# 发送者统计 {chat_title}",
        "",
        f"- total_messages: {sum(item[1]['messages'] for item in sender_rows)}",
        f"- total_senders: {len(sender_rows)}",
    ]
    if query:
        coverage_lines.append(f"- filtered_query: {query}")
    coverage_lines.extend(["", "## Top Senders", ""])
    for sender, stats in sender_rows[:10]:
        coverage_lines.append(
            f"- {sender} | messages={stats['messages']} | links={stats['links']} | text={stats['texts']} | voice={stats['voices']} | days={len(stats['days'])}"
        )

    sections: List[PromptSection] = [
        PromptSection(
            title=f"发送者统计 {chat_title}",
            source=f"virtual://sender-report/{chat_title}",
            text="\n".join(coverage_lines),
        )
    ]
    for sender, stats in sender_rows[:8]:
        detail_lines = [
            f"# {sender}",
            "",
            f"- messages: {stats['messages']}",
            f"- shared_links: {stats['links']}",
            f"- text_messages: {stats['texts']}",
            f"- voice_messages: {stats['voices']}",
            f"- active_days: {len(stats['days'])}",
            "",
            "## Samples",
            "",
            *stats["samples"],
        ]
        sections.append(
            PromptSection(
                title=f"发送者 {sender}",
                source=f"virtual://sender-report/{chat_title}/{sender}",
                text="\n".join(detail_lines),
            )
        )
    return sections, sender_rows


def _fallback_top_articles_report(
    title: str,
    *,
    records: Sequence[ArticleCandidate],
    limit: int,
) -> str:
    lines = [f"# {title}", "", f"- Candidates: {len(records)}", "", "## 推荐阅读", ""]
    for record in records[:limit]:
        lines.append(
            f"- {record.title} | sources={record.source_count} | mentions={record.mention_count} | days={', '.join(record.days[:3])}"
        )
    return "\n".join(lines) + "\n"


def _collect_article_candidates(paths: Sequence[Path], query: str | None = None) -> List[ArticleCandidate]:
    grouped: dict[Path, dict] = {}
    for entry in _collect_entries(paths):
        if entry.linked_doc_path is None:
            continue
        if query and not _entry_matches_query(entry, query):
            continue
        bucket = grouped.setdefault(
            entry.linked_doc_path,
            {
                "title": _extract_doc_title(entry.linked_doc_path, entry.body or entry.linked_doc_path.stem),
                "mention_count": 0,
                "sources": set(),
                "days": set(),
            },
        )
        bucket["mention_count"] += 1
        bucket["sources"].add(entry.chat_title)
        bucket["days"].add(entry.source_path.stem)

    records = [
        ArticleCandidate(
            doc_path=doc_path,
            title=data["title"],
            mention_count=data["mention_count"],
            source_count=len(data["sources"]),
            sources=tuple(sorted(data["sources"])),
            days=tuple(sorted(data["days"])),
        )
        for doc_path, data in grouped.items()
    ]
    return sorted(
        records,
        key=lambda item: (item.source_count, item.mention_count, item.title.lower()),
        reverse=True,
    )


def _build_top_articles_sections(
    *,
    records: Sequence[ArticleCandidate],
    limit: int,
) -> List[PromptSection]:
    candidate_lines = [
        "# 候选文章池",
        "",
        f"- total_candidates: {len(records)}",
        f"- requested_limit: {limit}",
        "",
        "## Candidates",
        "",
    ]
    for record in records[: max(limit * 3, 10)]:
        candidate_lines.append(
            f"- {record.title} | sources={record.source_count} | mentions={record.mention_count} | days={', '.join(record.days[:3])}"
        )

    sections: List[PromptSection] = [
        PromptSection(
            title="候选文章池",
            source="virtual://top-articles/candidates",
            text="\n".join(candidate_lines),
        )
    ]
    doc_paths = [record.doc_path for record in records[: max(limit * 3, 10)]]
    sections.extend(
        _analysis_sections_to_prompt_sections(
            collect_analysis_sections(
                doc_paths,
                include_linked_docs=False,
                max_total_chars=24000,
                max_source_chars=1800,
            )
        )
    )
    return sections


def run_index(args: argparse.Namespace) -> int:
    stats = export_wechat_archive(args.source, args.export_root)
    client = _open_client(args, quiet=False)
    try:
        result = client.add_resource(
            path=str(Path(args.export_root).expanduser().resolve()),
            to=args.target,
            reason=args.reason,
            instruction=(
                "Index this WeChat archive export for semantic retrieval. "
                "Prioritize chat topics, senders, message dates, and linked article context."
            ),
            wait=args.wait,
            timeout=args.timeout if args.wait else None,
            watch_interval=args.watch_interval if not args.http_url else 0,
        )
    finally:
        client.close()

    print(f"Export root: {stats.output_root}")
    print(
        "Export stats:"
        f" chats={stats.chats}"
        f" message_files={stats.message_files}"
        f" messages={stats.messages}"
        f" linked_docs={stats.linked_docs}"
        f" generated_files={stats.generated_files}"
    )
    print(f"Indexed target: {result.get('root_uri') or args.target}")
    queue_status = result.get("queue_status")
    if queue_status:
        print(f"Queue status: {queue_status}")
    if not args.wait:
        print("Indexing was queued asynchronously.")
    return 0


def _format_search_hits(resources: Sequence[object]) -> str:
    lines: List[str] = []
    for index, item in enumerate(resources, start=1):
        score = getattr(item, "score", 0.0)
        uri = getattr(item, "uri", "")
        abstract = (getattr(item, "abstract", "") or "").strip()
        lines.append(f"{index}. {uri}")
        lines.append(f"   score={score:.4f}")
        if abstract:
            lines.append(f"   abstract={_clip_text(' '.join(abstract.split()), 180)}")
    return "\n".join(lines)


def run_search(args: argparse.Namespace) -> int:
    client = _open_client(args, quiet=True)
    try:
        result = client.find(query=args.query, target_uri=args.target, limit=args.limit)
    finally:
        client.close()

    resources = getattr(result, "resources", []) if result is not None else []
    if resources:
        print(_format_search_hits(resources))
        return 0

    if not args.text_fallback:
        print("No results found.")
        return 0

    export_root = _ensure_export_root(args.export_root)
    paths = list(iter_export_markdown_files(export_root, include_overviews=True))
    matches = find_text_matches(paths, args.query)
    if not matches:
        print("No results found.")
        return 0

    for index, item in enumerate(matches[: args.limit], start=1):
        print(f"{index}. {item.path}")
        print(f"   snippet={_clip_text(item.snippet, 180)}")
    return 0


def run_daily_summary(args: argparse.Namespace) -> int:
    _apply_runtime_config(args, quiet=True)
    export_root = _ensure_export_root(args.export_root)
    paths = find_daily_markdown_files(export_root, args.date, chat_query=args.chat)
    coverage_section = _build_daily_coverage_section(args.date, paths)
    coverage_sections = _analysis_sections_to_prompt_sections(
        collect_analysis_sections(
            paths,
            include_linked_docs=False,
            max_total_chars=60000,
            max_source_chars=750,
        )
    )
    focus_paths = _rank_day_paths(paths)[:12]
    focus_sections = _analysis_sections_to_prompt_sections(
        collect_analysis_sections(
            focus_paths,
            include_linked_docs=True,
            max_total_chars=24000,
            max_source_chars=1400,
            max_linked_doc_chars=2200,
            max_linked_docs=12,
        )
    )
    sections = _merge_prompt_sections([coverage_section], coverage_sections, focus_sections)
    title = f"WeChat 日报 {args.date}" if not args.chat else f"{args.chat} 日报 {args.date}"
    task = (
        f"Produce a daily intelligence brief for WeChat archive date {args.date}. "
        f"There are {len(paths)} source chats in scope."
    )
    if args.chat:
        task += f" Limit the analysis to chat filter `{args.chat}`."
    report = _run_markdown_analysis(
        title=title,
        task=task,
        sections=sections,
        prompt_rules=[
            "Treat this as a daily intelligence brief, not a flat message list.",
            "Prioritize recurring or strategically useful themes first: AI, robotics, autonomous driving, chips, industry, policy, business, and technical work.",
            "If a topic appears in multiple sources, explicitly say it is a repeated theme or a multi-source signal.",
            "Do not claim all sources are one format unless the coverage stats support it; if both chat messages and shared links exist, describe the day as mixed.",
            "Demote one-off low-signal entertainment, lifestyle, or generic clickbait items into a short `## 低优先级杂项` section unless they dominate the day.",
            "Structure the answer as: one short summary paragraph, then `## 今日主线`, then `## 重点主题`, then `## 低优先级杂项`, then `## 来源分布`.",
            "Under `## 今日主线`, keep 2-4 flat bullet points describing the main lines of the day.",
            "Under `## 重点主题`, keep 4-8 flat bullet points and mention concrete source names.",
            "Under `## 来源分布`, summarize what kinds of sources dominated the day and mention the source count.",
        ],
        **_analysis_kwargs(args),
    )
    _emit_output(report, args.output)
    return 0


def _choose_chat(export_root: Path, query: str):
    matches = match_exported_chats(export_root, query)
    if not matches:
        raise SystemExit(f"No chat matched query: {query}")
    if len(matches) == 1:
        return matches[0]

    normalized_query = _normalize_text(query)
    exact_matches = []
    for chat in matches:
        fields = [chat.chat_name, chat.chat_dir, chat.chat_id, *chat.aliases]
        if any(_normalize_text(field) == normalized_query for field in fields if field):
            exact_matches.append(chat)
    if len(exact_matches) == 1:
        return exact_matches[0]

    lines = [f"Multiple chats matched `{query}`. Refine the query with a more specific name or chat_id:", ""]
    for chat in matches[:10]:
        lines.append(
            f"- {chat.chat_name} | chat_id={chat.chat_id} | messages={chat.message_count} | last_seen={chat.last_seen_ts}"
        )
    raise SystemExit("\n".join(lines))


def run_chat_summary(args: argparse.Namespace) -> int:
    _apply_runtime_config(args, quiet=True)
    export_root = _ensure_export_root(args.export_root)
    chat = _choose_chat(export_root, args.chat_query)
    paths = list(chat.day_files)
    if args.date:
        paths = [path for path in paths if path.stem == args.date]
    if args.start_date:
        paths = [path for path in paths if path.stem >= args.start_date]
    if args.end_date:
        paths = [path for path in paths if path.stem <= args.end_date]
    sections = _analysis_sections_to_prompt_sections(collect_analysis_sections(paths))
    range_text = args.date or f"{args.start_date or chat.first_seen_ts} ~ {args.end_date or chat.last_seen_ts}"
    title = f"{chat.chat_name} 内容分析"
    task = (
        f"Summarize chat `{chat.chat_name}` (`{chat.chat_id}`) for range {range_text}. "
        "Highlight main topics, repeated themes, and notable linked articles."
    )
    report = _run_markdown_analysis(
        title=title,
        task=task,
        sections=sections,
        **_analysis_kwargs(args),
    )
    _emit_output(report, args.output)
    return 0


def _semantic_sections_from_hits(client, query: str, target: str, limit: int) -> List[PromptSection]:
    result = client.find(query=query, target_uri=target, limit=limit)
    resources = getattr(result, "resources", []) if result is not None else []
    sections: List[PromptSection] = []
    for item in resources:
        uri = getattr(item, "uri", "")
        if not uri:
            continue
        abstract = (getattr(item, "abstract", "") or "").strip()
        try:
            text = abstract or client.read(uri, offset=0, limit=120)
        except Exception:
            if not abstract:
                continue
            text = abstract
        title = _extract_title(text, Path(uri).name or uri)
        sections.append(PromptSection(title=title, source=uri, text=_clip_text(text, 3000)))
    return sections


def run_topic_report(args: argparse.Namespace) -> int:
    _apply_runtime_config(args, quiet=True)
    export_root = _ensure_export_root(args.export_root)
    export_paths = list(
        iter_export_markdown_files(
            export_root,
            date=args.date,
            chat_query=args.chat,
            include_overviews=True,
        )
    )
    text_matches = find_text_matches(export_paths, args.query)
    export_sections = _analysis_sections_to_prompt_sections(
        collect_analysis_sections([item.path for item in text_matches[:10]])
    )

    client = _open_client(args, quiet=True)
    try:
        semantic_sections = _semantic_sections_from_hits(
            client, args.query, args.target, args.limit
        )
    finally:
        client.close()

    merged_sections: List[PromptSection] = []
    seen_sources = set()
    for section in [*semantic_sections, *export_sections]:
        if section.source in seen_sources:
            continue
        seen_sources.add(section.source)
        merged_sections.append(section)

    title = f"专题分析：{args.query}"
    task = f"Analyze topic `{args.query}` across the WeChat archive."
    if args.date:
        task += f" Focus on date {args.date}."
    if args.chat:
        task += f" Limit the analysis to chat filter `{args.chat}` when possible."
    report = _run_markdown_analysis(
        title=title,
        task=task,
        sections=merged_sections,
        query=args.query,
        **_analysis_kwargs(args),
    )
    _emit_output(report, args.output)
    return 0


def run_hotspots(args: argparse.Namespace) -> int:
    _apply_runtime_config(args, quiet=True)
    export_root = _ensure_export_root(args.export_root)
    paths = _collect_day_paths(
        export_root,
        date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
        chat_query=args.chat,
    )
    sections = _analysis_sections_to_prompt_sections(collect_analysis_sections(paths))
    range_text = _range_label(date=args.date, start_date=args.start_date, end_date=args.end_date)
    title = f"热点分析 {range_text}" if not args.chat else f"{args.chat} 热点分析 {range_text}"
    task = (
        f"Identify the hottest recurring themes in WeChat archive content for range {range_text}. "
        "Group repeated topics, repeated linked articles, and repeated entities. "
        "Explain why each hotspot matters and mention which sources repeat it."
    )
    if args.chat:
        task += f" Limit the analysis to chat filter `{args.chat}`."
    report = _run_markdown_analysis(
        title=title,
        task=task,
        sections=sections,
        **_analysis_kwargs(args),
    )
    _emit_output(report, args.output)
    return 0


def run_compare_days(args: argparse.Namespace) -> int:
    _apply_runtime_config(args, quiet=True)
    export_root = _ensure_export_root(args.export_root)
    paths1 = find_daily_markdown_files(export_root, args.day1, chat_query=args.chat)
    paths2 = find_daily_markdown_files(export_root, args.day2, chat_query=args.chat)
    sections1 = _analysis_sections_to_prompt_sections(collect_analysis_sections(paths1))
    sections2 = _analysis_sections_to_prompt_sections(collect_analysis_sections(paths2))
    merged_sections = [
        *_prefix_prompt_sections(args.day1, sections1),
        *_prefix_prompt_sections(args.day2, sections2),
    ]
    title = f"日期对比 {args.day1} vs {args.day2}"
    if args.chat:
        title = f"{args.chat} 日期对比 {args.day1} vs {args.day2}"
    task = (
        f"Compare WeChat archive content between {args.day1} and {args.day2}. "
        "Highlight persistent topics, new topics on the later day, fading topics, and any source coverage shift."
    )
    if args.chat:
        task += f" Limit the comparison to chat filter `{args.chat}`."
    report = _run_markdown_analysis(
        title=title,
        task=task,
        sections=merged_sections,
        **_analysis_kwargs(args),
        fallback_builder=lambda: _fallback_compare_report(
            title,
            day1=args.day1,
            day2=args.day2,
            sections1=sections1,
            sections2=sections2,
        ),
    )
    _emit_output(report, args.output)
    return 0


def run_timeline_report(args: argparse.Namespace) -> int:
    _apply_runtime_config(args, quiet=True)
    export_root = _ensure_export_root(args.export_root)
    export_paths = list(
        iter_export_markdown_files(
            export_root,
            date=args.date,
            chat_query=args.chat,
            include_overviews=True,
        )
    )
    if args.start_date or args.end_date:
        filtered_paths: List[Path] = []
        for path in export_paths:
            day = _extract_date_string(str(path))
            if args.start_date and day and day < args.start_date:
                continue
            if args.end_date and day and day > args.end_date:
                continue
            filtered_paths.append(path)
        export_paths = filtered_paths
    text_matches = find_text_matches(export_paths, args.query)
    export_sections = _analysis_sections_to_prompt_sections(
        collect_analysis_sections([item.path for item in text_matches[: max(args.limit, 1) * 2]])
    )

    client = _open_client(args, quiet=True)
    try:
        semantic_sections = _semantic_sections_from_hits(
            client,
            args.query,
            args.target,
            args.limit,
        )
    finally:
        client.close()

    merged_sections: List[PromptSection] = []
    seen_sources = set()
    for section in [*semantic_sections, *export_sections]:
        if section.source in seen_sources:
            continue
        seen_sources.add(section.source)
        merged_sections.append(section)
    merged_sections = _sort_prompt_sections_by_date(merged_sections)

    range_text = _range_label(date=args.date, start_date=args.start_date, end_date=args.end_date)
    title = f"时间线：{args.query}"
    task = (
        f"Build a chronological timeline for topic `{args.query}` across WeChat archive sources in range {range_text}. "
        "Each bullet should reflect a dated development, viewpoint, or source mention. "
        "Prefer chronology over clustering."
    )
    if args.chat:
        task += f" Limit the timeline to chat filter `{args.chat}` when possible."
    report = _run_markdown_analysis(
        title=title,
        task=task,
        sections=merged_sections,
        query=args.query,
        **_analysis_kwargs(args),
        fallback_builder=lambda: _fallback_timeline_report(
            title,
            merged_sections,
            query=args.query,
        ),
    )
    _emit_output(report, args.output)
    return 0


def run_sender_report(args: argparse.Namespace) -> int:
    _apply_runtime_config(args, quiet=True)
    export_root = _ensure_export_root(args.export_root)
    chat = _choose_chat(export_root, args.chat_query)
    paths = list(chat.day_files)
    if args.date:
        paths = [path for path in paths if path.stem == args.date]
    if args.start_date:
        paths = [path for path in paths if path.stem >= args.start_date]
    if args.end_date:
        paths = [path for path in paths if path.stem <= args.end_date]

    sections, sender_rows = _build_sender_sections(
        chat_title=chat.chat_name,
        entries=_collect_entries(paths),
        query=args.query,
    )
    title = f"{chat.chat_name} 发送者分析"
    if args.query:
        title += f" · {args.query}"
    range_text = args.date or f"{args.start_date or chat.first_seen_ts} ~ {args.end_date or chat.last_seen_ts}"
    task = (
        f"Analyze who is contributing content in chat `{chat.chat_name}` for range {range_text}. "
        "Highlight the most active senders, what they usually contribute, and whether the contribution is links, text, or voice."
    )
    if args.query:
        task += f" Focus only on content related to `{args.query}`."
    report = _run_markdown_analysis(
        title=title,
        task=task,
        sections=sections,
        prompt_rules=[
            "Treat this as a sender activity report for one chat.",
            "Structure the answer as: one short summary paragraph, then `## 活跃发送者`, then `## 内容类型`, then `## 来源`.",
            "Under `## 活跃发送者`, keep 4-8 flat bullet points and mention concrete sender names.",
            "Call out repeated senders and whether they mainly share links, text notes, or voice messages.",
            "Ignore pure system-notice noise unless it is materially relevant.",
        ],
        **_analysis_kwargs(args),
        fallback_builder=lambda: _fallback_sender_report(
            title,
            sender_rows=sender_rows,
            query=args.query,
        ),
    )
    _emit_output(report, args.output)
    return 0


def run_top_articles(args: argparse.Namespace) -> int:
    _apply_runtime_config(args, quiet=True)
    export_root = _ensure_export_root(args.export_root)
    paths = _collect_day_paths(
        export_root,
        date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
        chat_query=args.chat,
    )
    records = _collect_article_candidates(paths, query=args.query)
    title = "推荐文章"
    if args.date or args.start_date or args.end_date:
        title += f" { _range_label(date=args.date, start_date=args.start_date, end_date=args.end_date) }"
    if args.chat:
        title += f" · {args.chat}"
    sections = _build_top_articles_sections(records=records, limit=args.limit)
    task = (
        f"Recommend the {args.limit} most worthwhile linked articles from the WeChat archive. "
        "Prefer technology, AI, robotics, autonomous driving, chips, policy, and infrastructure. "
        "De-prioritize entertainment, lifestyle, and generic clickbait."
    )
    if args.query:
        task += f" Focus on articles related to `{args.query}`."
    report = _run_markdown_analysis(
        title=title,
        task=task,
        sections=sections,
        query=args.query,
        prompt_rules=[
            "Treat this as a recommendation brief for what to read next.",
            f"Under `## 推荐阅读`, output up to {args.limit} flat bullet points.",
            "Structure the answer as: one short summary paragraph, then `## 推荐阅读`, then `## 候选分布`, then `## 来源`.",
            "Each recommendation bullet should include the article title, why it is worth reading, and what kind of reader would benefit.",
            "Prefer high-signal industry or technical articles over entertainment or lifestyle pieces.",
        ],
        **_analysis_kwargs(args),
        fallback_builder=lambda: _fallback_top_articles_report(
            title,
            records=records,
            limit=args.limit,
        ),
    )
    _emit_output(report, args.output)
    return 0




def _slugify_filename(value: str) -> str:
    normalized = _normalize_text(value)
    normalized = normalized.replace("/", " ")
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff._-]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized)
    normalized = normalized.strip(" -_.")
    return normalized or "report"


def _report_stamp(*, date: str | None = None, start_date: str | None = None, end_date: str | None = None) -> str:
    if date:
        return date
    if start_date or end_date:
        return f"{start_date or 'start'}_to_{end_date or 'latest'}"
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _section_matches_range(
    section: PromptSection,
    *,
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> bool:
    day = _extract_date_string(section.source) or _extract_date_string(section.title)
    if date and day and day != date:
        return False
    if start_date and day and day < start_date:
        return False
    if end_date and day and day > end_date:
        return False
    return True


def _filter_export_paths_by_range(
    paths: Sequence[Path],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Sequence[Path]:
    if not start_date and not end_date:
        return paths

    filtered_paths: List[Path] = []
    for path in paths:
        day = _extract_date_string(str(path))
        if start_date and day and day < start_date:
            continue
        if end_date and day and day > end_date:
            continue
        filtered_paths.append(path)
    return filtered_paths


def _collect_topic_prompt_sections(
    args: argparse.Namespace,
    *,
    query: str,
    limit: int,
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    chat: str | None = None,
    export_paths: Sequence[Path] | None = None,
    client=None,
) -> List[PromptSection]:
    if export_paths is None:
        export_root = _ensure_export_root(args.export_root)
        export_paths = list(
            iter_export_markdown_files(
                export_root,
                date=date,
                chat_query=chat,
                include_overviews=True,
            )
        )
    export_paths = _filter_export_paths_by_range(
        export_paths,
        start_date=start_date,
        end_date=end_date,
    )

    text_matches = find_text_matches(export_paths, query)
    matched_paths = [item.path for item in text_matches[: max(limit, 1) * 2]]
    export_sections = _analysis_sections_to_prompt_sections(
        collect_analysis_sections(
            matched_paths,
            include_linked_docs=True,
            max_total_chars=28000,
            max_source_chars=1800,
            max_linked_doc_chars=1800,
            max_linked_docs=max(limit, 1),
        )
    ) if matched_paths else []

    own_client = client is None
    if own_client:
        client = _open_client(args, quiet=True)
    try:
        semantic_sections = _semantic_sections_from_hits(client, query, args.target, limit)
    finally:
        if own_client:
            client.close()

    semantic_sections = [
        section
        for section in semantic_sections
        if _section_matches_range(section, date=date, start_date=start_date, end_date=end_date)
    ]

    merged: List[PromptSection] = []
    seen_sources: set[str] = set()
    for section in [*semantic_sections, *export_sections]:
        if section.source in seen_sources:
            continue
        seen_sources.add(section.source)
        merged.append(section)
    return merged


def _persist_report(
    args: argparse.Namespace,
    *,
    text: str,
    report_kind: str,
    slug: str,
    reason: str,
    instruction: str,
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[Path, str | None]:
    report_root = Path(args.report_root).expanduser().resolve() / report_kind
    report_root.mkdir(parents=True, exist_ok=True)
    filename = f"{_report_stamp(date=date, start_date=start_date, end_date=end_date)}--{_slugify_filename(slug)}.md"
    report_path = report_root / filename
    report_path.write_text(text.rstrip() + "\n", encoding="utf-8")
    print(f"[wechat-archive-agent] saved report: {report_path}", file=sys.stderr)

    if not getattr(args, "sync", True):
        return report_path, None

    indexed_target = None
    client = _open_client(args, quiet=True)
    try:
        result = client.add_resource(
            path=str(report_path),
            to=args.report_target,
            reason=reason,
            instruction=instruction,
            wait=False,
        )
        indexed_target = result.get("root_uri") or args.report_target
        print(f"[wechat-archive-agent] indexed report: {indexed_target}", file=sys.stderr)
    except Exception as exc:
        print(f"[wechat-archive-agent] failed to index derived report: {exc}", file=sys.stderr)
    finally:
        client.close()
    return report_path, indexed_target


def _parse_watchlist_topic(line: str) -> WatchlistTopic | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    for separator in ("=>", "|", ":"):
        if separator not in stripped:
            continue
        left, right = [part.strip() for part in stripped.split(separator, 1)]
        if left and right:
            return WatchlistTopic(label=left, query=right)
    return WatchlistTopic(label=stripped, query=stripped)


def _load_watchlist_topics(values: Sequence[str], watchlist_file: str) -> List[WatchlistTopic]:
    raw_items = list(values)
    if not raw_items:
        path = Path(watchlist_file).expanduser()
        if not path.exists():
            raise SystemExit(
                f"Watchlist file not found: {path}\n"
                "Pass topics explicitly or create the file with one topic per line."
            )
        raw_items = path.read_text(encoding="utf-8").splitlines()

    topics: List[WatchlistTopic] = []
    seen_queries: set[str] = set()
    for raw in raw_items:
        topic = _parse_watchlist_topic(raw)
        if topic is None:
            continue
        key = _normalize_text(topic.query)
        if key in seen_queries:
            continue
        seen_queries.add(key)
        topics.append(topic)

    if not topics:
        raise SystemExit("No watchlist topics found. Provide topics explicitly or fill the watchlist file.")
    return topics


def _build_watchlist_prompt_sections(
    topic_rows: Sequence[tuple[WatchlistTopic, Sequence[PromptSection]]],
    *,
    limit_per_topic: int,
) -> List[PromptSection]:
    sections: List[PromptSection] = []
    for topic, matches in topic_rows:
        lines = [
            f"# Watchlist {topic.label}",
            "",
            f"- query: {topic.query}",
            f"- matched_sections: {len(matches)}",
            "",
            "## Signals",
            "",
        ]
        if matches:
            for section in matches[:limit_per_topic]:
                snippet = first_matching_line(section.text, topic.query.lower())
                if not snippet:
                    snippet = next((line.strip() for line in section.text.splitlines() if line.strip()), "")
                lines.append(f"- {section.title} | {_clip_text(snippet or section.text, 180)}")
        else:
            lines.append("- no strong matching section in the current scope")
        sections.append(
            PromptSection(
                title=f"Watchlist {topic.label}",
                source=f"virtual://watchlist/{_slugify_filename(topic.label)}",
                text="\n".join(lines),
            )
        )
    return sections


def _fallback_watchlist_report(
    title: str,
    *,
    topic_rows: Sequence[tuple[WatchlistTopic, Sequence[PromptSection]]],
    range_text: str,
) -> str:
    ordered_rows = sorted(topic_rows, key=lambda item: len(item[1]), reverse=True)
    lines = [
        f"# {title}",
        "",
        f"- range: {range_text}",
        f"- topics: {len(topic_rows)}",
        "",
        "## 强提醒",
        "",
    ]
    strong_rows = [item for item in ordered_rows if item[1]]
    if strong_rows:
        for topic, sections in strong_rows[:8]:
            snippet = first_matching_line(sections[0].text, topic.query.lower())
            snippet = _clip_text(snippet or sections[0].text, 160)
            lines.append(f"- {topic.label} | hits={len(sections)} | {snippet}")
    else:
        lines.append("- 当前范围内没有检测到明显强信号。")

    weak_rows = [item for item in ordered_rows if not item[1]]
    if weak_rows:
        lines.extend(["", "## 弱提醒", ""])
        for topic, _ in weak_rows[:8]:
            lines.append(f"- {topic.label} | 当前范围内未命中明显相关内容")

    lines.extend(["", "## 来源", ""])
    for topic, sections in ordered_rows[:8]:
        if sections:
            lines.append(f"- {topic.label} | {sections[0].title}")
        else:
            lines.append(f"- {topic.label} | no matching source")
    return "\n".join(lines) + "\n"


def run_topic_memory_card(args: argparse.Namespace) -> int:
    _apply_runtime_config(args, quiet=True)
    range_text = _range_label(date=args.date, start_date=args.start_date, end_date=args.end_date)
    sections = _collect_topic_prompt_sections(
        args,
        query=args.query,
        limit=args.limit,
        date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
        chat=args.chat,
    )
    title = f"主题记忆卡：{args.query}"
    task = (
        f"Build a durable topic memory card for `{args.query}` using WeChat archive sources in range {range_text}. "
        "Focus on stable signals, recurring sources, concrete entities, and what to watch next."
    )
    if args.chat:
        task += f" Limit the analysis to chat filter `{args.chat}` when possible."
    report = _run_markdown_analysis(
        title=title,
        task=task,
        sections=sections,
        query=args.query,
        prompt_rules=[
            "Treat this as a durable topic memory card, not a one-off news recap.",
            "Structure the answer as: one short summary paragraph, then `## 核心判断`, then `## 近期信号`, then `## 关键来源`, then `## 后续追踪`.",
            "Under `## 核心判断`, keep 3-5 flat bullet points describing the most durable takeaways.",
            "Under `## 近期信号`, keep 4-8 flat bullet points and mention concrete companies, products, events, or policies when present.",
            "Under `## 关键来源`, mention the most informative chats,公众号, or linked articles.",
            "Under `## 后续追踪`, list what to watch next and what evidence would change the conclusion.",
        ],
        **_analysis_kwargs(args),
    )
    _emit_output(report, args.output)
    _persist_report(
        args,
        text=report,
        report_kind="topic-memory-card",
        slug=args.slug or args.query,
        reason=f"Derived topic memory card for {args.query}",
        instruction=(
            "Keep this as a durable derived report for future WeChat archive retrieval. "
            "It summarizes recurring signals, key sources, and follow-up items for one topic."
        ),
        date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    return 0


def run_watchlist_alerts(args: argparse.Namespace) -> int:
    _apply_runtime_config(args, quiet=True)
    topics = _load_watchlist_topics(args.topics, args.watchlist_file)
    range_text = _range_label(date=args.date, start_date=args.start_date, end_date=args.end_date)
    export_root = _ensure_export_root(args.export_root)
    export_paths = list(
        iter_export_markdown_files(
            export_root,
            date=args.date,
            chat_query=args.chat,
            include_overviews=True,
        )
    )

    topic_rows: List[tuple[WatchlistTopic, Sequence[PromptSection]]] = []
    client = _open_client(args, quiet=True)
    try:
        for topic in topics:
            matches = _collect_topic_prompt_sections(
                args,
                query=topic.query,
                limit=args.limit_per_topic,
                date=args.date,
                start_date=args.start_date,
                end_date=args.end_date,
                chat=args.chat,
                export_paths=export_paths,
                client=client,
            )
            topic_rows.append((topic, matches))
    finally:
        client.close()

    title = f"Watchlist 提醒 {range_text}"
    sections = _build_watchlist_prompt_sections(topic_rows, limit_per_topic=args.limit_per_topic)
    task = (
        f"Generate a watchlist alert digest for WeChat archive range {range_text}. "
        "Judge which tracked topics have the strongest signal, which are weak or absent, and what follow-up is worth doing next."
    )
    if args.chat:
        task += f" Limit the digest to chat filter `{args.chat}` when possible."
    report = _run_markdown_analysis(
        title=title,
        task=task,
        sections=sections,
        prompt_rules=[
            "Treat this as a watchlist alert digest for tracked topics.",
            "Structure the answer as: one short summary paragraph, then `## 强提醒`, then `## 弱提醒`, then `## 建议跟进`, then `## 来源`.",
            "Under `## 强提醒`, rank topics by signal strength and explain why they matter now.",
            "Under `## 弱提醒`, explicitly mention tracked topics with little or no signal in the current range.",
            "Under `## 建议跟进`, suggest what to monitor next for the strongest topics.",
            "Use the provided topic labels exactly as written when possible.",
        ],
        **_analysis_kwargs(args),
        fallback_builder=lambda: _fallback_watchlist_report(
            title,
            topic_rows=topic_rows,
            range_text=range_text,
        ),
    )
    _emit_output(report, args.output)
    _persist_report(
        args,
        text=report,
        report_kind="watchlist-alerts",
        slug="watchlist",
        reason=f"Derived watchlist alert digest for {range_text}",
        instruction=(
            "Keep this as a derived watchlist report for future archive retrieval. "
            "It captures which tracked topics are active, weak, or worth following up."
        ),
        date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "index":
        return run_index(args)
    if args.command == "search":
        return run_search(args)
    if args.command == "daily-summary":
        return run_daily_summary(args)
    if args.command == "chat-summary":
        return run_chat_summary(args)
    if args.command == "topic-report":
        return run_topic_report(args)
    if args.command == "hotspots":
        return run_hotspots(args)
    if args.command == "compare-days":
        return run_compare_days(args)
    if args.command == "timeline-report":
        return run_timeline_report(args)
    if args.command == "sender-report":
        return run_sender_report(args)
    if args.command == "top-articles":
        return run_top_articles(args)
    if args.command == "topic-memory-card":
        return run_topic_memory_card(args)
    if args.command == "watchlist-alerts":
        return run_watchlist_alerts(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
