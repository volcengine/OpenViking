# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A failed Watch submission must always release the attached run.

``WatchStore.finish_run`` only clears the row when the supplied ``run_id``
matches ``current_run_id``.  Passing ``None`` after a Task was attached is a
guaranteed no-op, so a tracker failure in the error path would leave the
Watch stuck until the recovery grace period expires.
"""

from types import SimpleNamespace

import pytest

from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_types import WatchRun, WatchTask


class _Store:
    def __init__(self) -> None:
        self.attached: list[tuple] = []
        self.finished: list[tuple] = []

    async def attach_task(self, task_id, run_id, *, started_at=None):
        self.attached.append((task_id, run_id, started_at))
        return True

    async def finish_run(self, task_id, run_id, result, *, started_at=None):
        self.finished.append((task_id, run_id, result))
        return True


class _Tracker:
    def __init__(self, record=None, fail_error=None) -> None:
        self.record = record
        self.fail_error = fail_error
        self.failed: list[tuple] = []

    async def get(self, *args, **kwargs):
        return self.record

    async def fail(self, run_id, error, **kwargs):
        self.failed.append((run_id, error))
        if self.fail_error is not None:
            raise self.fail_error


def _run() -> WatchRun:
    task = WatchTask(path="https://example.org/doc", to_uri="")
    return WatchRun(task_id="watch-1", run_id=None, task=task, started_at=123.0)


async def _execute_with(store, tracker) -> None:
    manager = WatchManager(
        store,
        locks=object(),
        viking_fs=object(),
        tracker=tracker,
        resource_service=object(),
    )

    async def submit(run, on_task_created):
        await on_task_created(SimpleNamespace(task_id="run-1"))
        raise ConnectionError("submission failed")

    manager._submit_resource = submit
    await manager._execute(_run())


@pytest.mark.asyncio
async def test_missing_tracker_record_still_releases_run():
    store = _Store()
    tracker = _Tracker(record=None)

    with pytest.raises(ConnectionError, match="submission failed"):
        await _execute_with(store, tracker)

    assert store.attached == [("watch-1", "run-1", 123.0)]
    assert store.finished[-1][1] == "run-1"
    assert store.finished[-1][2].status == "failed"


@pytest.mark.asyncio
async def test_tracker_failure_still_releases_run_and_preserves_error():
    store = _Store()
    record = SimpleNamespace(task_id="run-1", is_terminal=lambda: False)
    tracker = _Tracker(record=record, fail_error=RuntimeError("tracker unavailable"))

    with pytest.raises(ConnectionError, match="submission failed"):
        await _execute_with(store, tracker)

    assert tracker.failed == [("run-1", "submission failed")]
    assert store.finished[-1][1] == "run-1"
