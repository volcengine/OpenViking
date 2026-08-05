# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Candidate gathering for context assembly.

Generalizes the memory-only type-quota fan-out to every context category and
keeps the peer-origin ranking rules that recall v1 established.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from openviking.core.namespace import AGENT_SHARED_ROOTS, canonical_user_root
from openviking.core.retrieval_targets import default_target_directories
from openviking.retrieve.context_assembler.params import (
    CATEGORY_KEYS,
    MEMORY_CATEGORIES,
    ORIGIN_ORDER,
    OTHER_PEER_OVERFETCH,
)
from openviking.server.identity import RequestContext
from openviking.utils.search_filters import merge_context_type_filter
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve import ContextType

LEVEL_SUFFIXES = (".abstract.md", ".overview.md")


@dataclass
class Candidate:
    """One retrieval hit, resolved enough to plan a detail tier for it."""

    uri: str
    base_uri: str
    category: str
    score: float
    ranked_score: float
    level: int
    abstract: str
    origin: str
    is_directory: bool
    read_ctx: RequestContext


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


def _level(item: Any) -> int:
    try:
        return int(_get_attr(item, "level", 2) or 2)
    except (TypeError, ValueError):
        return 2


def _extract(result: Any, bucket: str) -> List[Any]:
    """Pull the list matching ``bucket`` out of a FindResult-shaped value."""
    key = {"resources": "resources", "skills": "skills"}.get(bucket, "memories")
    if result is None:
        return []
    if isinstance(result, Mapping):
        return list(result.get(key) or [])
    return list(getattr(result, key, None) or [])


def memory_target_roots(ctx: RequestContext) -> List[str]:
    user_root = canonical_user_root(ctx)
    targets = [f"{user_root}/memories"]
    if ctx.actor_peer_id:
        targets.append(f"{user_root}/peers/{ctx.actor_peer_id}/memories")
    return targets


def category_targets(category: str, ctx: RequestContext) -> List[str]:
    """Retrieval scopes owning ``category``."""
    user_root = canonical_user_root(ctx)
    if category == "resources":
        targets = default_target_directories(ctx, context_type=ContextType.RESOURCE)
        if targets:
            return targets
        fallback = [f"{user_root}/resources", "viking://resources"]
        if ctx.actor_peer_id:
            fallback.append(f"{user_root}/peers/{ctx.actor_peer_id}/resources")
        return fallback
    if category == "skills":
        return default_target_directories(ctx, context_type=ContextType.SKILL) or [
            f"{user_root}/skills",
            *AGENT_SHARED_ROOTS,
        ]
    return [f"{root.rstrip('/')}/{category}" for root in memory_target_roots(ctx)]


def _is_under(uri: str, root: str) -> bool:
    uri = uri.rstrip("/")
    root = root.rstrip("/")
    return uri == root or uri.startswith(f"{root}/")


def origin_for_uri(uri: str, actor_peer_id: Optional[str], user_root: str) -> str:
    peers_root = f"{user_root.rstrip('/')}/peers"
    if _is_under(uri, peers_root):
        suffix = uri[len(peers_root) :].strip("/")
        peer_id = suffix.split("/", 1)[0] if suffix else ""
        if actor_peer_id and peer_id == actor_peer_id:
            return "actor_peer"
        return "other_peer"
    return "self"


def strip_level_suffix(uri: str) -> Tuple[str, bool]:
    """Return ``(node_uri, is_directory)`` for a possibly sidecar-suffixed URI."""
    trimmed = uri.rstrip("/")
    for suffix in LEVEL_SUFFIXES:
        if trimmed.endswith(f"/{suffix}"):
            return trimmed[: -(len(suffix) + 1)], True
    return trimmed, False


def category_for(item: Any, bucket: Optional[str]) -> str:
    if bucket:
        return bucket
    uri = _uri(item)
    marker = "/memories/"
    if marker in uri:
        segment = uri.split(marker, 1)[1].split("/", 1)[0]
        if segment in MEMORY_CATEGORIES:
            return segment
    declared = str(_get_attr(item, "category", "") or "")
    if declared in CATEGORY_KEYS:
        return declared
    if "/skills/" in uri:
        return "skills"
    if "/resources/" in uri or uri.startswith("viking://resources"):
        return "resources"
    return "memories"


def dedupe_keep_best(items: Sequence[Any]) -> List[Any]:
    """Deduplicate by URI, keeping the highest-scoring occurrence."""
    positions: Dict[str, int] = {}
    out: List[Any] = []
    for item in items:
        uri = _uri(item)
        if not uri:
            continue
        if uri not in positions:
            positions[uri] = len(out)
            out.append(item)
        elif _score(item) > _score(out[positions[uri]]):
            out[positions[uri]] = item
    return out


async def _safe_find(service: Any, errors: List[str], **kwargs: Any) -> Any:
    """Retrieval must never fail the whole assembly for one scope.

    Failures are counted into stats rather than swallowed silently, so an empty
    context block caused by a broken embedder is distinguishable from one caused
    by genuinely having no relevant memories. A rejected request is not such a
    failure: it fails every scope identically and degrading it would hide a
    caller mistake behind a 200 with an empty block, so it propagates and gets
    the same 400 ``mode="list"`` returns.
    """
    try:
        return await service.search.find(**kwargs)
    except InvalidArgumentError:
        raise
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}"[:200])
        return None


async def gather_candidates(
    *,
    service: Any,
    ctx: RequestContext,
    queries: Sequence[str],
    quotas: Optional[Mapping[str, int]],
    limit: int,
    score_threshold: Optional[float],
    filter: Optional[Dict[str, Any]] = None,
    image_url: Optional[str] = None,
    peer_scope: str = "all",
    penalties: Optional[Mapping[str, float]] = None,
    excluded: Optional[Set[str]] = None,
) -> Tuple[List[Candidate], Dict[str, Any]]:
    """Retrieve and rank candidates, returning ``(candidates, stats)``."""
    peer_scope = "actor" if peer_scope == "actor" else "all"
    user_root = canonical_user_root(ctx)
    open_ctx = replace(ctx, actor_peer_id=None)
    penalties = penalties or {}
    excluded = excluded or set()
    planned = [q for q in queries if q] or [""]

    searched: Dict[str, int] = {}
    retrieval_errors: List[str] = []
    excluded_count = 0

    def _build(items: Sequence[Tuple[Any, Optional[str]]]) -> List[Candidate]:
        nonlocal excluded_count
        built: List[Candidate] = []
        for item, bucket in items:
            uri = _uri(item)
            if not uri:
                continue
            base_uri, is_directory = strip_level_suffix(uri)
            if base_uri.endswith("/profile.md"):
                continue
            if uri in excluded or base_uri in excluded:
                excluded_count += 1
                continue
            category = category_for(item, bucket)
            origin = origin_for_uri(base_uri, ctx.actor_peer_id, user_root)
            score = _score(item)
            penalty = penalties.get(category, 0.0) if origin == "other_peer" else 0.0
            built.append(
                Candidate(
                    uri=uri,
                    base_uri=base_uri,
                    category=category,
                    score=score,
                    ranked_score=score - penalty,
                    level=_level(item),
                    abstract=_abstract(item),
                    origin=origin,
                    is_directory=is_directory,
                    read_ctx=open_ctx if origin == "other_peer" else ctx,
                )
            )
        return built

    def _overfetch(want: int) -> int:
        """Rows to request on top of ``want`` to cover post-retrieval filtering.

        Cooled and caller-excluded URIs are dropped after the search returns, so
        without this a bucket whose whole top page is cooled comes back empty
        instead of falling through to the next-best hits.
        """
        return want + min(len(excluded), want * 2)

    def _find(
        *,
        query: str,
        find_ctx: RequestContext,
        target_uri: str,
        find_limit: int,
        find_filter: Optional[Dict[str, Any]] = None,
    ) -> Any:
        return _safe_find(
            service,
            retrieval_errors,
            query=query,
            ctx=find_ctx,
            target_uri=target_uri,
            limit=find_limit,
            score_threshold=score_threshold,
            filter=find_filter if find_filter is not None else filter,
            image_url=image_url,
            level=None,
        )

    async def gather_bucket(bucket: str, quota: int) -> List[Candidate]:
        targets = category_targets(bucket, ctx)
        context_type = {
            "resources": ContextType.RESOURCE,
            "skills": ContextType.SKILL,
        }.get(bucket, ContextType.MEMORY)
        bucket_filter = merge_context_type_filter(filter, context_type)
        searches = [
            _find(
                query=query,
                find_ctx=ctx,
                target_uri=target,
                find_limit=_overfetch(quota),
                find_filter=bucket_filter,
            )
            for query in planned
            for target in targets
        ]
        peer_offset = len(searches)
        if peer_scope == "all" and bucket in MEMORY_CATEGORIES:
            searches.extend(
                _find(
                    query=query,
                    find_ctx=open_ctx,
                    target_uri=f"{user_root}/peers",
                    find_limit=_overfetch(max(quota * OTHER_PEER_OVERFETCH, quota)),
                    find_filter=bucket_filter,
                )
                for query in planned
            )

        results = await asyncio.gather(*searches)
        found: List[Any] = []
        for result in results[:peer_offset]:
            found.extend(_extract(result, bucket))
        for result in results[peer_offset:]:
            found.extend(
                item
                for item in _extract(result, bucket)
                if f"/memories/{bucket}/" in _uri(item)
                and origin_for_uri(_uri(item), ctx.actor_peer_id, user_root) == "other_peer"
            )
        found = dedupe_keep_best(found)
        searched[bucket] = len(found)
        candidates = _build([(item, bucket) for item in found])
        candidates.sort(key=_rank_key, reverse=True)
        return candidates[: max(0, quota)]

    async def gather_flat() -> List[Candidate]:
        searches = [
            _find(
                query=query,
                find_ctx=ctx,
                target_uri="",
                find_limit=_overfetch(limit),
            )
            for query in planned
        ]
        peer_offset = len(searches)
        if peer_scope == "all":
            searches.extend(
                _find(
                    query=query,
                    find_ctx=open_ctx,
                    target_uri=f"{user_root}/peers",
                    find_limit=_overfetch(max(limit * OTHER_PEER_OVERFETCH, limit)),
                )
                for query in planned
            )
        results = await asyncio.gather(*searches)
        # Keep each hit's owning bucket: inferring the category from the URI
        # alone reads viking://resources/backup/memories/events/log.md as an
        # event, which would lift a large resource past its tier ceiling.
        buckets: Dict[str, Optional[str]] = {}
        found: List[Any] = []
        for result in results[:peer_offset]:
            for bucket in ("memories", "resources", "skills"):
                for item in _extract(result, bucket):
                    buckets.setdefault(_uri(item), None if bucket == "memories" else bucket)
                    found.append(item)
        for result in results[peer_offset:]:
            for item in _extract(result, "memories"):
                if origin_for_uri(_uri(item), ctx.actor_peer_id, user_root) != "other_peer":
                    continue
                buckets.setdefault(_uri(item), None)
                found.append(item)
        found = dedupe_keep_best(found)
        searched["all"] = len(found)
        candidates = _build([(item, buckets.get(_uri(item))) for item in found])
        candidates.sort(key=_rank_key, reverse=True)
        return candidates[: max(0, limit)]

    if quotas is None:
        candidates = await gather_flat()
    else:
        active = [(bucket, quota) for bucket, quota in quotas.items() if quota > 0]
        buckets = await asyncio.gather(*(gather_bucket(b, q) for b, q in active))
        candidates = [candidate for bucket in buckets for candidate in bucket]
        candidates = _dedupe_candidates(candidates)
        candidates.sort(key=_rank_key, reverse=True)

    origins = dict.fromkeys(ORIGIN_ORDER, 0)
    for candidate in candidates:
        origins[candidate.origin] = origins.get(candidate.origin, 0) + 1

    stats: Dict[str, Any] = {
        "searched": searched,
        "candidates": len(candidates),
        "excluded": excluded_count,
        "origins": origins,
        "peer_scope": peer_scope,
        "quotas": dict(quotas) if quotas is not None else None,
    }
    if retrieval_errors:
        stats["retrieval_errors"] = retrieval_errors[:5]
    return candidates, stats


def _rank_key(candidate: Candidate) -> Tuple[float, int]:
    origin_rank = {origin: index for index, origin in enumerate(ORIGIN_ORDER)}
    return (candidate.ranked_score, -origin_rank.get(candidate.origin, len(origin_rank)))


def _dedupe_candidates(candidates: Sequence[Candidate]) -> List[Candidate]:
    best: Dict[str, Candidate] = {}
    order: List[str] = []
    for candidate in candidates:
        existing = best.get(candidate.base_uri)
        if existing is None:
            best[candidate.base_uri] = candidate
            order.append(candidate.base_uri)
        elif candidate.score > existing.score:
            best[candidate.base_uri] = candidate
    return [best[key] for key in order]
