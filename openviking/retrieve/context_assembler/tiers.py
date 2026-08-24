# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Detail-tier text resolution.

Every tier always carries the URI; the tiers differ only in how much body text
they spend. ``abstract`` comes from the vector payload and costs no read, while
``overview`` and ``full`` need the node content.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openviking.parse.parsers.code.ast import extract_skeleton_result
from openviking.parse.parsers.code.ast.providers import supports_code_skeleton
from openviking.retrieve.context_assembler.gather import Candidate
from openviking.retrieve.context_assembler.params import (
    DEFAULT_TIER,
    DEFAULT_TIER_BY_CATEGORY,
    DEPTH_CEILING_BY_CATEGORY,
    FULL_BODY_ABSTRACT_CATEGORIES,
    READ_CONCURRENCY,
    TIER_RANK,
    Tier,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.abstract_overview import body_for_preview

OVERVIEW_HEADING_LIMIT = 24
OVERVIEW_PARAGRAPH_CHARS = 400

_SUMMARY_SECTION = re.compile(
    r"^#{1,3}[ \t]*Summary[ \t]*$\n(.*?)(?=^#{1,3}[ \t]|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_SUMMARY_INLINE = re.compile(
    r"^\s*Summary:\s*(.*?)(?:\n\s*\d{4}-\d{2}-\d{2}"
    r"(?:\s*\([^)]+\))?\s*ChatLog:|\n\s*ChatLog:|\n\s*<!--\s*MEMORY_FIELDS|$)",
    re.IGNORECASE | re.DOTALL,
)
_HEADING_LINE = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def filename_from_uri(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1] if uri else ""


def _decode(raw: Any) -> str:
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    return str(raw or "")


def _is_memory_uri(uri: str) -> bool:
    return "/memories/" in uri


def _is_code_uri(uri: str) -> bool:
    try:
        return supports_code_skeleton(uri)
    except Exception:
        return False


def extract_summary_section(content: str) -> str:
    """Leading ``# Summary`` block, tolerating the legacy ``Summary:`` prefix."""
    if not content:
        return ""
    match = _SUMMARY_SECTION.search(content)
    if match:
        return match.group(1).strip()
    inline = _SUMMARY_INLINE.search(content)
    if inline:
        return re.sub(r"\s+", " ", inline.group(1)).strip()
    return ""


def doc_overview(content: str) -> str:
    """Heading tree plus the first paragraph — the generic long-document skeleton."""
    if not content:
        return ""
    lines: List[str] = []
    for match in _HEADING_LINE.finditer(content):
        depth = len(match.group(1)) - 1
        lines.append(f"{'  ' * depth}{'#' * len(match.group(1))} {match.group(2)}")
        if len(lines) >= OVERVIEW_HEADING_LIMIT:
            break

    paragraph = ""
    for block in re.split(r"\n\s*\n", content):
        candidate = "\n".join(
            line for line in block.strip().splitlines() if not line.lstrip().startswith("#")
        ).strip()
        if candidate:
            paragraph = candidate[:OVERVIEW_PARAGRAPH_CHARS]
            break

    parts = [part for part in ("\n".join(lines), paragraph) if part]
    return "\n\n".join(parts).strip()


def overview_from_content(candidate: Candidate, content: str) -> str:
    """Dispatch overview extraction by source type."""
    if not content:
        return ""
    if candidate.is_directory:
        # The cached content already is the directory's own overview sidecar.
        return body_for_preview(content).strip()
    if _is_code_uri(candidate.base_uri):
        extraction = extract_skeleton_result(filename_from_uri(candidate.base_uri), content)
        if extraction.text:
            return extraction.text.strip()
    if _is_memory_uri(candidate.base_uri):
        summary = extract_summary_section(content)
        if summary:
            return summary
    return doc_overview(content)


def content_uri_for(candidate: Candidate) -> str:
    """URI whose body backs the overview/full tiers of ``candidate``."""
    if candidate.is_directory:
        return f"{candidate.base_uri}/.overview.md"
    return candidate.base_uri


async def prefetch_contents(
    *,
    service: Any,
    candidates: Sequence[Candidate],
) -> Dict[str, str]:
    """Read every candidate body once, concurrently, tolerating failures."""
    semaphore = asyncio.Semaphore(READ_CONCURRENCY)
    wanted: List[Candidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        uri = content_uri_for(candidate)
        if uri in seen:
            continue
        seen.add(uri)
        wanted.append(candidate)

    async def read_one(candidate: Candidate) -> tuple[str, str]:
        uri = content_uri_for(candidate)
        try:
            async with semaphore:
                raw = await service.fs.read(uri, ctx=candidate.read_ctx)
        except Exception:
            return uri, ""
        content = _decode(raw)
        if content and _is_memory_uri(uri) and not candidate.is_directory:
            try:
                content = MemoryFileUtils.read(content, uri=uri).content
            except Exception:
                pass
        return uri, content

    pairs = await asyncio.gather(*(read_one(candidate) for candidate in wanted))
    return dict(pairs)


def tier_text(
    candidate: Candidate,
    tier: Tier,
    *,
    contents: Dict[str, str],
) -> Optional[str]:
    """Body text for ``tier``, or ``None`` when that tier is unavailable."""
    if tier == "uri":
        return ""
    if tier == "abstract":
        return candidate.abstract.strip() or None

    content = contents.get(content_uri_for(candidate), "")
    if tier == "overview":
        return overview_from_content(candidate, content) or None
    if tier == "full":
        if candidate.is_directory:
            # A directory's "full" body would be its whole subtree; capped at overview.
            return None
        return content.strip() or None
    return None


def start_tier(candidate: Candidate, pin: Optional[Tier] = None) -> Tier:
    """Tier this candidate is served at before any leftover budget is spent."""
    if candidate.is_directory:
        # A directory has no stored abstract, and its "full" body would be the
        # whole subtree: the .overview.md sidecar is both floor and ceiling.
        return "overview"
    tier = pin or DEFAULT_TIER_BY_CATEGORY.get(candidate.category, DEFAULT_TIER)
    if tier == "abstract" and not candidate.abstract.strip():
        return abstract_substitute(candidate)
    return tier


def abstract_substitute(candidate: Candidate) -> Tier:
    """Tier that stands in when ``abstract`` is unavailable or too expensive.

    Only the full-body-abstract categories can spend ``overview`` here: their
    abstract is the whole body, so the substitute discloses strictly less. A
    resource or skill whose abstract is missing or oversized degrades to a bare
    URI instead — reading its body would cross the opt-in deepening boundary
    that keeps generated summaries, not raw content, on the default path.
    """
    if candidate.category in FULL_BODY_ABSTRACT_CATEGORIES:
        return "overview"
    return "uri"


def tier_window(candidate: Candidate, pin: Optional[Tier] = None) -> Tuple[Tier, Tier]:
    """``(start, ceiling)``: where the candidate starts and how far it may deepen."""
    start = start_tier(candidate, pin)
    if pin is not None or candidate.is_directory:
        return start, start
    ceiling = DEPTH_CEILING_BY_CATEGORY.get(candidate.category, start)
    return start, ceiling if TIER_RANK[ceiling] > TIER_RANK[start] else start


def needs_content(candidate: Candidate, pin: Optional[Tier] = None) -> bool:
    """Whether any tier this candidate can reach requires reading its body."""
    return TIER_RANK[tier_window(candidate, pin)[1]] >= TIER_RANK["overview"]
