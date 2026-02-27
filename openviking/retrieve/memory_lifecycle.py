# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Hotness scoring for cold/hot memory lifecycle management (#296).

Provides a pure function to compute a 0.0–1.0 hotness score based on
access frequency (active_count) and recency (updated_at).  The score
can be blended with semantic similarity to boost frequently-accessed,
recently-updated contexts in search results.
"""

import math
from datetime import datetime, timezone
from typing import Optional

# Default half-life in days for the exponential time-decay component.
DEFAULT_HALF_LIFE_DAYS: float = 7.0


def hotness_score(
    active_count: int,
    updated_at: Optional[datetime],
    now: Optional[datetime] = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """Compute a 0.0–1.0 hotness score.

    Formula::

        score = sigmoid(log1p(active_count)) * time_decay(updated_at)

    * **sigmoid** maps ``log1p(active_count)`` into (0, 1).
    * **time_decay** is an exponential decay with configurable half-life;
      returns 0.0 when *updated_at* is ``None``.

    Args:
        active_count: Number of times this context was retrieved/accessed.
        updated_at: Last update / access timestamp (preferably UTC).
        now: Current time override (useful for deterministic tests).
        half_life_days: Half-life for the recency decay, in days.

    Returns:
        A float in [0.0, 1.0].
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # --- frequency component ---
    freq = 1.0 / (1.0 + math.exp(-math.log1p(active_count)))

    # --- recency component ---
    if updated_at is None:
        return 0.0

    # Normalise to aware UTC so subtraction always works.
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    age_days = max((now - updated_at).total_seconds() / 86400.0, 0.0)
    decay_rate = math.log(2) / half_life_days
    recency = math.exp(-decay_rate * age_days)

    return freq * recency
