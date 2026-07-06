# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Type-quota memory recall helpers.

This module centralizes the recall strategy previously embedded in VikingBot:
search memory subtrees independently by type, then render a bounded context
block that degrades from full content to summary to URI-only entries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from openviking.core.namespace import canonical_user_root
from openviking.server.identity import RequestContext

TYPE_ORDER = ("events", "entities", "preferences", "experiences")
DEFAULT_QUOTAS = {"events": 10, "entities": 10, "preferences": 3, "experiences": 0}
DEFAULT_MAX_CHARS = 6500
DEFAULT_MIN_SCORE = 0.1
EVENTS_BUDGET_RATIO = 0.75
PREFERENCE_FULL_LIMIT = 3


@dataclass
class RecallEntry:
    uri: str
    score: float
    type: str
    mode: str
    content: str = ""
    summary: str = ""
    rank: int = 0
    abstract: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "uri": self.uri,
            "score": self.score,
            "type": self.type,
            "mode": self.mode,
            "rank": self.rank,
        }
        if self.content:
            data["content"] = self.content
        if self.summary:
            data["summary"] = self.summary
        if self.abstract:
            data["abstract"] = self.abstract
        return data


@dataclass
class RecallResult:
    entries: list[RecallEntry] = field(default_factory=list)
    rendered: str = ""
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "rendered": self.rendered,
            "stats": self.stats,
        }


def normalize_quotas(quotas: Mapping[str, Any] | None) -> dict[str, int]:
    merged = {**DEFAULT_QUOTAS}
    for key, value in (quotas or {}).items():
        if key not in DEFAULT_QUOTAS:
            continue
        try:
            merged[key] = max(0, int(value))
        except (TypeError, ValueError):
            merged[key] = 0
    return merged


def memory_target_roots(ctx: RequestContext) -> list[str]:
    user_root = canonical_user_root(ctx)
    targets = [f"{user_root}/memories"]
    if ctx.actor_peer_id:
        targets.append(f"{user_root}/peers/{ctx.actor_peer_id}/memories")
    return targets


def _type_target(root: str, memory_type: str) -> str:
    return f"{root.rstrip('/')}/{memory_type}"


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _uri(item: Any) -> str:
    return str(_get_attr(item, "uri", "") or "")


def _score(item: Any) -> float:
    try:
        return float(_get_attr(item, "score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _abstract(item: Any) -> str:
    return str(_get_attr(item, "abstract", "") or _get_attr(item, "overview", "") or "")


def _extract_memories(result: Any) -> list[Any]:
    if result is None:
        return []
    if isinstance(result, Mapping):
        return list(result.get("memories") or [])
    return list(getattr(result, "memories", []) or [])


def _dedupe(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    out: list[Any] = []
    for item in items:
        uri = _uri(item)
        if not uri or uri in seen:
            continue
        seen.add(uri)
        out.append(item)
    return out


def _limit(items: list[Any], limit: int) -> list[Any]:
    return sorted(items, key=_score, reverse=True)[: max(0, limit)]


def _filename_from_uri(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1] if uri else ""


def _extract_event_summary(content: str, fallback: str = "") -> str:
    if content:
        match = re.search(
            r"(?is)^\s*Summary:\s*(.*?)(?:\n\s*\d{4}-\d{2}-\d{2}"
            r"(?:\s*\([^)]+\))?\s*ChatLog:|\n\s*ChatLog:|\n\s*<!--\s*MEMORY_FIELDS|$)",
            content,
        )
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return fallback.strip()


def type_char_budgets(max_chars: int) -> dict[str, int]:
    max_chars = max(1, int(max_chars))
    return {
        "events": int(max_chars * EVENTS_BUDGET_RATIO),
        "entities": max_chars,
        "preferences": max_chars,
        "experiences": max_chars,
    }


def _full_fragment(index: int, uri: str, score: float, content: str) -> str:
    return (
        f'<memory index="{index}" type="full">\n'
        f"  <uri>{uri}</uri>\n"
        f"  <filename>{_filename_from_uri(uri)}</filename>\n"
        f"  <score>{score}</score>\n"
        f"  <content>{content}</content>\n"
        f"</memory>"
    )


def _summary_fragment(index: int, uri: str, score: float, summary: str) -> str:
    return (
        f'<memory index="{index}" type="summary">\n'
        f"  <uri>{uri}</uri>\n"
        f"  <filename>{_filename_from_uri(uri)}</filename>\n"
        f"  <score>{score}</score>\n"
        f"  <summary>{summary}</summary>\n"
        f"</memory>"
    )


def _uri_fragment(index: int, uri: str, score: float) -> str:
    return (
        f'<memory index="{index}" type="uri">\n'
        f"  <uri>{uri}</uri>\n"
        f"  <filename>{_filename_from_uri(uri)}</filename>\n"
        f"  <score>{score}</score>\n"
        f"</memory>"
    )


def _group_fragment(memory_type: str, fragments: list[str]) -> str:
    return (
        f'<memory_group type="{memory_type}" count="{len(fragments)}">\n'
        + "\n".join(fragments)
        + "\n</memory_group>"
    )


async def search_type_quota_recall(
    *,
    service: Any,
    ctx: RequestContext,
    query: str,
    quotas: Mapping[str, Any] | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_score: float = DEFAULT_MIN_SCORE,
    render: bool = True,
) -> RecallResult:
    normalized_quotas = normalize_quotas(quotas)
    roots = memory_target_roots(ctx)
    raw_by_type: dict[str, list[Any]] = {}
    selected: list[tuple[str, Any, int]] = []

    for memory_type in TYPE_ORDER:
        quota = normalized_quotas.get(memory_type, 0)
        if quota <= 0:
            raw_by_type[memory_type] = []
            continue
        found: list[Any] = []
        for root in roots:
            result = await service.search.find(
                query=query,
                ctx=ctx,
                target_uri=_type_target(root, memory_type),
                limit=quota,
                score_threshold=min_score,
                level=None,
            )
            found.extend(_extract_memories(result))
        found = _limit(_dedupe(found), quota)
        raw_by_type[memory_type] = found
        selected.extend((memory_type, item, rank) for rank, item in enumerate(found, start=1))

    entries: list[RecallEntry] = []
    fragments_by_type: dict[str, list[str]] = {key: [] for key in TYPE_ORDER}
    budgets = type_char_budgets(max_chars)
    used_by_type = dict.fromkeys(TYPE_ORDER, 0)
    total_chars = 0
    preference_full_count = 0
    dropped = 0
    seen_content: set[int] = set()

    for index, (memory_type, item, rank) in enumerate(selected, start=1):
        uri = _uri(item)
        if not uri or uri.rstrip("/").endswith("/profile.md"):
            continue
        score = _score(item)
        abstract = _abstract(item)
        content = ""
        try:
            content = await service.fs.read(uri, ctx=ctx)
        except Exception:
            content = ""

        content_key = content or abstract or uri
        if content_key:
            content_hash = hash(content_key)
            if content_hash in seen_content:
                continue
            seen_content.add(content_hash)

        mode = "uri"
        summary = ""
        entry_content = ""
        fragment = _uri_fragment(index, uri, score)

        if content:
            full = _full_fragment(index, uri, score, content)
            full_chars = len(full) + (1 if total_chars else 0)
            can_try_full = memory_type in budgets
            if memory_type == "preferences":
                can_try_full = preference_full_count < PREFERENCE_FULL_LIMIT
                preference_full_count += 1
            if (
                can_try_full
                and used_by_type.get(memory_type, 0) + full_chars
                <= budgets.get(memory_type, max_chars)
                and total_chars + full_chars <= max_chars
            ):
                mode = "full"
                entry_content = content
                fragment = full
                used_by_type[memory_type] = used_by_type.get(memory_type, 0) + full_chars
                total_chars += full_chars
            elif memory_type == "events":
                summary = _extract_event_summary(content, fallback=abstract)
                if summary:
                    mode = "summary"
                    fragment = _summary_fragment(index, uri, score, summary)
            elif abstract:
                summary = abstract

        # VikingBot's heuristic only budgets full fragments, but max_chars is
        # this API's contract: every rendered fragment counts. Fallbacks keep
        # degrading (summary -> uri) and drop entirely once nothing fits.
        if mode != "full":
            fragment_chars = len(fragment) + (1 if total_chars else 0)
            if total_chars + fragment_chars > max_chars and mode == "summary":
                mode = "uri"
                fragment = _uri_fragment(index, uri, score)
                fragment_chars = len(fragment) + (1 if total_chars else 0)
            if total_chars + fragment_chars > max_chars:
                dropped += 1
                continue
            total_chars += fragment_chars

        entries.append(
            RecallEntry(
                uri=uri,
                score=score,
                type=memory_type,
                mode=mode,
                content=entry_content,
                summary=summary,
                rank=rank,
                abstract=abstract,
            )
        )
        fragments_by_type.setdefault(memory_type, []).append(fragment)

    rendered = ""
    if render:
        rendered_groups = [
            _group_fragment(memory_type, fragments_by_type[memory_type])
            for memory_type in TYPE_ORDER
            if fragments_by_type.get(memory_type)
        ]
        rendered = "\n".join(rendered_groups)

    return RecallResult(
        entries=entries,
        rendered=rendered,
        stats={
            "quotas": normalized_quotas,
            "roots": roots,
            "searched": {key: len(value) for key, value in raw_by_type.items()},
            "returned": len(entries),
            "dropped": dropped,
            "max_chars": max_chars,
            "min_score": min_score,
        },
    )
