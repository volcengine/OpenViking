# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Focused tests for QueueManager concurrency selection."""

from openviking.storage.queuefs.queue_manager import QueueManager


def test_external_parse_concurrent_uses_configured_value() -> None:
    manager = QueueManager(agfs=object(), max_concurrent_external_parse=9)

    assert manager._max_concurrent_for_queue(manager.EXTERNAL_PARSE) == 9


def test_session_commit_concurrent_stays_at_four() -> None:
    manager = QueueManager(agfs=object(), max_concurrent_external_parse=9)

    assert manager._max_concurrent_for_queue(manager.SESSION_COMMIT) == 4
