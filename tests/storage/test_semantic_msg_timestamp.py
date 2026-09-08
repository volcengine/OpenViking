# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SemanticMsg.timestamp must reflect per-instance creation time.

Regression guard: the field default used to be evaluated once at class
definition (module import), so every message created by a long-running
process carried the process-start timestamp instead of its own creation
time.
"""

from datetime import datetime

from openviking.storage.queuefs.semantic_msg import SemanticMsg


def _now_epoch() -> int:
    return int(datetime.now().timestamp())


def test_new_message_timestamp_is_own_creation_time():
    before = _now_epoch()
    msg = SemanticMsg(uri="viking://res/a", context_type="resource")
    after = _now_epoch()

    # Instance-level attribute (falls back to the class attribute only when
    # unset — the frozen-timestamp regression is exactly that fallback).
    assert "timestamp" in msg.__dict__
    assert before <= msg.timestamp <= after


def test_messages_do_not_share_a_frozen_timestamp():
    first = SemanticMsg(uri="viking://res/a", context_type="resource")
    first.timestamp = 1111111111  # mutate one instance only

    second = SemanticMsg(uri="viking://res/b", context_type="memory")
    assert second.timestamp != 1111111111
    assert second.timestamp != first.timestamp


def test_to_dict_carries_creation_timestamp():
    msg = SemanticMsg(uri="viking://res/a", context_type="resource")
    data = msg.to_dict()
    assert data["timestamp"] == msg.timestamp
    assert abs(data["timestamp"] - _now_epoch()) <= 5


def test_from_dict_preserves_stored_timestamp():
    msg = SemanticMsg.from_dict(
        {"uri": "viking://res/a", "context_type": "resource", "timestamp": 1234567890}
    )
    assert msg.timestamp == 1234567890


def test_from_dict_without_timestamp_still_gets_fresh_one():
    msg = SemanticMsg.from_dict({"uri": "viking://res/a", "context_type": "resource"})
    assert "timestamp" in msg.__dict__
    assert abs(msg.timestamp - _now_epoch()) <= 5
