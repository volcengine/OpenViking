# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Token budgeting and breadth-first-then-depth tier filling.

Scores cluster in a narrow band, so spending the whole budget on the top hit is
a bad bet. Every candidate is placed at its category's default tier first and
only then deepens on leftover budget, and an oversized tier falls back to the
previous one instead of being truncated.
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
from openviking.retrieve.context_assembler.tiers import tier_text, tier_window

SEPARATOR_TOKENS = 1


@dataclass
class _Slot:
    candidate: Candidate
    entry: AssembledEntry
    ceiling: Tier
    cost: int


@dataclass
class BudgetPlan:
    entries: List[AssembledEntry]
    stats: Dict[str, Any]


def per_entry_cap(max_tokens: int, candidate_count: int) -> int:
    """Twice the average share — the ceiling one entry may occupy."""
    return max(1, max_tokens // max(1, candidate_count) * 2)


def _tiers_down_from(tier: Tier) -> List[Tier]:
    order = list(reversed(TIER_ORDER[: TIER_RANK[tier] + 1]))
    if tier == "abstract":
        # A memory file's stored abstract is its whole body, so overview is a
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


def abstract_over_cap(candidate: Candidate, cap: int) -> bool:
    """Whether this candidate's stored abstract exceeds the per-entry cap.

    Such a candidate falls back to overview, so its body has to be read even
    though its own tier would not need one.
    """
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

    for candidate in candidates:
        start, ceiling = tier_window(candidate, pins.for_category(candidate.category))
        placed: Optional[_Slot] = None
        for tier in _tiers_down_from(start):
            text = tier_text(candidate, tier, contents=contents)
            if text is None:
                continue
            entry = _make_entry(candidate, tier, text)
            cost = entry.tokens + (SEPARATOR_TOKENS if slots else 0)
            if not fits_cap(tier, entry.tokens):
                continue
            if used + cost > max_tokens:
                continue
            placed = _Slot(candidate=candidate, entry=entry, ceiling=ceiling, cost=cost)
            break

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
            used += delta
            upgraded += 1
        return upgraded

    # Slots are in score order, so the depth passes spend whatever budget is
    # left on the best hits first — no absolute score threshold, which the
    # observed 0.38-0.50 score band cannot support anyway.
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
        "detail": pins.as_stats(),
        "tier_counts": tier_counts,
        "fill": {
            "floor_tokens": floor_used,
            "overview_upgrades": overview_upgrades,
            "full_upgrades": full_upgrades,
            "spare_upgrades": spare_upgrades,
        },
    }
    return BudgetPlan(entries=entries, stats=stats)
