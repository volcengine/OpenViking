# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unit tests for event-tag three-state resolution during commit.

Precedence: an explicit commit-time value wins over the session default; an
empty list disables default-tag injection, and None means "use session default".
"""

from openviking.session.session import _resolve_event_search_tags


class TestResolveEventSearchTags:
    def test_none_commit_falls_back_to_session_default(self):
        assert _resolve_event_search_tags(None, ["team=search"]) == ["team=search"]

    def test_none_commit_and_none_default_is_empty(self):
        assert _resolve_event_search_tags(None, None) == []

    def test_commit_override_wins_over_default(self):
        assert _resolve_event_search_tags(["channel=app"], ["team=search"]) == ["channel=app"]

    def test_explicit_empty_commit_disables_default_tag_injection(self):
        # --no-event-tags / explicit [] must ignore the session default.
        assert _resolve_event_search_tags([], ["team=search"]) == []

    def test_values_are_normalized_and_deduped(self):
        assert _resolve_event_search_tags(["Team=Search", "team=search"], None) == ["team=search"]
