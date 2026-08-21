# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resolve filesystem resources into the exact inputs used by vector indexing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from openviking.core.context import ContextLevel, ResourceContentType
from openviking.server.identity import RequestContext
from openviking.storage.index_digest import source_digest
from openviking.utils.embedding_input import truncate_embedding_input
from openviking.utils.embedding_utils import get_resource_content_type
from openviking_cli.utils import VikingURI
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.embedding_config import SUMMARY_TEXT_SOURCES

ABSTRACT_NOT_READY_SUFFIX = "[Directory abstract is not ready]"
OVERVIEW_NOT_READY_SUFFIX = "[Directory overview is not ready]"


class SourceState(str, Enum):
    """A source read result that keeps absence separate from failure."""

    FOUND = "found"
    ABSENT = "absent"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class SourceRead:
    """Text source read result."""

    state: SourceState
    text: str = ""
    reason_code: str | None = None


@dataclass(frozen=True)
class IndexSourceFact:
    """One filesystem fact expected to have a vector record."""

    uri: str
    rel_path: str
    level: int
    vector_text: str

    @property
    def digest(self) -> str:
        return source_digest(self.vector_text)

    @property
    def key(self) -> tuple[str, int]:
        return self.uri, self.level


@dataclass(frozen=True)
class UnresolvedIndexSource:
    """An expected source whose contents could not be verified safely."""

    uri: str
    rel_path: str
    level: int
    reason_code: str


def is_not_ready_sentinel(text: str, suffix: str) -> bool:
    """Return whether text is exactly a VikingFS directory placeholder."""
    if not text:
        return False
    head = text.rstrip()
    if not head.endswith(suffix):
        return False
    head = head[: -len(suffix)].strip()
    return head.startswith("#") and "\n" not in head


async def read_text_source(viking_fs: Any, uri: str, ctx: RequestContext) -> SourceRead:
    """Read UTF-8 text without collapsing backend errors into absence."""
    try:
        if not await viking_fs.exists(uri, ctx=ctx):
            return SourceRead(SourceState.ABSENT)
        value = await viking_fs.read_file(uri, ctx=ctx)
    except Exception:
        return SourceRead(SourceState.UNREADABLE, reason_code="source_read_failed")
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            return SourceRead(SourceState.UNREADABLE, reason_code="source_not_utf8")
    else:
        text = str(value or "")
    return SourceRead(SourceState.FOUND, text=text)


async def directory_source(
    viking_fs: Any,
    uri: str,
    level: int,
    ctx: RequestContext,
) -> SourceRead:
    """Resolve a directory L0/L1 source, including the established L1 fallback."""
    abstract = await read_text_source(viking_fs, f"{uri}/.abstract.md", ctx)
    if abstract.state == SourceState.FOUND and is_not_ready_sentinel(
        abstract.text, ABSTRACT_NOT_READY_SUFFIX
    ):
        abstract = SourceRead(SourceState.ABSENT)
    if level == int(ContextLevel.ABSTRACT):
        return abstract

    overview = await read_text_source(viking_fs, f"{uri}/.overview.md", ctx)
    if overview.state == SourceState.FOUND and is_not_ready_sentinel(
        overview.text, OVERVIEW_NOT_READY_SUFFIX
    ):
        overview = SourceRead(SourceState.ABSENT)
    if overview.state == SourceState.UNREADABLE:
        return overview
    if overview.state == SourceState.FOUND and overview.text:
        return overview
    return abstract


def parse_overview(content: str) -> dict[str, str]:
    """Parse per-file summaries from a directory overview document."""
    parsed: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in (content or "").splitlines():
        if line.startswith("## "):
            if current_name is not None:
                parsed[current_name] = "\n".join(current_lines).strip()
            current_name = line[3:].strip()
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        parsed[current_name] = "\n".join(current_lines).strip()
    return parsed


async def file_summary(viking_fs: Any, uri: str, ctx: RequestContext) -> SourceRead:
    """Resolve the filesystem-owned summary for a resource file."""
    parent_uri = VikingURI(uri).parent.uri
    overview = await read_text_source(viking_fs, f"{parent_uri}/.overview.md", ctx)
    if overview.state != SourceState.FOUND:
        return overview
    summary = parse_overview(overview.text).get(uri.rsplit("/", 1)[-1], "")
    return SourceRead(SourceState.FOUND if summary else SourceState.ABSENT, summary)


async def resource_file_source(viking_fs: Any, uri: str, ctx: RequestContext) -> SourceRead:
    """Resolve the final filesystem-owned L2 vector text for a resource."""
    summary = await file_summary(viking_fs, uri, ctx)
    content_type = get_resource_content_type(uri.rsplit("/", 1)[-1])
    if content_type != ResourceContentType.TEXT:
        return summary

    embedding_config = get_openviking_config().embedding
    if summary_source_selected(embedding_config):
        if summary.state == SourceState.UNREADABLE:
            return summary
        if summary.text:
            return summary

    content = await read_text_source(viking_fs, uri, ctx)
    if content.state == SourceState.UNREADABLE:
        return content
    if content.text:
        return SourceRead(
            SourceState.FOUND, select_resource_file_vector_text(content.text, summary.text)
        )
    return summary


def select_resource_file_vector_text(
    content: str,
    summary: str,
    fallback: str = "",
    embedding_config: Any | None = None,
) -> str:
    """Select and truncate L2 vector text using the active embedding policy."""
    embedding_config = embedding_config or get_openviking_config().embedding
    if summary_source_selected(embedding_config) and summary:
        return summary
    if content:
        return truncate_embedding_input(content, embedding_config.max_input_tokens)
    return summary or fallback


def summary_source_selected(embedding_config: Any | None = None) -> bool:
    """Return whether the embedding policy prefers a generated summary."""
    embedding_config = embedding_config or get_openviking_config().embedding
    return embedding_config.text_source in SUMMARY_TEXT_SOURCES


def _entry_uri(root_uri: str, entry: dict[str, Any]) -> str:
    uri = entry.get("uri")
    if isinstance(uri, str) and uri:
        return uri
    return VikingURI(root_uri).join(str(entry.get("rel_path") or "")).uri


async def build_index_sources(
    viking_fs: Any,
    root_uri: str,
    entries: list[dict[str, Any]],
    ctx: RequestContext,
) -> tuple[tuple[IndexSourceFact, ...], tuple[UnresolvedIndexSource, ...]]:
    """Build all verifiable resource L0/L1/L2 facts under a tree."""
    directories: list[tuple[str, str]] = [(root_uri, "")]
    files: list[tuple[str, str]] = []
    for entry in entries:
        uri = _entry_uri(root_uri, entry)
        rel_path = str(entry.get("rel_path") or "")
        name = str(entry.get("name") or uri.rsplit("/", 1)[-1])
        if entry.get("isDir"):
            directories.append((uri, rel_path))
        elif not name.startswith(".") and entry.get("size") != 0:
            files.append((uri, rel_path))

    facts: list[IndexSourceFact] = []
    unresolved: list[UnresolvedIndexSource] = []
    for uri, rel_path in sorted(set(directories)):
        for level in (int(ContextLevel.ABSTRACT), int(ContextLevel.OVERVIEW)):
            source = await directory_source(viking_fs, uri, level, ctx)
            if source.state == SourceState.UNREADABLE:
                unresolved.append(
                    UnresolvedIndexSource(
                        uri, rel_path, level, source.reason_code or "source_read_failed"
                    )
                )
            elif source.state == SourceState.FOUND and source.text:
                facts.append(IndexSourceFact(uri, rel_path, level, source.text))

    for uri, rel_path in sorted(set(files)):
        source = await resource_file_source(viking_fs, uri, ctx)
        if source.state == SourceState.UNREADABLE:
            unresolved.append(
                UnresolvedIndexSource(
                    uri,
                    rel_path,
                    int(ContextLevel.DETAIL),
                    source.reason_code or "source_read_failed",
                )
            )
        elif source.state == SourceState.FOUND and source.text:
            facts.append(IndexSourceFact(uri, rel_path, int(ContextLevel.DETAIL), source.text))

    return (
        tuple(sorted(facts, key=lambda item: (item.uri, item.level))),
        tuple(sorted(unresolved, key=lambda item: (item.uri, item.level))),
    )
