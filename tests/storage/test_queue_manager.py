# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Focused tests for QueueManager concurrency selection."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from collections import deque
from typing import Any, Dict, Optional

from openviking.storage.queuefs.named_queue import QueueStatus
from openviking.storage.queuefs.queue_manager import QueueManager


def test_queuefs_package_imports_in_a_clean_process(tmp_path) -> None:
    env = os.environ.copy()
    env["OPENVIKING_CONFIG_FILE"] = str(tmp_path / "missing-ov.conf")

    subprocess.run(
        [sys.executable, "-c", "from openviking.storage.queuefs import QueueManager"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_queue_concurrency_uses_separate_configured_values() -> None:
    manager = QueueManager(
        agfs=object(),
        max_concurrent_external_parse=9,
        max_concurrent_add_resource=7,
        max_concurrent_session_commit=5,
    )

    assert manager._max_concurrent_for_queue(manager.EXTERNAL_PARSE) == 9
    assert manager._max_concurrent_for_queue(manager.ADD_RESOURCE) == 7
    assert manager._max_concurrent_for_queue(manager.SESSION_COMMIT) == 5


def _session_commit_queue_item(msg_id: str, session_id: str) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "data": json.dumps(
            {
                "task_id": msg_id,
                "session_id": session_id,
                "session_uri": f"viking://user/alice/sessions/{session_id}",
                "archive_uri": f"viking://user/alice/sessions/{session_id}/history/archive_001",
                "user": {"account_id": "acme", "user_id": "alice"},
            }
        ),
    }


class _FakeSessionCommitQueue:
    name = QueueManager.SESSION_COMMIT

    def __init__(
        self,
        messages: list[Dict[str, Any]],
        *,
        block_first: bool = False,
    ) -> None:
        self._messages = deque(messages)
        self.block_first = block_first
        self.started: list[str] = []
        self.acked: list[str] = []
        self.in_progress_count = 0
        self.processed_count = 0
        self.error_count = 0
        self.errors: list[str] = []
        self.first_started = asyncio.Event()
        self.second_started = asyncio.Event()
        self.release_first = asyncio.Event()

    def has_dequeue_handler(self) -> bool:
        return True

    async def size(self) -> int:
        return len(self._messages)

    async def dequeue_raw(self) -> Optional[Dict[str, Any]]:
        if not self._messages:
            return None
        return self._messages.popleft()

    async def process_dequeued(self, data: Dict[str, Any]) -> None:
        msg_id = str(data["id"])
        self.started.append(msg_id)
        if len(self.started) == 1:
            self.first_started.set()
            if self.block_first:
                await self.release_first.wait()
        elif len(self.started) == 2:
            self.second_started.set()
        self._on_process_success()

    async def ack(self, msg_id: str, message: Optional[Dict[str, Any]] = None) -> None:
        self.acked.append(msg_id)

    def _on_dequeue_start(self) -> None:
        self.in_progress_count += 1

    def _on_process_success(self) -> None:
        self.in_progress_count -= 1
        self.processed_count += 1

    def _on_process_error(self, error_msg: str, data: Optional[Dict[str, Any]] = None) -> None:
        self.in_progress_count -= 1
        self.error_count += 1
        self.errors.append(error_msg)

    async def get_status(self) -> QueueStatus:
        return QueueStatus(
            pending=len(self._messages),
            in_progress=self.in_progress_count,
            processed=self.processed_count,
            error_count=self.error_count,
        )


async def test_session_commit_worker_serializes_same_session() -> None:
    manager = QueueManager(agfs=object())
    manager._SESSION_COMMIT_POLL_INTERVAL = 0.01
    queue = _FakeSessionCommitQueue(
        [
            _session_commit_queue_item("commit-1", "session-a"),
            _session_commit_queue_item("commit-2", "session-a"),
        ],
        block_first=True,
    )
    stop_event = threading.Event()
    worker = asyncio.create_task(
        manager._worker_async_session_fifo(queue, stop_event, max_concurrent=2)
    )

    await asyncio.wait_for(queue.first_started.wait(), timeout=1.0)
    await asyncio.sleep(0.05)

    assert queue.started == ["commit-1"]
    assert queue.in_progress_count == 2
    assert queue.acked == []
    assert queue.errors == []
    manager._queues[manager.SESSION_COMMIT] = queue
    assert not await manager.is_all_complete(manager.SESSION_COMMIT)

    queue.release_first.set()
    await asyncio.wait_for(queue.second_started.wait(), timeout=1.0)
    stop_event.set()
    await asyncio.wait_for(worker, timeout=1.0)

    assert queue.started == ["commit-1", "commit-2"]
    assert queue.acked == ["commit-1", "commit-2"]
    assert queue.in_progress_count == 0


async def test_session_commit_worker_allows_different_sessions_concurrently() -> None:
    manager = QueueManager(agfs=object())
    manager._SESSION_COMMIT_POLL_INTERVAL = 0.01
    queue = _FakeSessionCommitQueue(
        [
            _session_commit_queue_item("commit-1", "session-a"),
            _session_commit_queue_item("commit-2", "session-b"),
        ],
        block_first=True,
    )
    stop_event = threading.Event()
    worker = asyncio.create_task(
        manager._worker_async_session_fifo(queue, stop_event, max_concurrent=2)
    )

    await asyncio.wait_for(queue.second_started.wait(), timeout=1.0)

    assert queue.started == ["commit-1", "commit-2"]
    assert queue.in_progress_count == 1
    assert queue.acked == ["commit-2"]
    assert queue.errors == []

    queue.release_first.set()
    stop_event.set()
    await asyncio.wait_for(worker, timeout=1.0)

    assert queue.acked == ["commit-2", "commit-1"]
    assert queue.in_progress_count == 0


async def test_session_commit_worker_lookahead_reaches_later_different_session() -> None:
    manager = QueueManager(agfs=object())
    manager._SESSION_COMMIT_POLL_INTERVAL = 0.01
    queue = _FakeSessionCommitQueue(
        [
            _session_commit_queue_item("commit-a1", "session-a"),
            _session_commit_queue_item("commit-a2", "session-a"),
            _session_commit_queue_item("commit-a3", "session-a"),
            _session_commit_queue_item("commit-b1", "session-b"),
        ],
        block_first=True,
    )
    stop_event = threading.Event()
    worker = asyncio.create_task(
        manager._worker_async_session_fifo(queue, stop_event, max_concurrent=3)
    )

    await asyncio.wait_for(queue.second_started.wait(), timeout=1.0)

    assert queue.started == ["commit-a1", "commit-b1"]
    assert queue.in_progress_count == 3
    assert queue.acked == ["commit-b1"]
    assert len(queue._messages) == 0

    queue.release_first.set()
    stop_event.set()
    await asyncio.wait_for(worker, timeout=1.0)

    assert queue.started == ["commit-a1", "commit-b1", "commit-a2", "commit-a3"]
    assert queue.acked == ["commit-b1", "commit-a1", "commit-a2", "commit-a3"]
    assert queue.in_progress_count == 0


async def test_session_commit_worker_bounds_deferred_messages() -> None:
    manager = QueueManager(agfs=object())
    manager._SESSION_COMMIT_POLL_INTERVAL = 0.01
    queue = _FakeSessionCommitQueue(
        [_session_commit_queue_item(f"commit-{i}", "session-a") for i in range(70)],
        block_first=True,
    )
    stop_event = threading.Event()
    worker = asyncio.create_task(
        manager._worker_async_session_fifo(queue, stop_event, max_concurrent=3)
    )

    await asyncio.wait_for(queue.first_started.wait(), timeout=1.0)
    await asyncio.sleep(0.05)

    assert queue.started == ["commit-0"]
    assert queue.in_progress_count == 65
    assert len(queue._messages) == 5

    queue.release_first.set()
    stop_event.set()
    await asyncio.wait_for(worker, timeout=1.0)

    assert len(queue.started) == 65
    assert len(queue.acked) == 65
    assert queue.in_progress_count == 0
    assert len(queue._messages) == 5
