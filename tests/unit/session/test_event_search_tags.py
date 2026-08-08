# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unit tests for event-memory auto-tagging: SessionMeta.event_search_tags.

These tests target pure serialization behavior on SessionMeta, so they don't
need a running OpenViking server.
"""

from openviking.session.session import SessionMeta


class TestSessionMetaEventSearchTags:
    def test_default_is_none(self):
        meta = SessionMeta(session_id="s1")
        assert meta.event_search_tags is None

    def test_round_trip_with_value(self):
        meta = SessionMeta(
            session_id="s1",
            event_search_tags=["team=search", "channel=web"],
        )
        restored = SessionMeta.from_dict(meta.to_dict())
        assert restored.event_search_tags == ["team=search", "channel=web"]

    def test_round_trip_empty_list(self):
        # Empty list is an explicit "no default tags" state and must survive
        # serialization distinctly from None.
        meta = SessionMeta(session_id="s1", event_search_tags=[])
        restored = SessionMeta.from_dict(meta.to_dict())
        assert restored.event_search_tags == []

    def test_missing_field_from_old_dict_is_none(self):
        # Sessions persisted before this feature have no key; load as None.
        old_dict = {"session_id": "s1"}
        restored = SessionMeta.from_dict(old_dict)
        assert restored.event_search_tags is None
