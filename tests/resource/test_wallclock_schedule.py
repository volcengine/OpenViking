# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for wall-clock scheduled watch execution (#3932).

``schedule_time`` ("HH:MM" in an explicit IANA timezone) anchors execution to
the next occurrence of that wall-clock time instead of drifting by interval.
"""

from datetime import datetime, timedelta

import pytest

from openviking.resource.watch_manager import (
    WatchTask,
    _validate_wallclock_schedule,
)


def _task(schedule_time=None, schedule_timezone=None, **kwargs):
    return WatchTask(
        path="/p",
        schedule_time=schedule_time,
        schedule_timezone=schedule_timezone,
        **kwargs,
    )


# ---------- validation ----------


@pytest.mark.parametrize("value", ["01:00", "23:59", "00:00", "13:05"])
def test_validate_accepts_wallclock_times(value):
    _validate_wallclock_schedule(value, "Asia/Shanghai")


@pytest.mark.parametrize("value", ["24:00", "1:00", "01:0", "0100", "ab:cd", "1a:30"])
def test_validate_rejects_malformed_times(value):
    with pytest.raises(ValueError, match="HH:MM"):
        _validate_wallclock_schedule(value, None)


def test_validate_rejects_unknown_timezone():
    with pytest.raises(ValueError, match="Unknown schedule_timezone"):
        _validate_wallclock_schedule("01:00", "Mars/Olympus")


def test_validate_requires_paired_clear():
    with pytest.raises(ValueError, match="cleared together"):
        _validate_wallclock_schedule("", "Asia/Shanghai")


# ---------- interval fallback unchanged ----------


def test_interval_schedule_ignores_wallclock_fields():
    task = _task(watch_interval=30)
    base = datetime(2026, 9, 4, 10, 0, 0)
    task.created_at = base
    assert task.calculate_next_execution_time(now=base) == base + timedelta(minutes=30)


# ---------- wall-clock next-run ----------


def test_wallclock_today_when_now_is_before():
    task = _task(schedule_time="09:30", schedule_timezone="UTC")
    now = datetime(2026, 9, 4, 8, 0, 0)  # aware via UTC assumption below
    nxt = task.calculate_next_execution_time(now=_utc(now))
    # Compare in UTC to be server-tz independent
    assert _to_utc(nxt) == datetime(2026, 9, 4, 9, 30, 0, tzinfo=None)


def test_wallclock_tomorrow_when_now_is_after():
    task = _task(schedule_time="09:30", schedule_timezone="UTC")
    nxt = task.calculate_next_execution_time(now=_utc(datetime(2026, 9, 4, 10, 0, 0)))
    assert _to_utc(nxt) == datetime(2026, 9, 5, 9, 30, 0, tzinfo=None)


def test_wallclock_timezone_conversion():
    # 01:00 Asia/Shanghai == 17:00 UTC previous day
    task = _task(schedule_time="01:00", schedule_timezone="Asia/Shanghai")
    nxt = task.calculate_next_execution_time(now=_utc(datetime(2026, 9, 4, 3, 0, 0)))
    assert _to_utc(nxt) == datetime(2026, 9, 4, 17, 0, 0, tzinfo=None)


def test_wallclock_no_drift_after_delayed_run():
    # A delayed execution must not push the next wall-clock run: the anchor is
    # "next occurrence vs now", never "last_execution + interval" (#3932).
    task = _task(schedule_time="02:00", schedule_timezone="UTC")
    task.last_execution_time = datetime(2026, 9, 4, 5, 30, 0)  # ran 3.5h late
    nxt = task.calculate_next_execution_time(now=_utc(datetime(2026, 9, 4, 5, 31, 0)))
    assert _to_utc(nxt) == datetime(2026, 9, 5, 2, 0, 0, tzinfo=None)


def test_wallclock_without_timezone_uses_server_local():
    task = _task(schedule_time="23:59")
    now = datetime.now()
    nxt = task.calculate_next_execution_time(now=now)
    assert nxt > now
    assert (nxt - now) <= timedelta(days=1)


# ---------- helpers ----------


def _utc(naive: datetime) -> datetime:
    from datetime import timezone

    return naive.replace(tzinfo=timezone.utc)


def _to_utc(naive_or_aware: datetime) -> datetime:
    from datetime import timezone

    if naive_or_aware.tzinfo is None:
        return naive_or_aware.astimezone(timezone.utc).replace(tzinfo=None)
    return naive_or_aware.astimezone(timezone.utc).replace(tzinfo=None)


# ---------- serialization round-trip ----------


def test_to_dict_includes_schedule_fields():
    task = _task(schedule_time="01:00", schedule_timezone="Asia/Shanghai")
    data = task.to_dict()
    assert data["schedule_time"] == "01:00"
    assert data["schedule_timezone"] == "Asia/Shanghai"


# ---------- DST correctness ----------


def test_wallclock_across_spring_forward():
    # America/New_York springs forward 2026-03-08 02:00 -> 03:00; a 01:00
    # schedule still resolves to the next real 01:00 (Mar 9), never the
    # nonexistent Mar 8 02:00-03:00 window.
    task = _task(schedule_time="01:00", schedule_timezone="America/New_York")
    nxt = task.calculate_next_execution_time(now=_utc(datetime(2026, 3, 7, 12, 0, 0)))
    assert _to_utc(nxt) == datetime(2026, 3, 8, 6, 0, 0, tzinfo=None)  # 01:00 EST = 06:00 UTC


def test_wallclock_after_spring_forward_gap():
    # Just past the gap (Mar 8 07:00 UTC = 02:00 EST->EDT local), the next
    # 01:00 local occurrence is Mar 9 01:00 EDT = 05:00 UTC.
    task = _task(schedule_time="01:00", schedule_timezone="America/New_York")
    nxt = task.calculate_next_execution_time(now=_utc(datetime(2026, 3, 8, 7, 0, 0)))
    assert _to_utc(nxt) == datetime(2026, 3, 9, 5, 0, 0, tzinfo=None)


def test_wallclock_across_fall_back():
    # America/New_York falls back 2026-11-01 02:00 -> 01:00; 01:30 exists
    # twice (01:30 EDT = 05:30 UTC, then 01:30 EST = 06:30 UTC). At 06:00 UTC
    # (= 02:00 EDT) the second occurrence is still ahead, so the next run is
    # the same day's 01:30 EST.
    task = _task(schedule_time="01:30", schedule_timezone="America/New_York")
    nxt = task.calculate_next_execution_time(now=_utc(datetime(2026, 11, 1, 6, 0, 0)))
    assert _to_utc(nxt) == datetime(2026, 11, 1, 6, 30, 0, tzinfo=None)
