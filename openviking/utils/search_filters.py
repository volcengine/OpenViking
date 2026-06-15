# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Union

from openviking.utils.time_utils import format_iso8601, parse_iso_datetime
from openviking_cli.retrieve.types import ContextType

_CONTEXT_TYPE_INPUT_ERROR = (
    "context_type must be a ContextType, string, or list of ContextType/string values"
)
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RELATIVE_RE = re.compile(r"^(?P<value>\d+)(?P<unit>[smhdw])$")
TimeField = Literal["updated_at", "created_at"]
VALID_TIME_FIELDS = {"updated_at", "created_at"}
SearchContextTypeValue = Union[ContextType, str]
SearchContextTypeInput = Union[SearchContextTypeValue, List[SearchContextTypeValue]]


def merge_search_filter(
    existing_filter: Optional[Dict[str, Any]],
    *,
    context_type: Optional[SearchContextTypeInput] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    time_field: Optional[TimeField] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Merge public search filter shortcuts into one metadata filter tree."""
    merged = merge_context_type_filter(existing_filter, context_type)
    return merge_time_filter(
        merged,
        since=since,
        until=until,
        time_field=time_field,
        now=now,
    )


def merge_context_type_filter(
    existing_filter: Optional[Dict[str, Any]],
    context_type: Optional[SearchContextTypeInput] = None,
) -> Optional[Dict[str, Any]]:
    """Merge context type shortcut into an existing metadata filter tree."""
    context_types = resolve_context_types(context_type)
    if not context_types:
        return existing_filter
    context_filter = {"op": "must", "field": "context_type", "conds": context_types}
    return _and_filters(existing_filter, context_filter)


def resolve_context_types(context_type: Optional[SearchContextTypeInput]) -> List[str]:
    """Resolve context_type parameter into normalized retrieval context types."""
    if context_type is None:
        return []

    values: List[str] = []
    if isinstance(context_type, ContextType):
        values = [context_type.value]
    elif isinstance(context_type, str):
        values = context_type.split(",")
    elif isinstance(context_type, list):
        for item in context_type:
            if isinstance(item, ContextType):
                values.append(item.value)
            elif isinstance(item, str):
                values.extend(item.split(","))
            else:
                raise ValueError(_CONTEXT_TYPE_INPUT_ERROR)
    else:
        raise ValueError(_CONTEXT_TYPE_INPUT_ERROR)

    normalized_values: List[str] = []
    invalid_values: List[str] = []
    for value in values:
        normalized = value.strip().lower()
        if not normalized:
            continue
        try:
            resolved = ContextType(normalized).value
        except ValueError:
            invalid_values.append(value)
            continue
        if resolved not in normalized_values:
            normalized_values.append(resolved)

    if invalid_values:
        valid_values = ", ".join(sorted(context_type.value for context_type in ContextType))
        raise ValueError(f"context_type must be one or more of: {valid_values}")

    return normalized_values


def merge_time_filter(
    existing_filter: Optional[Dict[str, Any]],
    since: Optional[str] = None,
    until: Optional[str] = None,
    time_field: Optional[TimeField] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Merge relative or absolute time bounds into an existing metadata filter tree."""
    since_dt, until_dt = resolve_time_bounds(since=since, until=until, now=now)
    if since_dt is None and until_dt is None:
        return existing_filter

    time_filter: Dict[str, Any] = {
        "op": "time_range",
        "field": normalize_time_field(time_field),
    }

    if since_dt is not None:
        time_filter["gte"] = _serialize_time_value(since_dt)
    if until_dt is not None:
        time_filter["lte"] = _serialize_time_value(until_dt)

    if not existing_filter:
        return time_filter
    # Preserve any caller-supplied metadata predicates by AND-ing the time range
    # into the existing filter tree instead of replacing it.
    return _and_filters(existing_filter, time_filter)


def _and_filters(
    left: Optional[Dict[str, Any]],
    right: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not left:
        return right
    if not right:
        return left
    conds: List[Dict[str, Any]] = []
    for item in (left, right):
        if item.get("op") == "and" and isinstance(item.get("conds"), list):
            conds.extend(item["conds"])
        else:
            conds.append(item)
    return {"op": "and", "conds": conds}


def _resolve_levels(level: Optional[Union[int, str, List[int], List[str]]]) -> List[int]:
    """Resolve level parameter into a list of integers."""
    if level is None:
        return []

    if isinstance(level, int):
        return [level]

    if isinstance(level, list):
        result = []
        for item in level:
            if isinstance(item, int):
                result.append(item)
            elif isinstance(item, str):
                try:
                    result.append(int(item.strip()))
                except ValueError:
                    continue
        return result

    if isinstance(level, str):
        level_str = level.strip()
        if not level_str:
            return []

        # Support comma-separated values
        if "," in level_str:
            parts = level_str.split(",")
            result = []
            for part in parts:
                part = part.strip()
                if part:
                    try:
                        result.append(int(part))
                    except ValueError:
                        continue
            return result

        # Single value
        try:
            return [int(level_str)]
        except ValueError:
            return []

    return []


def normalize_time_field(time_field: Optional[str]) -> str:
    normalized = (time_field or "updated_at").strip() or "updated_at"
    if normalized not in VALID_TIME_FIELDS:
        raise ValueError("time_field must be one of: updated_at, created_at")
    return normalized


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

    if (
        since_dt
        and until_dt
        and normalize_datetime_for_comparison(since_dt)
        > normalize_datetime_for_comparison(until_dt)
    ):
        raise ValueError(f"{lower_label} must be earlier than or equal to {upper_label}")

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
            combined = datetime.combine(parsed_date, time.max)
        else:
            combined = datetime.combine(parsed_date, time.min)
        if now.tzinfo is not None:
            return combined.replace(tzinfo=now.tzinfo)
        return combined

    dt = parse_iso_datetime(value)
    # Ensure it's in UTC for consistency
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    elif dt.tzinfo != timezone.utc:
        dt = dt.astimezone(timezone.utc)
    return dt


def _serialize_time_value(value: datetime) -> str:
    """Serialize datetime to ISO 8601 format, always in UTC."""
    return format_iso8601(value)


def _comparison_datetime(value: datetime) -> datetime:
    """Normalize datetime for comparison: always timezone-aware, in UTC."""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    # Naive datetime is treated as UTC for consistency with Context and time_utils
    return value.replace(tzinfo=timezone.utc)


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
