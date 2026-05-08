# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for path variable resolution."""

from datetime import datetime

import pytest

from openviking.core.path_variables import (
    CalendarVariableProvider,
    PathVariableResolver,
    has_path_variables,
    resolve_path_variables,
)


class TestCalendarVariableProvider:
    """Tests for calendar variable provider."""

    @pytest.fixture
    def sample_datetime(self):
        """Sample datetime for testing: 2026-05-07."""
        return datetime(2026, 5, 7, 14, 30, 0)

    def test_get_variables_returns_all_keys(self, sample_datetime):
        provider = CalendarVariableProvider(sample_datetime)
        variables = provider.get_variables()

        assert "today" in variables
        assert "yesterday" in variables
        assert "tomorrow" in variables
        assert "year" in variables
        assert "month" in variables
        assert "day" in variables
        assert "ym" in variables
        assert "quarter" in variables
        assert "yq" in variables
        assert "week" in variables
        assert "yw" in variables

    def test_today_variable(self, sample_datetime):
        provider = CalendarVariableProvider(sample_datetime)
        variables = provider.get_variables()

        assert variables["today"] == "2026/05/07"

    def test_yesterday_variable(self, sample_datetime):
        provider = CalendarVariableProvider(sample_datetime)
        variables = provider.get_variables()

        assert variables["yesterday"] == "2026/05/06"

    def test_tomorrow_variable(self, sample_datetime):
        provider = CalendarVariableProvider(sample_datetime)
        variables = provider.get_variables()

        assert variables["tomorrow"] == "2026/05/08"

    def test_yesterday_crosses_month_boundary(self):
        # Test with 2026-05-01 (yesterday is in April)
        dt = datetime(2026, 5, 1)
        provider = CalendarVariableProvider(dt)
        variables = provider.get_variables()

        assert variables["yesterday"] == "2026/04/30"

    def test_tomorrow_crosses_month_boundary(self):
        # Test with 2026-05-31 (tomorrow is in June)
        dt = datetime(2026, 5, 31)
        provider = CalendarVariableProvider(dt)
        variables = provider.get_variables()

        assert variables["tomorrow"] == "2026/06/01"

    def test_year_variable(self, sample_datetime):
        provider = CalendarVariableProvider(sample_datetime)
        variables = provider.get_variables()

        assert variables["year"] == "2026"

    def test_month_variable(self, sample_datetime):
        provider = CalendarVariableProvider(sample_datetime)
        variables = provider.get_variables()

        assert variables["month"] == "05"

    def test_day_variable(self, sample_datetime):
        provider = CalendarVariableProvider(sample_datetime)
        variables = provider.get_variables()

        assert variables["day"] == "07"

    def test_ym_variable(self, sample_datetime):
        provider = CalendarVariableProvider(sample_datetime)
        variables = provider.get_variables()

        assert variables["ym"] == "2026/05"

    def test_quarter_q2(self, sample_datetime):
        provider = CalendarVariableProvider(sample_datetime)
        variables = provider.get_variables()

        assert variables["quarter"] == "Q2"
        assert variables["yq"] == "2026/Q2"

    @pytest.mark.parametrize(
        "month, expected_quarter",
        [
            (1, "Q1"),
            (3, "Q1"),
            (4, "Q2"),
            (6, "Q2"),
            (7, "Q3"),
            (9, "Q3"),
            (10, "Q4"),
            (12, "Q4"),
        ],
    )
    def test_quarter_bounds(self, month, expected_quarter):
        dt = datetime(2026, month, 15)
        provider = CalendarVariableProvider(dt)
        variables = provider.get_variables()

        assert variables["quarter"] == expected_quarter

    def test_week_variable(self, sample_datetime):
        provider = CalendarVariableProvider(sample_datetime)
        variables = provider.get_variables()

        # 2026-05-07 is in week 19 (ISO week numbering)
        assert variables["week"] == "19"

    def test_yw_variable(self, sample_datetime):
        provider = CalendarVariableProvider(sample_datetime)
        variables = provider.get_variables()

        # 2026-05-07 is in week 19
        assert variables["yw"] == "2026/w19"

    def test_resolve_individual_key(self, sample_datetime):
        provider = CalendarVariableProvider(sample_datetime)

        assert provider.resolve("year") == "2026"
        assert provider.resolve("month") == "05"
        assert provider.resolve("day") == "07"
        assert provider.resolve("nonexistent") is None

    def test_namespace_property(self):
        provider = CalendarVariableProvider()
        assert provider.namespace == "calendar"

    def test_default_provider_uses_current_time_each_call(self):
        """Test that default provider (no fixed dt) gets current time each call."""
        # Create provider without fixed dt
        provider = CalendarVariableProvider()

        # Get variables multiple times - each call uses current time
        variables1 = provider.get_variables()
        variables2 = provider.get_variables()

        # Should return same date (since we call within same second)
        assert variables1["today"] == variables2["today"]

    def test_fixed_dt_provider_does_not_update(self):
        """Test that provider with explicit dt always uses that date."""
        fixed_dt = datetime(2026, 5, 7, 14, 30, 0)
        provider = CalendarVariableProvider(fixed_dt)

        variables1 = provider.get_variables()
        assert variables1["today"] == "2026/05/07"

        variables2 = provider.get_variables()
        assert variables2["today"] == "2026/05/07"


class TestPathVariableResolver:
    """Tests for path variable resolver."""

    @pytest.fixture
    def sample_datetime(self):
        """Sample datetime for testing: 2026-05-07."""
        return datetime(2026, 5, 7, 14, 30, 0)

    @pytest.fixture
    def resolver(self):
        """Resolver with default providers."""
        return PathVariableResolver()

    def test_resolve_no_variables_returns_unchanged(self, resolver):
        uri = "viking://resources/docs/api.md"
        assert resolver.resolve(uri) == uri

    def test_resolve_single_calendar_variable(self, resolver, sample_datetime):
        uri = "viking://resources/emails/{calendar:year}/inbox"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/emails/2026/inbox"

    def test_resolve_multiple_calendar_variables(self, resolver, sample_datetime):
        uri = "viking://resources/logs/{calendar:year}/{calendar:month}/app.log"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/logs/2026/05/app.log"

    def test_resolve_today_variable(self, resolver, sample_datetime):
        uri = "viking://resources/emails/{calendar:today}/inbox"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/emails/2026/05/07/inbox"

    def test_resolve_yesterday_variable(self, resolver, sample_datetime):
        uri = "viking://resources/emails/{calendar:yesterday}/inbox"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/emails/2026/05/06/inbox"

    def test_resolve_tomorrow_variable(self, resolver, sample_datetime):
        uri = "viking://resources/emails/{calendar:tomorrow}/inbox"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/emails/2026/05/08/inbox"

    def test_resolve_ym_variable(self, resolver, sample_datetime):
        uri = "viking://resources/reports/{calendar:ym}/summary.pdf"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/reports/2026/05/summary.pdf"

    def test_resolve_quarter_variables(self, resolver, sample_datetime):
        uri = "viking://resources/quarterly/{calendar:yq}/report.pdf"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/quarterly/2026/Q2/report.pdf"

    def test_resolve_week_variables(self, resolver, sample_datetime):
        uri = "viking://resources/weekly/{calendar:yw}/summary.pdf"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/weekly/2026/w19/summary.pdf"

    def test_resolve_unknown_namespace_raises_error(self, resolver):
        uri = "viking://resources/{env:production}/config"

        with pytest.raises(ValueError, match="Cannot resolve variables"):
            resolver.resolve(uri)

    def test_resolve_unknown_key_raises_error(self, resolver):
        uri = "viking://resources/{calendar:nonexistent}/path"

        with pytest.raises(ValueError, match="Cannot resolve variables"):
            resolver.resolve(uri)

    def test_has_variables_true(self, resolver):
        assert resolver.has_variables("viking://resources/{calendar:year}/docs") is True
        assert resolver.has_variables("{calendar:today}") is True

    def test_has_variables_false(self, resolver):
        assert resolver.has_variables("viking://resources/docs") is False
        assert resolver.has_variables("") is False
        assert resolver.has_variables("no variables here") is False

    def test_extract_variables(self, resolver):
        uri = "viking://resources/{calendar:year}/{calendar:month}/{calendar:day}"
        variables = resolver.extract_variables(uri)

        assert variables == {"calendar:year", "calendar:month", "calendar:day"}

    def test_extract_variables_none(self, resolver):
        uri = "viking://resources/docs/api.md"
        variables = resolver.extract_variables(uri)

        assert variables == set()

    def test_multiple_occurrences_same_variable(self, resolver, sample_datetime):
        uri = "viking://resources/{calendar:year}/backup/{calendar:year}/data"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/2026/backup/2026/data"


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    @pytest.fixture
    def sample_datetime(self):
        """Sample datetime for testing: 2026-05-07."""
        return datetime(2026, 5, 7, 14, 30, 0)

    def test_resolve_path_variables(self, sample_datetime):
        uri = "viking://resources/emails/{calendar:today}/inbox"
        resolved = resolve_path_variables(uri, dt=sample_datetime)

        assert resolved == "viking://resources/emails/2026/05/07/inbox"

    def test_has_path_variables_true(self):
        assert has_path_variables("viking://resources/{calendar:year}/docs") is True

    def test_has_path_variables_false(self):
        assert has_path_variables("viking://resources/docs") is False


class TestRealWorldExamples:
    """Tests for real-world usage patterns."""

    @pytest.fixture
    def sample_datetime(self):
        """Sample datetime for testing: 2026-05-07."""
        return datetime(2026, 5, 7, 14, 30, 0)

    @pytest.fixture
    def resolver(self):
        """Resolver with default providers."""
        return PathVariableResolver()

    def test_email_organization(self, resolver, sample_datetime):
        uri = "viking://resources/emails/{calendar:today}/inbox"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/emails/2026/05/07/inbox"

    def test_log_rotation(self, resolver, sample_datetime):
        uri = "viking://resources/logs/{calendar:year}/{calendar:month}/{calendar:day}/app.log"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/logs/2026/05/07/app.log"

    def test_daily_backups(self, resolver, sample_datetime):
        uri = "viking://resources/backups/{calendar:ym}/backup_{calendar:today}.zip"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/backups/2026/05/backup_2026/05/07.zip"

    def test_quarterly_reports(self, resolver, sample_datetime):
        uri = "viking://resources/reports/{calendar:yq}/summary.pdf"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/reports/2026/Q2/summary.pdf"

    def test_weekly_summaries(self, resolver, sample_datetime):
        uri = "viking://resources/summaries/{calendar:yw}/week.pdf"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/summaries/2026/w19/week.pdf"

    def test_yesterday_logs(self, resolver, sample_datetime):
        uri = "viking://resources/logs/{calendar:yesterday}/app.log"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/logs/2026/05/06/app.log"

    def test_tomorrow_tasks(self, resolver, sample_datetime):
        uri = "viking://resources/tasks/{calendar:tomorrow}/todo.md"
        resolved = resolver.resolve(uri, dt=sample_datetime)

        assert resolved == "viking://resources/tasks/2026/05/08/todo.md"
