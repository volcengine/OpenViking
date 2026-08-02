# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Deterministic admission decisions for injection-ready memory recall."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

ADMISSION_MODES = ("off", "shadow", "enforce")
MEMORY_TYPE_ORDER = ("events", "entities", "preferences", "experiences")
REASON_BELOW_TYPE_MIN_SCORE = "below_type_min_score"
REASON_BELOW_OTHER_PEER_MIN_SCORE = "below_other_peer_min_score"
REASON_NO_CANDIDATES = "no_candidates"


def _clamp_score(value: Any, fallback: float = 0.0) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = fallback
    if not math.isfinite(score):
        score = fallback
    return min(1.0, max(0.0, score))


@dataclass(frozen=True)
class RecallAdmissionConfig:
    """Normalized, model-free recall admission policy."""

    mode: str = "off"
    type_min_scores: dict[str, float] = field(default_factory=dict)
    other_peer_score_delta: float = 0.0

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | None) -> RecallAdmissionConfig:
        raw = value or {}
        mode = str(raw.get("mode", "off")).lower()
        if mode not in ADMISSION_MODES:
            mode = "off"

        raw_type_scores = raw.get("type_min_scores")
        type_scores: dict[str, float] = {}
        if isinstance(raw_type_scores, Mapping):
            for memory_type, raw_score in raw_type_scores.items():
                if memory_type not in MEMORY_TYPE_ORDER:
                    continue
                try:
                    score = float(raw_score)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(score):
                    type_scores[str(memory_type)] = min(1.0, max(0.0, score))

        return cls(
            mode=mode,
            type_min_scores=type_scores,
            other_peer_score_delta=_clamp_score(raw.get("other_peer_score_delta", 0.0)),
        )


@dataclass(frozen=True)
class RecallAdmissionDecision:
    admitted: bool
    reason: str | None
    required_score: float


def decide_recall_admission(
    *,
    score: Any,
    memory_type: str,
    origin: str,
    min_score: float,
    config: RecallAdmissionConfig,
) -> RecallAdmissionDecision:
    """Evaluate one candidate without reading or exposing memory content."""
    base_required = max(
        _clamp_score(min_score),
        config.type_min_scores.get(memory_type, 0.0),
    )
    candidate_score = _clamp_score(score)
    if origin == "other_peer":
        peer_required = min(1.0, base_required + config.other_peer_score_delta)
        if candidate_score < peer_required:
            return RecallAdmissionDecision(
                admitted=False,
                reason=REASON_BELOW_OTHER_PEER_MIN_SCORE,
                required_score=peer_required,
            )
    if candidate_score < base_required:
        return RecallAdmissionDecision(
            admitted=False,
            reason=REASON_BELOW_TYPE_MIN_SCORE,
            required_score=base_required,
        )
    return RecallAdmissionDecision(admitted=True, reason=None, required_score=base_required)


@dataclass
class RecallAdmissionTracker:
    """Collect aggregate decisions while keeping candidate identifiers private."""

    config: RecallAdmissionConfig
    min_score: float
    evaluated: int = 0
    accepted: int = 0
    rejected: int = 0
    reason_counts: dict[str, int] = field(default_factory=dict)

    def evaluate(self, *, score: Any, memory_type: str, origin: str) -> bool:
        if self.config.mode == "off":
            return True
        decision = decide_recall_admission(
            score=score,
            memory_type=memory_type,
            origin=origin,
            min_score=self.min_score,
            config=self.config,
        )
        self.evaluated += 1
        if decision.admitted:
            self.accepted += 1
        else:
            self.rejected += 1
            assert decision.reason is not None
            self.reason_counts[decision.reason] = self.reason_counts.get(decision.reason, 0) + 1
        return decision.admitted or self.config.mode == "shadow"

    def to_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "mode": self.config.mode,
            "evaluated": self.evaluated,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "reason_counts": dict(sorted(self.reason_counts.items())),
            "type_min_scores": self.config.type_min_scores,
            "other_peer_score_delta": self.config.other_peer_score_delta,
            "would_abstain": self.config.mode != "off" and self.accepted == 0,
            "abstained": self.config.mode == "enforce" and self.accepted == 0,
        }
        if self.config.mode != "off" and self.evaluated == 0:
            stats["reason_counts"] = {REASON_NO_CANDIDATES: 1}
        return stats
