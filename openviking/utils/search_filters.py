from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional

from openviking.utils.source_utils import normalize_source_name
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RELATIVE_RE = re.compile(r"^(?P<value>\d+)(?P<unit>[smhdw])$")


def merge_time_filter(
    existing_filter: Optional[Dict[str, Any]],
    since: Optional[str] = None,
    until: Optional[str] = None,
    time_field: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Merge relative or absolute time bounds into an existing metadata filter tree."""
    since_dt, until_dt = resolve_time_bounds(since=since, until=until, now=now)
    if since_dt is None and until_dt is None:
        return existing_filter

    time_filter: Dict[str, Any] = {
        "op": "time_range",
        "field": (time_field or "updated_at").strip() or "updated_at",
    }

    if since_dt is not None:
        time_filter["gte"] = _serialize_time_value(since_dt)
    if until_dt is not None:
        time_filter["lte"] = _serialize_time_value(until_dt)

    if not existing_filter:
        return time_filter
    return {"op": "and", "conds": [existing_filter, time_filter]}


def merge_source_filter(
    existing_filter: Optional[Dict[str, Any]],
    source: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Merge a canonical source constraint into an existing metadata filter tree."""
    normalized_source = normalize_source_name(source)
    if not normalized_source:
        return existing_filter

    source_filter: Dict[str, Any] = {
        "op": "must",
        "field": "source",
        "conds": [normalized_source],
    }

    if not existing_filter:
        return source_filter
    return {"op": "and", "conds": [existing_filter, source_filter]}


def resolve_time_bounds(
    since: Optional[str] = None,
    until: Optional[str] = None,
    now: Optional[datetime] = None,
    *,
    lower_label: str = "since",
    upper_label: str = "until",
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Resolve relative or absolute time bounds into parsed datetimes."""
    normalized_since = (since or "").strip()
    normalized_until = (until or "").strip()
    if not normalized_since and not normalized_until:
        return (None, None)

    current_time = now or datetime.now(timezone.utc)
    since_dt = None
    until_dt = None
    if normalized_since:
        since_dt = _parse_time_value(normalized_since, current_time, is_upper_bound=False)
    if normalized_until:
        until_dt = _parse_time_value(normalized_until, current_time, is_upper_bound=True)

    if since_dt and until_dt and normalize_datetime_for_comparison(
        since_dt
    ) > normalize_datetime_for_comparison(until_dt):
        raise ValueError(
            f"--{lower_label} must be earlier than or equal to --{upper_label}"
        )

    return (since_dt, until_dt)


def normalize_datetime_for_comparison(value: datetime) -> datetime:
    """Normalize aware/naive datetimes so they can be compared safely."""
    return _comparison_datetime(value)


def matches_time_bounds(
    value: Optional[datetime],
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> bool:
    """Return True when a datetime falls within resolved bounds."""
    if value is None:
        return False

    comparable_value = normalize_datetime_for_comparison(value)
    if since is not None and comparable_value < normalize_datetime_for_comparison(since):
        return False
    if until is not None and comparable_value > normalize_datetime_for_comparison(until):
        return False
    return True


def _parse_time_value(value: str, now: datetime, *, is_upper_bound: bool) -> datetime:
    relative_match = _RELATIVE_RE.fullmatch(value)
    if relative_match:
        amount = int(relative_match.group("value"))
        unit = relative_match.group("unit")
        delta = _duration_from_unit(amount, unit)
        return now - delta

    if _DATE_ONLY_RE.fullmatch(value):
        parsed_date = datetime.strptime(value, "%Y-%m-%d").date()
        if is_upper_bound:
            return datetime.combine(parsed_date, time.max)
        return datetime.combine(parsed_date, time.min)

    return parse_iso_datetime(value)


def _serialize_time_value(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat(timespec="milliseconds")
    return format_iso8601(value)


def _comparison_datetime(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value

    local_tz = datetime.now().astimezone().tzinfo
    if local_tz is None:
        raise ValueError("Could not determine local timezone for time filter comparison")
    return value.replace(tzinfo=local_tz)


def _duration_from_unit(amount: int, unit: str) -> timedelta:
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    if unit == "w":
        return timedelta(weeks=amount)
    raise ValueError(f"Unsupported relative time unit: {unit}")
