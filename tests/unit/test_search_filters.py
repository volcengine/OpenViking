# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timezone

import pytest

from openviking.utils.search_filters import merge_time_filter, resolve_time_bounds
from openviking.utils.time_utils import parse_iso_datetime


def test_merge_time_filter_builds_relative_range():
    now = datetime(2026, 3, 11, 18, 0, tzinfo=timezone.utc)

    result = merge_time_filter(None, since="2h", now=now)

    assert result == {
        "op": "time_range",
        "field": "updated_at",
        "gte": "2026-03-11T16:00:00.000Z",
    }


def test_merge_time_filter_merges_with_existing_filter():
    now = datetime(2026, 3, 11, 18, 0, tzinfo=timezone.utc)
    existing_filter = {"op": "must", "field": "kind", "conds": ["email"]}

    result = merge_time_filter(
        existing_filter,
        since="2026-03-10",
        until="2026-03-11",
        time_field="created_at",
        now=now,
    )

    assert result == {
        "op": "and",
        "conds": [
            existing_filter,
            {
                "op": "time_range",
                "field": "created_at",
                "gte": "2026-03-10T00:00:00.000Z",
                "lte": "2026-03-11T23:59:59.999Z",
            },
        ],
    }


def test_merge_time_filter_accepts_absolute_timestamp():
    result = merge_time_filter(None, until="2026-03-11T15:18:00Z")

    assert result == {
        "op": "time_range",
        "field": "updated_at",
        "lte": "2026-03-11T15:18:00.000Z",
    }


def test_merge_time_filter_treats_empty_filter_as_missing():
    result = merge_time_filter({}, since="2026-03-11")

    assert result == {
        "op": "time_range",
        "field": "updated_at",
        "gte": "2026-03-11T00:00:00.000Z",
    }


def test_merge_time_filter_rejects_inverted_range():
    with pytest.raises(ValueError, match="since must be earlier than or equal to until"):
        merge_time_filter(None, since="2026-03-12", until="2026-03-11")


def test_merge_time_filter_handles_mixed_aware_and_naive_bounds():
    now = datetime(2026, 3, 11, 18, 0, tzinfo=timezone.utc)

    result = merge_time_filter(None, since="2h", until="2099-01-01", now=now)

    assert result == {
        "op": "time_range",
        "field": "updated_at",
        "gte": "2026-03-11T16:00:00.000Z",
        "lte": "2099-01-01T23:59:59.999Z",
    }


def test_merge_time_filter_rejects_inverted_mixed_range():
    now = datetime(2026, 3, 11, 18, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="since must be earlier than or equal to until"):
        merge_time_filter(None, since="2099-01-01", until="2h", now=now)


def test_merge_time_filter_rejects_invalid_time_value():
    with pytest.raises(ValueError):
        merge_time_filter(None, since="not-a-time")


@pytest.mark.parametrize(
    ("value", "label"),
    [
        ("1000000000d", "since"),
        ("999999999d", "since"),
        ("9999-12-31T23:59:59-12:00", "since"),
        ("1000000000d", "until"),
        ("9999-12-31T23:59:59-12:00", "until"),
    ],
)
def test_merge_time_filter_rejects_out_of_range_time_value(value, label):
    now = datetime(2026, 3, 11, tzinfo=timezone.utc)
    kwargs = {label: value}

    # OverflowError is not a ValueError, so this also proves the range error is
    # reported as invalid input rather than escaping as an internal failure.
    with pytest.raises(ValueError, match=f"{label} is out of range") as excinfo:
        merge_time_filter(None, now=now, **kwargs)

    assert not isinstance(excinfo.value, OverflowError)


def test_resolve_time_bounds_uses_the_caller_labels_for_range_errors():
    now = datetime(2026, 3, 11, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="lower is out of range"):
        resolve_time_bounds(since="1000000000d", now=now, lower_label="lower")

    with pytest.raises(ValueError, match="upper is out of range"):
        resolve_time_bounds(until="1000000000d", now=now, upper_label="upper")


def test_merge_time_filter_rejects_invalid_time_field():
    with pytest.raises(ValueError, match="time_field must be one of"):
        merge_time_filter(None, since="2h", time_field="published_at")


def test_merge_time_filter_output_preserves_timezone_semantics():
    now = datetime(2026, 3, 11, 18, 0, tzinfo=timezone.utc)

    result = merge_time_filter(None, since="30m", until="2026-03-11", now=now)

    assert parse_iso_datetime(result["gte"]).tzinfo is not None
    assert parse_iso_datetime(result["lte"]).tzinfo is not None


def test_merge_time_filter_date_only_uses_now_timezone():
    local_tz = timezone.utc
    now = datetime(2026, 3, 11, 18, 0, tzinfo=local_tz)

    result = merge_time_filter(None, since="2026-03-11", until="2026-03-12", now=now)

    assert result["gte"].endswith("Z")
    assert result["lte"].endswith("Z")
