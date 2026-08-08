# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for SessionCommitMsg.event_search_tags round-trip.

The Phase-2 queue message carries the resolved event tags from the committing
process to the queue worker; this field must survive to_dict/from_dict and keep
old producers (without the field) working.
"""

from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg


def _base_kwargs() -> dict:
    return {
        "task_id": "task-1",
        "session_id": "sess-1",
        "session_uri": "viking://sessions/sess-1",
        "archive_uri": "viking://sessions/sess-1/history/archive_001",
        "user": {"account_id": "acme", "user_id": "alice"},
    }


def test_default_event_search_tags_is_empty_list():
    msg = SessionCommitMsg(**_base_kwargs())
    assert msg.event_search_tags == []


def test_round_trip_preserves_event_search_tags():
    msg = SessionCommitMsg(**_base_kwargs(), event_search_tags=["team=search", "channel=web"])
    restored = SessionCommitMsg.from_dict(msg.to_dict())
    assert restored.event_search_tags == ["team=search", "channel=web"]


def test_from_dict_ignores_missing_event_search_tags():
    # Payloads produced before this field existed load with the default.
    restored = SessionCommitMsg.from_dict(_base_kwargs())
    assert restored.event_search_tags == []
