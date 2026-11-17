# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Token budgeting and breadth-first-then-depth tier filling.

Scores cluster in a narrow band, so spending the whole budget on the top hit is
a bad bet. Every candidate is placed at its category's default tier first and
only then deepens on leftover budget. An oversized tier falls back to a cheaper
content tier; the cheapest available content is clipped before falling back to
a bare URI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from openviking.retrieve.context_assembler.gather import Candidate
from openviking.retrieve.context_assembler.models import AssembledEntry
from openviking.retrieve.context_assembler.params import (
    TIER_ORDER,
    TIER_RANK,
    Tier,
    normalize_detail,
)
from openviking.retrieve.context_assembler.render import fragment_tokens
from openviking.retrieve.context_assembler.tiers import abstract_substitute, tier_text, tier_window
from openviking.utils.token_estimation import estimate_text_tokens, truncate_text_to_token_budget

SEPARATOR_TOKENS = 1


@dataclass
class _Slot:
    candidate: Candidate
    entry: AssembledEntry
    ceiling: Tier
    cost: int
    truncated: bool = False


@dataclass
class BudgetPlan:
    entries: List[AssembledEntry]
    stats: Dict[str, Any]


def per_entry_cap(max_tokens: int, candidate_count: int) -> int:
    """Twice the average share — the ceiling one entry may occupy."""
    return max(1, max_tokens // max(1, candidate_count) * 2)


def _tiers_down_from(candidate: Candidate, tier: Tier) -> List[Tier]:
    order = list(reversed(TIER_ORDER[: TIER_RANK[tier] + 1]))
    if tier == "abstract" and abstract_substitute(candidate) == "overview":
        # The candidate's stored abstract is its whole body, so overview is a
        # cheaper substitute here rather than a step up: try it before giving up
        # on showing any content at all.
        order.insert(1, "overview")
    return order


def _make_entry(candidate: Candidate, tier: Tier, text: str) -> AssembledEntry:
    entry = AssembledEntry(
        uri=candidate.base_uri,
        category=candidate.category,
        score=candidate.score,
        detail=tier,
        text=text,
        origin=candidate.origin,
    )
    entry.tokens = fragment_tokens(entry)
    return entry


def _clip_entry(
    candidate: Candidate,
    tier: Tier,
    text: str,
    token_budget: int,
) -> Optional[AssembledEntry]:
    """Clip non-empty text so its complete rendered fragment fits ``token_budget``."""
    if token_budget <= 0:
        return None

    # The body-only truncator cannot account for the URI, XML envelope or body
    # escaping. Search the largest body budget whose measured fragment fits,
    # rather than letting those fixed costs push the entry over the cap again.
    low = 1
    high = estimate_text_tokens(text)
    best: Optional[AssembledEntry] = None
    while low <= high:
        body_budget = (low + high) // 2
        clipped = truncate_text_to_token_budget(text, body_budget)
        entry = _make_entry(candidate, tier, clipped)
        if clipped and entry.tokens <= token_budget:
            best = entry
            low = body_budget + 1
        else:
            high = body_budget - 1
    return best


def oversized_abstract_needs_body(candidate: Candidate, cap: int) -> bool:
    """Whether an over-cap abstract falls back to a tier that reads the body.

    Only the full-body-abstract categories have that fallback. A resource or
    skill keeps its short abstract, clipped if necessary, so an oversized
    abstract there never justifies a read.
    """
    if abstract_substitute(candidate) != "overview":
        return False
    text = candidate.abstract.strip()
    return bool(text) and fragment_tokens(_make_entry(candidate, "abstract", text)) > cap


def plan_entries(
    candidates: Sequence[Candidate],
    contents: Dict[str, str],
    *,
    max_tokens: int,
    detail: Any = None,
) -> BudgetPlan:
    """Fill the budget over ``candidates`` and return the chosen tiers."""
    max_tokens = max(1, int(max_tokens))
    pins = normalize_detail(detail)
    cap = per_entry_cap(max_tokens, len(candidates))
    slots: List[_Slot] = []
    used = 0
    dropped = 0
    deduped = 0
    seen_bodies: set[int] = set()

    def fits_cap(tier: Tier, tokens: int) -> bool:
        # Only the bare URI is exempt — it has no body to cap. No tier is cheap
        # enough to trust unmeasured: a memory file's stored abstract is its
        # whole body, so exempting that tier lets one entry eat the budget.
        return tier == "uri" or tokens <= cap

    def entry_budget(index: int) -> int:
        # Reserve a fair share for every unplaced candidate. The per-entry cap
        # alone is twice that share, so using it as the floor budget can let
        # early hits starve later ones even when none exceeds the cap.
        remaining = max_tokens - used - (SEPARATOR_TOKENS if slots else 0)
        fair_share = remaining // max(1, len(candidates) - index)
        return min(cap, fair_share)

    for index, candidate in enumerate(candidates):
        start, ceiling = tier_window(candidate, pins.for_category(candidate.category))
        placed: Optional[_Slot] = None
        clip_source: Optional[tuple[Tier, str]] = None
        for tier in _tiers_down_from(candidate, start):
            text = tier_text(candidate, tier, contents=contents)
            if text is None:
                continue
            if tier == "uri" and clip_source is not None:
                # Clipping is the last content-bearing fallback.
                entry = _clip_entry(
                    candidate,
                    *clip_source,
                    token_budget=entry_budget(index),
                )
                if entry is not None:
                    cost = entry.tokens + (SEPARATOR_TOKENS if slots else 0)
                    placed = _Slot(
                        candidate=candidate,
                        entry=entry,
                        ceiling=ceiling,
                        cost=cost,
                        truncated=True,
                    )
                    break

            entry = _make_entry(candidate, tier, text)
            cost = entry.tokens + (SEPARATOR_TOKENS if slots else 0)
            if fits_cap(tier, entry.tokens) and entry.tokens <= entry_budget(index):
                placed = _Slot(candidate=candidate, entry=entry, ceiling=ceiling, cost=cost)
                break
            if tier != "uri":
                # Prefer a genuinely cheaper content tier before clipping. For
                # resources/skills no such tier exists, so their already
                # retrieved abstract is clipped without reading the body.
                clip_source = (tier, text)

        if placed is None:
            dropped += 1
            continue

        # Dedup on what actually gets injected: two URIs sharing an abstract they
        # are not showing (uri tier) are still two distinct pointers.
        body_key = hash(placed.entry.text or candidate.base_uri)
        if body_key in seen_bodies:
            deduped += 1
            continue
        seen_bodies.add(body_key)

        slots.append(placed)
        used += placed.cost

    floor_used = used

    def restore_clipped_pass() -> int:
        """Spend leftover budget on whole originals, still respecting the cap."""
        nonlocal used
        restored = 0
        for slot in slots:
            if not slot.truncated:
                continue
            text = tier_text(slot.candidate, slot.entry.detail, contents=contents)
            if text is None:
                continue
            entry = _make_entry(slot.candidate, slot.entry.detail, text)
            if not fits_cap(slot.entry.detail, entry.tokens):
                continue
            delta = entry.tokens - slot.entry.tokens
            if used + delta > max_tokens:
                continue
            slot.entry = entry
            slot.cost += delta
            slot.truncated = False
            used += delta
            restored += 1
        return restored

    def upgrade_pass(target: Tier, *, respect_cap: bool) -> int:
        nonlocal used
        upgraded = 0
        for slot in slots:
            if TIER_RANK[slot.entry.detail] >= TIER_RANK[target]:
                continue
            if TIER_RANK[target] > TIER_RANK[slot.ceiling]:
                continue
            text = tier_text(slot.candidate, target, contents=contents)
            if text is None:
                continue
            entry = _make_entry(slot.candidate, target, text)
            if respect_cap and not fits_cap(target, entry.tokens):
                continue
            delta = entry.tokens - slot.entry.tokens
            if used + delta > max_tokens:
                continue
            slot.entry = entry
            slot.cost += delta
            slot.truncated = False
            used += delta
            upgraded += 1
        return upgraded

    # Slots are in score order, so restoration and depth passes spend leftover
    # budget on the best hits first. Restoration only recovers the original
    # tier within the cap; it never crosses the category's read boundary.
    restored = restore_clipped_pass()
    overview_upgrades = upgrade_pass("overview", respect_cap=True)
    full_upgrades = upgrade_pass("full", respect_cap=True)
    # Leftover budget is worth spending: one more depth pass without the
    # per-entry cap, still bounded by max_tokens.
    spare_upgrades = upgrade_pass("full", respect_cap=False)

    entries = [slot.entry for slot in slots]
    tier_counts: Dict[str, int] = {}
    for entry in entries:
        tier_counts[entry.detail] = tier_counts.get(entry.detail, 0) + 1

    stats = {
        "max_tokens": max_tokens,
        "used_tokens": used,
        "per_entry_cap": cap,
        "returned": len(entries),
        "dropped": dropped,
        "deduped": deduped,
        "truncated": sum(slot.truncated for slot in slots),
        "detail": pins.as_stats(),
        "tier_counts": tier_counts,
        "fill": {
            "floor_tokens": floor_used,
            "restored": restored,
            "overview_upgrades": overview_upgrades,
            "full_upgrades": full_upgrades,
            "spare_upgrades": spare_upgrades,
        },
    }
    return BudgetPlan(entries=entries, stats=stats)
