# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Comprehensive tests for time utility functions."""

from datetime import datetime, timedelta, timezone

from openviking.utils.time_utils import (
    format_iso8601,
    format_simplified,
    get_current_timestamp,
    parse_iso_datetime,
)


class TestParseIsoDatetime:
    """Test parse_iso_datetime function."""

    def test_parse_standard_iso_format(self):
        """Test parsing standard ISO 8601 format."""
        dt = parse_iso_datetime("2026-03-26T10:30:00+00:00")

        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 26
        assert dt.hour == 10
        assert dt.minute == 30
        assert dt.second == 0
        assert dt.tzinfo is not None

    def test_parse_z_suffix(self):
        """Test parsing ISO 8601 with Z suffix."""
        dt = parse_iso_datetime("2026-03-26T10:30:00Z")

        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 26
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timezone.utc.utcoffset(dt)

    def test_parse_with_microseconds(self):
        """Test parsing with microseconds."""
        dt = parse_iso_datetime("2026-03-26T10:30:00.123456+00:00")

        assert dt.microsecond == 123456

    def test_parse_with_excess_fractional_seconds(self):
        """Test parsing with >6 digit fractional seconds (Windows compatibility)."""
        # Windows may produce timestamps with >6 digit fractional seconds
        dt = parse_iso_datetime("2026-03-26T10:30:00.1470042+08:00")

        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 26
        # Should truncate to 6 digits
        assert dt.microsecond == 147004

    def test_parse_z_with_excess_fractional_seconds(self):
        """Test parsing Z suffix with excess fractional seconds."""
        dt = parse_iso_datetime("2026-03-26T01:26:14.481Z")

        assert dt.tzinfo is not None
        assert dt.utcoffset() == timezone.utc.utcoffset(dt)

    def test_parse_with_timezone_offset(self):
        """Test parsing with non-UTC timezone offset."""
        dt = parse_iso_datetime("2026-03-26T18:30:00+08:00")

        # 18:30+08:00 = 10:30 UTC
        dt_utc = dt.astimezone(timezone.utc)
        assert dt_utc.hour == 10
        assert dt_utc.minute == 30

    def test_parse_negative_timezone_offset(self):
        """Test parsing with negative timezone offset."""
        dt = parse_iso_datetime("2026-03-26T05:30:00-05:00")

        # 05:30-05:00 = 10:30 UTC
        dt_utc = dt.astimezone(timezone.utc)
        assert dt_utc.hour == 10
        assert dt_utc.minute == 30

    def test_parse_milliseconds_only(self):
        """Test parsing with milliseconds only."""
        dt = parse_iso_datetime("2026-03-26T10:30:00.123+00:00")

        assert dt.microsecond == 123000

    def test_parse_no_fractional_seconds(self):
        """Test parsing without fractional seconds."""
        dt = parse_iso_datetime("2026-03-26T10:30:00+00:00")

        assert dt.microsecond == 0


class TestFormatIso8601:
    """Test format_iso8601 function."""

    def test_format_utc_datetime(self):
        """Test formatting UTC datetime."""
        dt = datetime(2026, 3, 26, 10, 30, 0, tzinfo=timezone.utc)
        formatted = format_iso8601(dt)

        assert formatted == "2026-03-26T10:30:00.000Z"

    def test_format_naive_datetime_assumes_utc(self):
        """Test formatting naive datetime assumes UTC."""
        dt = datetime(2026, 3, 26, 10, 30, 0)
        formatted = format_iso8601(dt)

        assert formatted == "2026-03-26T10:30:00.000Z"

    def test_format_converts_to_utc(self):
        """Test formatting converts non-UTC to UTC."""
        dt = datetime(2026, 3, 26, 18, 30, 0, tzinfo=timezone(timedelta(hours=8)))
        formatted = format_iso8601(dt)

        # 18:30+08:00 = 10:30 UTC
        assert formatted == "2026-03-26T10:30:00.000Z"

    def test_format_with_milliseconds(self):
        """Test formatting preserves milliseconds."""
        dt = datetime(2026, 3, 26, 10, 30, 0, 123456, tzinfo=timezone.utc)
        formatted = format_iso8601(dt)

        assert formatted == "2026-03-26T10:30:00.123Z"

    def test_format_with_negative_timezone(self):
        """Test formatting converts negative timezone to UTC."""
        dt = datetime(2026, 3, 26, 5, 30, 0, tzinfo=timezone(timedelta(hours=-5)))
        formatted = format_iso8601(dt)

        # 05:30-05:00 = 10:30 UTC
        assert formatted == "2026-03-26T10:30:00.000Z"

    def test_format_ends_with_z(self):
        """Test formatted string ends with Z."""
        dt = datetime(2026, 3, 26, 10, 30, 0, tzinfo=timezone.utc)
        formatted = format_iso8601(dt)

        assert formatted.endswith("Z")


class TestFormatSimplified:
    """Test format_simplified function."""

    def test_format_same_day_shows_time(self):
        """Test formatting shows time for same day."""
        now = datetime(2026, 3, 26, 15, 0, 0)
        dt = datetime(2026, 3, 26, 10, 30, 0)

        formatted = format_simplified(dt, now)

        assert formatted == "10:30:00"

    def test_format_different_day_shows_date(self):
        """Test formatting shows date for different day."""
        now = datetime(2026, 3, 26, 15, 0, 0)
        dt = datetime(2026, 3, 25, 10, 30, 0)

        formatted = format_simplified(dt, now)

        assert formatted == "2026-03-25"

    def test_format_older_date_shows_date(self):
        """Test formatting shows date for older dates."""
        now = datetime(2026, 3, 26, 15, 0, 0)
        dt = datetime(2025, 12, 1, 10, 30, 0)

        formatted = format_simplified(dt, now)

        assert formatted == "2025-12-01"

    def test_format_just_under_24h_shows_time(self):
        """Test formatting shows time for just under 24 hours."""
        now = datetime(2026, 3, 26, 15, 0, 0)
        dt = datetime(2026, 3, 25, 16, 0, 0)

        formatted = format_simplified(dt, now)

        # 16:00 is less than 24 hours ago
        assert formatted == "16:00:00"

    def test_format_just_over_24h_shows_date(self):
        """Test formatting shows date for just over 24 hours."""
        now = datetime(2026, 3, 26, 15, 0, 0)
        dt = datetime(2026, 3, 25, 14, 0, 0)

        formatted = format_simplified(dt, now)

        # 14:00 is more than 24 hours ago
        assert formatted == "2026-03-25"


class TestGetCurrentTimestamp:
    """Test get_current_timestamp function."""

    def test_returns_string(self):
        """Test function returns string."""
        ts = get_current_timestamp()

        assert isinstance(ts, str)

    def test_ends_with_z(self):
        """Test timestamp ends with Z."""
        ts = get_current_timestamp()

        assert ts.endswith("Z")

    def test_format_is_correct(self):
        """Test timestamp format is correct."""
        ts = get_current_timestamp()

        # Should match pattern: YYYY-MM-DDTHH:MM:SS.sssZ
        assert len(ts) == 24
        assert ts[4] == "-"
        assert ts[7] == "-"
        assert ts[10] == "T"
        assert ts[13] == ":"
        assert ts[16] == ":"
        assert ts[19] == "."

    def test_is_utc(self):
        """Test timestamp is in UTC."""
        ts = get_current_timestamp()

        # Parse it back and verify it's a valid timestamp
        dt = parse_iso_datetime(ts)
        assert dt.tzinfo is not None


class TestTimeUtilsRoundTrip:
    """Test round-trip conversions."""

    def test_format_parse_roundtrip(self):
        """Test format -> parse roundtrip preserves values."""
        original = datetime(2026, 3, 26, 10, 30, 15, 123456, tzinfo=timezone.utc)

        formatted = format_iso8601(original)
        parsed = parse_iso_datetime(formatted)

        # Note: milliseconds precision, so microseconds may differ
        assert parsed.year == original.year
        assert parsed.month == original.month
        assert parsed.day == original.day
        assert parsed.hour == original.hour
        assert parsed.minute == original.minute
        assert parsed.second == original.second

    def test_current_timestamp_roundtrip(self):
        """Test get_current_timestamp can be parsed."""
        ts = get_current_timestamp()
        parsed = parse_iso_datetime(ts)

        assert parsed.tzinfo is not None
        assert parsed.year >= 2025  # Sanity check


class TestTimeUtilsEdgeCases:
    """Test edge cases for time utilities."""

    def test_parse_min_datetime(self):
        """Test parsing minimum datetime."""
        dt = parse_iso_datetime("0001-01-01T00:00:00Z")

        assert dt.year == 1
        assert dt.month == 1
        assert dt.day == 1

    def test_parse_max_datetime(self):
        """Test parsing maximum datetime."""
        dt = parse_iso_datetime("9999-12-31T23:59:59.999999Z")

        assert dt.year == 9999
        assert dt.month == 12
        assert dt.day == 31

    def test_parse_leap_second(self):
        """Test parsing with leap second (60 seconds)."""
        # Note: Python's datetime doesn't actually support leap seconds
        # but we test that the parsing logic doesn't crash
        try:
            dt = parse_iso_datetime("2026-03-26T10:30:60Z")
            # If it succeeds, verify it's a valid datetime
            assert dt.minute == 30
        except ValueError:
            # Python may reject leap seconds, which is fine
            pass

    def test_parse_midnight(self):
        """Test parsing midnight."""
        dt = parse_iso_datetime("2026-03-26T00:00:00Z")

        assert dt.hour == 0
        assert dt.minute == 0
        assert dt.second == 0

    def test_format_midnight(self):
        """Test formatting midnight."""
        dt = datetime(2026, 3, 26, 0, 0, 0, tzinfo=timezone.utc)
        formatted = format_iso8601(dt)

        assert formatted == "2026-03-26T00:00:00.000Z"

    def test_parse_different_timezone_formats(self):
        """Test parsing various timezone formats."""
        formats = [
            "2026-03-26T10:30:00Z",
            "2026-03-26T10:30:00+00:00",
            "2026-03-26T10:30:00-00:00",
        ]

        for fmt in formats:
            dt = parse_iso_datetime(fmt)
            dt_utc = dt.astimezone(timezone.utc)
            assert dt_utc.hour == 10
            assert dt_utc.minute == 30

    def test_format_daylight_saving_transition(self):
        """Test formatting during DST transition."""
        # This is mainly to ensure no crashes during DST transitions
        dt = datetime(2026, 3, 8, 2, 30, 0, tzinfo=timezone.utc)
        formatted = format_iso8601(dt)

        assert formatted == "2026-03-08T02:30:00.000Z"
