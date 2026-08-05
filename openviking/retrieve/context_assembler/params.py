# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Parameter contract and normalization for server-side context assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Mapping, Optional, Sequence, Tuple, Union

Detail = Literal["abstract", "overview", "full"]
# "auto" no longer selects a strategy; it is accepted as a synonym for "unset".
DetailRequest = Union[Literal["auto", "abstract", "overview", "full"], Dict[str, Detail]]
Tier = Literal["uri", "abstract", "overview", "full"]
Purpose = Literal["chat", "coding"]

MEMORY_CATEGORIES: Tuple[str, ...] = ("events", "entities", "preferences", "experiences")
CATEGORY_KEYS: Tuple[str, ...] = (*MEMORY_CATEGORIES, "resources", "skills")

TIER_ORDER: Tuple[Tier, ...] = ("uri", "abstract", "overview", "full")
TIER_RANK: Dict[str, int] = {tier: rank for rank, tier in enumerate(TIER_ORDER)}
PINNABLE_TIERS: Tuple[Detail, ...] = ("abstract", "overview", "full")

DEFAULT_MAX_TOKENS = 1600
DEFAULT_LIMIT = 10
MAX_EXCLUDE_URIS = 200
MAX_PLANNED_QUERIES = 3
READ_CONCURRENCY = 8
OTHER_PEER_OVERFETCH = 4

ORIGIN_ORDER: Tuple[str, ...] = ("actor_peer", "self", "other_peer")

# Tier a category is served at when the caller does not pin ``detail``.
#
# ``events`` is the only memory type whose body is long enough for the
# ``# Summary`` section to be a real compression, so it is the only one worth a
# file read. The others sit at ``abstract`` because the memory writer stores the
# whole stripped body in that scalar (it doubles as the embedding text), which
# makes ``abstract`` the complete file at zero read cost. Once the writer stores
# a separate summary scalar, ``events`` moves back to ``abstract`` here and the
# default path stops reading files altogether.
# ``resources``/``skills`` stay at their generated 256-char abstract: their
# bodies are large and may carry credentials, so deepening is opt-in.
DEFAULT_TIER_BY_CATEGORY: Dict[str, Tier] = {
    "events": "overview",
    "entities": "abstract",
    "preferences": "abstract",
    "experiences": "abstract",
    "resources": "abstract",
    "skills": "abstract",
}
DEFAULT_TIER: Tier = "abstract"

# How far leftover budget may raise a category above its default tier. A
# category absent here never deepens, which is what keeps the default path from
# reading resource bodies.
DEPTH_CEILING_BY_CATEGORY: Dict[str, Tier] = {"events": "full"}

# Purpose presets are absolute bucket ceilings. Their total is the candidate
# width for bucketed retrieval; ``limit`` belongs only to quota-free flat mode.
PURPOSE_PRESETS: Dict[str, Dict[str, int]] = {
    "coding": {
        "events": 1,
        "entities": 2,
        "preferences": 1,
        "experiences": 1,
        "resources": 3,
        "skills": 2,
    },
    "chat": {
        "events": 3,
        "entities": 3,
        "preferences": 1,
        "experiences": 1,
        "resources": 1,
        "skills": 1,
    },
}

DEFAULT_QUOTAS: Dict[str, int] = {
    "events": 10,
    "entities": 10,
    "preferences": 3,
    "experiences": 0,
}

DEFAULT_OTHER_PEER_PENALTIES: Dict[str, float] = {
    "events": 0.1,
    "entities": 0.1,
    "preferences": 0.02,
    "experiences": 0.02,
    "resources": 0.02,
    "skills": 0.02,
}


@dataclass
class AssembleParams:
    """Resolved request contract for one context assembly run."""

    query: str = ""
    image_url: Optional[str] = None
    limit: int = DEFAULT_LIMIT
    score_threshold: Optional[float] = None
    filter: Optional[Dict[str, Any]] = None

    session_id: Optional[str] = None
    query_expansion: Literal["off", "auto"] = "auto"

    max_tokens: int = DEFAULT_MAX_TOKENS
    quotas: Optional[Mapping[str, int]] = None
    purpose: Optional[Purpose] = None
    detail: Any = None
    dedup_turns: int = 0
    exclude_uris: Sequence[str] = field(default_factory=tuple)
    peer_scope: Literal["actor", "all"] = "all"
    other_peer_penalty: Any = None

    rewrite: Any = False
    rewrite_max_bullets: int = 6

    render: bool = True


def normalize_quotas(
    quotas: Optional[Mapping[str, Any]],
    purpose: Optional[str] = None,
) -> Optional[Dict[str, int]]:
    """Resolve the active quota map, or ``None`` when bucketed sampling is off."""
    if quotas is None:
        if not purpose:
            return None
        preset = PURPOSE_PRESETS.get(purpose)
        return dict(preset) if preset else None

    resolved: Dict[str, int] = {}
    for key, value in quotas.items():
        if key not in CATEGORY_KEYS:
            continue
        try:
            resolved[key] = max(0, int(value))
        except (TypeError, ValueError):
            resolved[key] = 0
    return resolved


def _clamp_penalty(value: Any, fallback: float) -> float:
    try:
        penalty = float(value)
    except (TypeError, ValueError):
        penalty = fallback
    return min(1.0, max(0.0, penalty))


def normalize_penalties(value: Any = None) -> Dict[str, float]:
    """Normalize other-peer score penalties per category."""
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
    return dict.fromkeys(CATEGORY_KEYS, penalty)


def normalize_exclude_uris(values: Optional[Sequence[str]]) -> set[str]:
    return {str(uri).strip() for uri in (values or ())[:MAX_EXCLUDE_URIS] if str(uri).strip()}


@dataclass(frozen=True)
class DetailPins:
    """Resolved ``detail`` request field: an explicit tier per category, if any."""

    scalar: Optional[Detail] = None
    by_category: Mapping[str, Detail] = field(default_factory=dict)

    def for_category(self, category: str) -> Optional[Detail]:
        """The pinned tier for ``category``, or ``None`` to use its default."""
        return self.by_category.get(category, self.scalar)

    def as_stats(self) -> Any:
        return dict(self.by_category) if self.by_category else self.scalar


def normalize_detail(value: Any) -> DetailPins:
    """Resolve ``detail`` from a scalar, a per-category mapping, or nothing.

    Unknown tiers are dropped rather than raised: the MCP tool signature cannot
    enforce an enum, and a stray value there must degrade to the defaults
    instead of failing the whole call.
    """
    if isinstance(value, DetailPins):
        return value
    if isinstance(value, Mapping):
        return DetailPins(
            by_category={
                key: tier
                for key, tier in value.items()
                if key in CATEGORY_KEYS and tier in PINNABLE_TIERS
            }
        )
    return DetailPins(scalar=value if value in PINNABLE_TIERS else None)
