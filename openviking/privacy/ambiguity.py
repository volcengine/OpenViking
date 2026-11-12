# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Policy for privacy values registered under more than one field.

The matcher already renders ``{{ov_privacy:ambiguous}}`` for such a value, so a
write is not lost information: the placeholder is a usable, if less specific,
reference. Rejecting the whole document instead drops content silently for
callers that write asynchronously (``wait=false`` never sees the error).

Tolerating by default keeps documents; ``OPENVIKING_PRIVACY_AMBIGUOUS_STRICT=1``
restores the previous hard rejection for catalog authors who want the signal.
"""

from __future__ import annotations

import os
from typing import Iterable, Sequence

STRICT_AMBIGUITY_ENV = "OPENVIKING_PRIVACY_AMBIGUOUS_STRICT"
_STRICT_VALUES = frozenset({"1", "true", "yes", "on"})


def strict_ambiguity_enabled() -> bool:
    """True when ambiguous registrations must reject the whole write."""

    return os.environ.get(STRICT_AMBIGUITY_ENV, "").strip().lower() in _STRICT_VALUES


def candidate_references(
    ambiguous: Iterable[Sequence[str]],
) -> list[list[str]]:
    """Normalize matcher output into the JSON-friendly shape used in errors."""

    return [list(candidates) for candidates in ambiguous]
