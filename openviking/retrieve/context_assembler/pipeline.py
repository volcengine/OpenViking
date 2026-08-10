# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Single-request context assembly: retrieve, budget, render, optionally digest.

One HTTP round trip replaces the per-type search-then-read loops each harness
plugin used to run, so every plugin inherits budgeting, tier degradation and
cross-turn dedup from one implementation.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openviking.retrieve.context_assembler.budget import (
    oversized_abstract_needs_body,
    per_entry_cap,
    plan_entries,
)
from openviking.retrieve.context_assembler.expansion import expand_queries
from openviking.retrieve.context_assembler.gather import Candidate, gather_candidates
from openviking.retrieve.context_assembler.ledger import RecallLedger
from openviking.retrieve.context_assembler.models import AssembleResult
from openviking.retrieve.context_assembler.params import (
    AssembleParams,
    READ_CONCURRENCY,
    normalize_detail,
    normalize_exclude_uris,
    normalize_penalties,
    normalize_quotas,
)
from openviking.retrieve.context_assembler.render import render_context
from openviking.retrieve.context_assembler.rewrite import rewrite_context, server_rewrite_enabled
from openviking.retrieve.context_assembler.tiers import (
    content_uri_for,
    needs_content,
    prefetch_contents,
)
from openviking.server.identity import RequestContext
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.memory_fields import MEMORY_FIELDS_COMMENT_RE
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_EXCLUDED_EXPERIENCE_STATUSES = frozenset({"deprecated", "archived"})


def _decode_raw_memory(raw: Any) -> str:
    if raw is None:
        raise ValueError("authoritative memory read returned no content")
    if isinstance(raw, bytes):
        decoded = raw.decode("utf-8")
    elif isinstance(raw, str):
        decoded = raw
    else:
        decoded = str(raw)
    if not decoded:
        raise ValueError("authoritative memory read returned empty content")
    return decoded


async def _filter_experience_lifecycle(
    *,
    service: Any,
    candidates: Sequence[Candidate],
) -> Tuple[List[Candidate], Dict[str, str], Dict[str, Any]]:
    """Fail closed on non-authoritative Experience lifecycle state.

    Retrieval payloads do not carry policy lifecycle metadata. Every Experience
    candidate therefore needs one raw file read before it can enter budgeting or
    rendering. The parsed body is returned as a cache so an explicitly deepened
    Experience is not read twice.
    """
    experience_candidates = [
        candidate for candidate in candidates if candidate.category == "experiences"
    ]
    stats: Dict[str, Any] = {
        "checked": len(experience_candidates),
        "kept": 0,
        "excluded": 0,
        "read_errors": 0,
        "excluded_by_status": {"deprecated": 0, "archived": 0},
    }
    if not experience_candidates:
        return list(candidates), {}, stats

    semaphore = asyncio.Semaphore(READ_CONCURRENCY)

    async def inspect(candidate: Candidate) -> Tuple[bool, str, str]:
        try:
            async with semaphore:
                raw = await service.fs.read(candidate.base_uri, ctx=candidate.read_ctx)
            decoded = _decode_raw_memory(raw)
            fields_match = MEMORY_FIELDS_COMMENT_RE.search(decoded)
            if fields_match:
                fields = json.loads(fields_match.group("fields").strip())
                if not isinstance(fields, dict):
                    raise ValueError("authoritative memory fields are not an object")
            memory_file = MemoryFileUtils.read(
                decoded,
                uri=candidate.base_uri,
            )
        except Exception as exc:
            logger.info(
                "Experience %s excluded because authoritative lifecycle read failed: %s",
                candidate.base_uri,
                exc,
            )
            return False, "read_error", ""

        status = str((memory_file.extra_fields or {}).get("status") or "").strip().lower()
        if status in _EXCLUDED_EXPERIENCE_STATUSES:
            return False, status, memory_file.content
        # Lifecycle validation already paid for an authoritative read. Use the
        # same snapshot for the default abstract tier instead of a potentially
        # stale vector payload.
        candidate.abstract = memory_file.content
        return True, status, memory_file.content

    inspections = iter(
        await asyncio.gather(*(inspect(candidate) for candidate in experience_candidates))
    )
    kept: List[Candidate] = []
    raw_contents: Dict[str, str] = {}
    for candidate in candidates:
        if candidate.category != "experiences":
            kept.append(candidate)
            continue
        allowed, reason, content = next(inspections)
        if allowed:
            kept.append(candidate)
            stats["kept"] += 1
            raw_contents[content_uri_for(candidate)] = content
            continue
        stats["excluded"] += 1
        if reason == "read_error":
            stats["read_errors"] += 1
        else:
            stats["excluded_by_status"][reason] += 1

    return kept, raw_contents, stats


async def _load_session(service: Any, ctx: RequestContext, session_id: Optional[str]) -> Any:
    if not session_id:
        return None
    try:
        session = await service.sessions.get(session_id, ctx, auto_create=True)
        if not await session.is_materialized():
            return None
        return session
    except Exception as exc:
        logger.info("Session %s unavailable for context assembly: %s", session_id, exc)
        return None


async def assemble_context(
    *,
    service: Any,
    ctx: RequestContext,
    params: AssembleParams,
) -> AssembleResult:
    """Run the full assembly pipeline for one request."""
    quotas = normalize_quotas(params.quotas, params.purpose)
    penalties = normalize_penalties(params.other_peer_penalty)

    intent_checker = getattr(service.search, "is_intent_enabled", None)
    intent_enabled = intent_checker() if callable(intent_checker) else True
    expansion_mode = params.query_expansion if intent_enabled else "off"
    needs_session = bool(params.session_id) and (expansion_mode == "auto" or params.dedup_turns > 0)
    session = await _load_session(service, ctx, params.session_id) if needs_session else None

    queries, expansion_status = await expand_queries(
        query=params.query,
        session=session,
        mode=expansion_mode,
    )

    ledger = await RecallLedger.load(
        service=service,
        ctx=ctx,
        session=session,
        dedup_turns=params.dedup_turns,
    )
    cooled = ledger.cooled_uris() if ledger else set()
    excluded = normalize_exclude_uris(params.exclude_uris) | cooled

    candidates, gather_stats = await gather_candidates(
        service=service,
        ctx=ctx,
        queries=queries,
        quotas=quotas,
        limit=params.limit,
        score_threshold=params.score_threshold,
        filter=params.filter,
        image_url=params.image_url,
        peer_scope=params.peer_scope,
        penalties=penalties,
        excluded=excluded,
    )

    candidates, experience_contents, experience_lifecycle_stats = (
        await _filter_experience_lifecycle(service=service, candidates=candidates)
    )

    # Read only the candidates whose planned tier actually needs a body: with
    # the default tiers that is the events bucket, not every hit.
    pins = normalize_detail(params.detail)
    cap = per_entry_cap(params.max_tokens, len(candidates))
    readable = [
        c
        for c in candidates
        if needs_content(c, pins.for_category(c.category)) or oversized_abstract_needs_body(c, cap)
    ]
    contents: Dict[str, str] = dict(experience_contents)
    unread = [candidate for candidate in readable if content_uri_for(candidate) not in contents]
    if unread:
        contents.update(await prefetch_contents(service=service, candidates=unread))

    plan = plan_entries(
        candidates,
        contents,
        max_tokens=params.max_tokens,
        detail=pins,
    )

    rendered = render_context(plan.entries) if params.render else ""

    digest = ""
    rewrite_status = "off"
    rewrite_usage: Optional[Dict[str, int]] = None
    if plan.entries and server_rewrite_enabled(params.rewrite):
        digest, rewrite_status, rewrite_usage = await rewrite_context(
            query=params.query,
            rendered=rendered or render_context(plan.entries),
            max_bullets=params.rewrite_max_bullets,
            valid_uris=[entry.uri for entry in plan.entries],
        )
        if rewrite_status == "no_relevant":
            rendered = ""

    # A digest that reports no relevant memory blanks the block, so this turn
    # served nothing: recording those URIs would cool them for `dedup_turns`
    # turns without the reader ever having seen them, and hold them back from
    # the later turn they are relevant to.
    served = plan.entries if rewrite_status != "no_relevant" else []
    if ledger and served:
        try:
            await ledger.record(served)
        except Exception as exc:
            logger.debug("Recall ledger record failed (%s); dedup stays best-effort", exc)

    stats: Dict[str, Any] = {
        **gather_stats,
        **plan.stats,
        "purpose": params.purpose,
        "query_expansion": expansion_status,
        "planned_queries": queries,
        "score_threshold": params.score_threshold,
        "other_peer_penalties": penalties,
        "rewrite": rewrite_status,
        "rewrite_usage": rewrite_usage,
        "experience_lifecycle": experience_lifecycle_stats,
        "dedup": {
            "turns": params.dedup_turns,
            "status": ledger.status if ledger else "off",
            "cooled": len(cooled),
            "turn": ledger.turn if ledger else None,
        },
    }
    return AssembleResult(
        entries=plan.entries,
        rendered=rendered,
        digest=digest,
        stats=stats,
    )
