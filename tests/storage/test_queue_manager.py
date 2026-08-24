# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Focused tests for QueueManager concurrency selection."""

import asyncio
import os
import subprocess
import sys
import threading

import pytest

from openviking.storage.queuefs.queue_hook import ProcessResult
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


@pytest.mark.asyncio
async def test_worker_only_schedules_consume_one() -> None:
    manager = QueueManager(agfs=object())
    stop_event = threading.Event()

    class QueueStub:
        name = "Semantic"

        def __init__(self):
            self.size_calls = 0
            self.consume_calls = 0

        def has_dequeue_handler(self):
            return True

        async def size(self):
            self.size_calls += 1
            return 1 if self.size_calls == 1 else 0

        async def consume_one(self):
            self.consume_calls += 1
            stop_event.set()
            return ProcessResult.success()

    queue = QueueStub()
    await asyncio.wait_for(
        manager._worker_async_concurrent(queue, stop_event, 2),
        timeout=1,
    )

    assert queue.consume_calls == 1
