# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for QueueObserver health semantics."""

from openviking.storage.observers.queue_observer import QueueObserver
from openviking.storage.queuefs.named_queue import QueueStatus


class _FakeQueueManager:
    SEMANTIC = "Semantic"

    def __init__(self, statuses: dict[str, QueueStatus]):
        self._statuses = statuses
        self._queues = {}

    async def check_status(self) -> dict[str, QueueStatus]:
        return self._statuses


def test_historical_queue_errors_do_not_make_drained_queue_unhealthy() -> None:
    observer = QueueObserver(
        _FakeQueueManager(
            {
                "Semantic": QueueStatus(
                    pending=0,
                    in_progress=0,
                    processed=69,
                    requeue_count=1,
                    error_count=1,
                )
            }
        )
    )

    assert observer.has_errors() is False
    assert observer.is_healthy() is True

    table = observer.get_status_table()
    assert "Semantic" in table
    assert "Warnings:" not in table


def test_queue_errors_make_pending_queue_unhealthy() -> None:
    observer = QueueObserver(
        _FakeQueueManager(
            {
                "Semantic": QueueStatus(
                    pending=1,
                    in_progress=0,
                    processed=69,
                    error_count=1,
                )
            }
        )
    )

    assert observer.has_errors() is True
    assert observer.is_healthy() is False

    table = observer.get_status_table()
    assert "Warnings:" not in table


def test_queue_errors_make_in_progress_queue_unhealthy() -> None:
    observer = QueueObserver(
        _FakeQueueManager(
            {
                "Semantic": QueueStatus(
                    pending=0,
                    in_progress=1,
                    processed=69,
                    error_count=1,
                )
            }
        )
    )

    assert observer.has_errors() is True
    assert observer.is_healthy() is False
