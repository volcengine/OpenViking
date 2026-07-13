# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Type-quota memory recall helpers.

This module centralizes the recall strategy previously embedded in VikingBot:
search memory subtrees independently by type, then render a bounded context
block that degrades from full content to summary to URI-only entries.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from openviking.core.namespace import canonical_user_root
from openviking.server.identity import RequestContext

TYPE_ORDER = ("events", "entities", "preferences", "experiences")
DEFAULT_QUOTAS = {"events": 10, "entities": 10, "preferences": 3, "experiences": 0}
DEFAULT_OTHER_PEER_PENALTIES = {
    "events": 0.1,
    "entities": 0.1,
    "preferences": 0.02,
    "experiences": 0.02,
}
DEFAULT_MAX_CHARS = 6500
DEFAULT_MIN_SCORE = 0.1
EVENTS_BUDGET_RATIO = 0.75
PREFERENCE_FULL_LIMIT = 3
OTHER_PEER_OVERFETCH = 4
ORIGIN_ORDER = ("actor_peer", "self", "other_peer")
ORIGIN_LABEL = {
    "actor_peer": "current-project",
    "self": "global",
    "other_peer": "other-projects",
}


@dataclass
class RecallEntry:
    uri: str
    score: float
    type: str
    mode: str
    origin: str = ""
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
        if self.origin:
            data["origin"] = self.origin
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


def _clamp_penalty(value: Any, fallback: float) -> float:
    try:
        penalty = float(value)
    except (TypeError, ValueError):
        penalty = fallback
    return min(1.0, max(0.0, penalty))


def normalize_penalties(value: Any = None) -> dict[str, float]:
    """Normalize other-peer recall penalties by memory type."""
    if value is None:
        return dict(DEFAULT_OTHER_PEER_PENALTIES)
    if isinstance(value, Mapping):
        merged = dict(DEFAULT_OTHER_PEER_PENALTIES)
        for key, penalty in value.items():
            if key not in DEFAULT_OTHER_PEER_PENALTIES:
                continue
            merged[key] = _clamp_penalty(penalty, merged[key])
        return merged
    penalty = _clamp_penalty(value, 0.0)
    return dict.fromkeys(TYPE_ORDER, penalty)


def memory_target_roots(ctx: RequestContext) -> list[str]:
    user_root = canonical_user_root(ctx)
    targets = [f"{user_root}/memories"]
    if ctx.actor_peer_id:
        targets.append(f"{user_root}/peers/{ctx.actor_peer_id}/memories")
    return targets


def _type_target(root: str, memory_type: str) -> str:
    return f"{root.rstrip('/')}/{memory_type}"


def _is_under(uri: str, root: str) -> bool:
    uri = uri.rstrip("/")
    root = root.rstrip("/")
    return uri == root or uri.startswith(f"{root}/")


def _origin_for_uri(uri: str, actor_peer_id: str | None, user_root: str) -> str:
    peers_root = f"{user_root.rstrip('/')}/peers"
    if _is_under(uri, peers_root):
        suffix = uri[len(peers_root) :].strip("/")
        peer_id = suffix.split("/", 1)[0] if suffix else ""
        if actor_peer_id and peer_id == actor_peer_id:
            return "actor_peer"
        return "other_peer"
    return "self"


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


def _limit_with_peer_penalties(
    items: list[Any],
    *,
    memory_type: str,
    limit: int,
    penalties: Mapping[str, float],
    actor_peer_id: str | None,
    user_root: str,
) -> list[tuple[Any, str]]:
    origin_rank = {origin: index for index, origin in enumerate(ORIGIN_ORDER)}

    def sort_key(item: Any) -> tuple[float, int]:
        origin = _origin_for_uri(_uri(item), actor_peer_id, user_root)
        penalty = penalties.get(memory_type, 0.0) if origin == "other_peer" else 0.0
        return (_score(item) - penalty, -origin_rank.get(origin, len(origin_rank)))

    limited = sorted(items, key=sort_key, reverse=True)[: max(0, limit)]
    return [(item, _origin_for_uri(_uri(item), actor_peer_id, user_root)) for item in limited]


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


def _section_fragment(origin: str, groups: list[str]) -> str:
    return (
        f'<memory_section source="{ORIGIN_LABEL.get(origin, origin)}">\n'
        + "\n".join(groups)
        + "\n</memory_section>"
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
    peer_scope: str = "all",
    other_peer_penalty: Any = None,
) -> RecallResult:
    normalized_quotas = normalize_quotas(quotas)
    normalized_penalties = normalize_penalties(other_peer_penalty)
    peer_scope = "actor" if peer_scope == "actor" else "all"
    user_root = canonical_user_root(ctx)
    roots = memory_target_roots(ctx)
    open_ctx = replace(ctx, actor_peer_id=None, legacy_agent_id=None)
    raw_by_type: dict[str, list[Any]] = {memory_type: [] for memory_type in TYPE_ORDER}
    selected: list[tuple[str, Any, int, str, RequestContext]] = []

    async def search_type(memory_type: str, quota: int) -> list[Any]:
        searches = [
            service.search.find(
                query=query,
                ctx=ctx,
                target_uri=_type_target(root, memory_type),
                limit=quota,
                score_threshold=min_score,
                level=None,
            )
            for root in roots
        ]
        if peer_scope == "all":
            searches.append(
                service.search.find(
                    query=query,
                    ctx=open_ctx,
                    target_uri=f"{user_root}/peers",
                    limit=max(quota * OTHER_PEER_OVERFETCH, quota),
                    score_threshold=min_score,
                    level=None,
                )
            )

        results = await asyncio.gather(*searches)
        found = [item for result in results[: len(roots)] for item in _extract_memories(result)]
        if peer_scope == "all":
            found.extend(
                item
                for item in _extract_memories(results[-1])
                if f"/memories/{memory_type}/" in _uri(item)
                and _origin_for_uri(_uri(item), ctx.actor_peer_id, user_root) == "other_peer"
            )
        return found

    active_types = [
        (memory_type, normalized_quotas[memory_type])
        for memory_type in TYPE_ORDER
        if normalized_quotas[memory_type] > 0
    ]
    found_by_type = await asyncio.gather(
        *(search_type(memory_type, quota) for memory_type, quota in active_types)
    )

    for (memory_type, quota), found in zip(active_types, found_by_type, strict=True):
        if peer_scope == "all":
            found = _dedupe(found)
            raw_by_type[memory_type] = found
            ranked = _limit_with_peer_penalties(
                found,
                memory_type=memory_type,
                limit=quota,
                penalties=normalized_penalties,
                actor_peer_id=ctx.actor_peer_id,
                user_root=user_root,
            )
        else:
            found = _limit(_dedupe(found), quota)
            raw_by_type[memory_type] = found
            ranked = [
                (item, _origin_for_uri(_uri(item), ctx.actor_peer_id, user_root)) for item in found
            ]

        selected.extend(
            (
                memory_type,
                item,
                rank,
                origin,
                open_ctx if origin == "other_peer" else ctx,
            )
            for rank, (item, origin) in enumerate(ranked, start=1)
        )

    entries: list[RecallEntry] = []
    fragments_by_type: dict[str, list[str]] = {key: [] for key in TYPE_ORDER}
    fragments_by_origin_type: dict[tuple[str, str], list[str]] = {}
    budgets = type_char_budgets(max_chars)
    used_by_type = dict.fromkeys(TYPE_ORDER, 0)
    total_chars = 0
    preference_full_count = 0
    dropped = 0
    seen_content: set[int] = set()

    for index, (memory_type, item, rank, origin, read_ctx) in enumerate(selected, start=1):
        uri = _uri(item)
        if not uri or uri.rstrip("/").endswith("/profile.md"):
            continue
        score = _score(item)
        abstract = _abstract(item)
        content = ""
        try:
            content = await service.fs.read(uri, ctx=read_ctx)
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
                origin=origin,
                content=entry_content,
                summary=summary,
                rank=rank,
                abstract=abstract,
            )
        )
        fragments_by_type.setdefault(memory_type, []).append(fragment)
        fragments_by_origin_type.setdefault((origin, memory_type), []).append(fragment)

    rendered = ""
    if render:
        if peer_scope == "actor":
            rendered_groups = [
                _group_fragment(memory_type, fragments_by_type[memory_type])
                for memory_type in TYPE_ORDER
                if fragments_by_type.get(memory_type)
            ]
            rendered = "\n".join(rendered_groups)
        else:
            rendered_sections: list[str] = []
            for origin in ORIGIN_ORDER:
                groups = [
                    _group_fragment(memory_type, fragments_by_origin_type[(origin, memory_type)])
                    for memory_type in TYPE_ORDER
                    if fragments_by_origin_type.get((origin, memory_type))
                ]
                if groups:
                    rendered_sections.append(_section_fragment(origin, groups))
            rendered = "\n".join(rendered_sections)

    origins = dict.fromkeys(ORIGIN_ORDER, 0)
    for entry in entries:
        origins[entry.origin] = origins.get(entry.origin, 0) + 1

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
            "peer_scope": peer_scope,
            "other_peer_penalties": normalized_penalties,
            "origins": origins,
        },
    )
