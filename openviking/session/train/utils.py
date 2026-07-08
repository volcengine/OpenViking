# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Small shared helpers for the train pipeline."""

from __future__ import annotations

from typing import Any

from openviking.session.train.domain import Rollout, RolloutAnalysis


def average_score(analyses: list[RolloutAnalysis | None]) -> float | None:
    """Return the mean evaluation score across analyses, ignoring None entries."""
    scores = [
        float(analysis.evaluation.score)
        for analysis in analyses
        if analysis is not None and analysis.evaluation is not None
    ]
    if not scores:
        return None
    return sum(scores) / len(scores)


def validate_rollouts_have_cases(rollouts: list[Rollout]) -> None:
    """Raise ``ValueError`` if any rollout is missing its ``case``."""
    missing = [
        idx for idx, rollout in enumerate(rollouts) if getattr(rollout, "case", None) is None
    ]
    if missing:
        raise ValueError(
            "rollout training requires Rollout.case for all rollouts; "
            f"missing indices={missing}"
        )


def safe_int(value: Any) -> int | None:
    """Parse ``value`` as a positive integer, returning ``None`` on failure/zero."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def first_uri(uris: list[str]) -> str | None:
    """Return the first URI from a list, or ``None`` if empty."""
    return uris[0] if uris else None
