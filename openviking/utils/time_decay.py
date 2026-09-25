# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Event time-decay validation and score fusion helpers."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timezone
from numbers import Real
from typing import Any, Optional

from openviking.utils.time_utils import format_iso8601, parse_iso_datetime

DEFAULT_TIME_DECAY_CANDIDATE_FACTOR = 3
MAX_TIME_DECAY_CANDIDATES = 100_000
# This curve is an internal ranking contract; it is intentionally not part of
# ov.conf or ovcli.conf.
EVENT_TIME_DECAY_SCALE = "7d"
EVENT_TIME_DECAY_DECAY = 0.5
# Match VikingDB's date_time duration limit: 3000 years of 365 days.
MAX_DURATION_MS = 3000 * 365 * 24 * 60 * 60 * 1000

_DURATION_RE = re.compile(r"^(0|[0-9]+[mhd])$")
_DURATION_MULTIPLIERS_MS = {
    "m": 60 * 1000,
    "h": 60 * 60 * 1000,
    "d": 24 * 60 * 60 * 1000,
}


def parse_duration_ms(value: Any, *, parameter_name: str = "duration") -> int:
    """Parse ``0`` or a non-negative integer duration with m/h/d units."""
    if not isinstance(value, str) or not _DURATION_RE.fullmatch(value):
        raise ValueError(
            f"{parameter_name} must be '0' or a non-negative integer followed by m, h, or d"
        )
    if value == "0":
        return 0
    duration_ms = int(value[:-1]) * _DURATION_MULTIPLIERS_MS[value[-1]]
    if duration_ms > MAX_DURATION_MS:
        raise ValueError(f"{parameter_name} exceeds the maximum duration of 1095000d")
    return duration_ms


def validate_event_time_decay_request(protection: Any) -> None:
    """Validate the optional protection period without changing score thresholds."""
    if protection is None:
        return
    parse_duration_ms(protection, parameter_name="events_time_decay_protection")


def _datetime_to_epoch_ms(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("time value must not be boolean")
    if isinstance(value, Real):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("time value must be finite")
        return result
    if isinstance(value, str):
        value = parse_iso_datetime(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp() * 1000.0
    raise ValueError("time value must be an epoch millisecond number or ISO 8601 string")


@dataclass(frozen=True)
class TimeDecayFusionSpec:
    """Compiled score-fusion parameters for event time decay."""

    field: str
    origin_ms: float
    offset_ms: int
    scale_ms: int
    decay: float
    _decay_rate: float = dataclass_field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Validate and compile request constants once, outside the candidate loop.
        if self.scale_ms <= 0:
            raise ValueError("time-decay scale must be greater than zero")
        if not 0.0 < self.decay < 1.0:
            raise ValueError("time-decay decay must be in (0, 1)")
        object.__setattr__(self, "origin_ms", _datetime_to_epoch_ms(self.origin_ms))
        object.__setattr__(self, "_decay_rate", math.log(self.decay) / self.scale_ms)

    def fuse(self, origin_score: float, source_time: Any) -> tuple[float, float]:
        """Return ``(final_score, raw_addition_score)`` for one candidate."""
        # Match VikingDB's exp decay operator, including future timestamps.
        distance_ms = max(
            0.0, abs(self.origin_ms - _datetime_to_epoch_ms(source_time)) - self.offset_ms
        )
        addition_score = math.exp(self._decay_rate * distance_ms)
        final_score = origin_score * addition_score
        return final_score, addition_score

    def fuse_optional(self, origin_score: float, source_time: Any) -> tuple[float, Optional[float]]:
        """Fuse only reliable source times; otherwise preserve the origin score."""
        try:
            return self.fuse(origin_score, source_time)
        except (TypeError, ValueError, OverflowError):
            return origin_score, None


def fuse_time_decay_scores(*, origin_score: float, addition_score: float) -> float:
    """Multiply the semantic score by the time score."""
    return origin_score * addition_score


def build_time_decay_fusion_spec(
    *,
    protection: str,
    origin: Optional[datetime] = None,
    field: str = "updated_at",
) -> TimeDecayFusionSpec:
    """Compile the fixed event time-decay curve for one retrieval request."""
    return TimeDecayFusionSpec(
        field=field,
        origin_ms=origin or datetime.now(timezone.utc),
        offset_ms=parse_duration_ms(protection, parameter_name="events_time_decay_protection"),
        scale_ms=parse_duration_ms(EVENT_TIME_DECAY_SCALE, parameter_name="time-decay scale"),
        decay=EVENT_TIME_DECAY_DECAY,
    )


def build_time_decay_post_process_ops(
    *,
    protection: str,
    origin: Optional[datetime] = None,
    field: str = "updated_at",
) -> list[dict[str, Any]]:
    """Build the fixed VikingDB score-fusion operator for event results."""
    protection_ms = parse_duration_ms(protection, parameter_name="events_time_decay_protection")
    addition = {
        "factor": 1,
        "base_value_from": "decay_func",
        "field": field,
        "func": "exp",
        "origin": format_iso8601(origin or datetime.now(timezone.utc)),
        "scale": EVENT_TIME_DECAY_SCALE,
        "decay": EVENT_TIME_DECAY_DECAY,
    }
    # VikingDB defaults an omitted offset to zero but rejects explicit zero
    # durations (including "0m", "0h", and "0d").
    if protection_ms > 0:
        addition["offset"] = protection

    return [
        {
            "op": "score_fusion",
            "fusion_by": "multiply",
            "normalize_for_origin_score": {"enable": False},
            "normalize_for_addition_score": {"enable": False},
            "addition_score": [addition],
        }
    ]


def time_decay_candidate_limit(limit: int, offset: int = 0) -> int:
    """Return the bounded candidate budget used before local score fusion."""
    final_window = limit + offset
    if final_window > MAX_TIME_DECAY_CANDIDATES:
        raise ValueError(
            f"time-decay search limit + offset must not exceed {MAX_TIME_DECAY_CANDIDATES}"
        )
    return min(
        final_window * DEFAULT_TIME_DECAY_CANDIDATE_FACTOR,
        MAX_TIME_DECAY_CANDIDATES,
    )
