# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared Watch fixtures over local RAGFS."""

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import pytest_asyncio

from openviking.resource.watch_manager import WatchManager, WatchPathLocks
from openviking.resource.watch_store import WatchStore
from openviking.resource.watch_store_file import FileWatchStore
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker


async def reserve_watch(store, task_id, *, target_uri, manual=False, run_id=None):
    """Exercise snapshot reservation and optional Task association in store tests."""
    candidate = await store.get_candidate(task_id)
    if candidate is None or candidate.to_uri != target_uri:
        return None
    run = await store.begin_run(candidate, manual=manual)
    if run is not None and run_id is not None:
        assert await store.attach_task(task_id, run_id, started_at=run.started_at)
        run = replace(run, run_id=run_id)
    return run


def local_viking_fs(root: Path):
    from openviking.storage.viking_fs import VikingFS
    from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
    from openviking_cli.utils.config.agfs_config import AGFSConfig

    agfs = create_agfs_client(RagfsBindingConfig(agfs=AGFSConfig(path=str(root), backend="local")))
    return VikingFS(agfs=agfs)


async def shape_watch(store: WatchStore, task_id: str, **values: Any) -> None:
    """Set ``next_run_at``, ``current_run_id`` or ``run_started_at`` (epoch) directly."""
    assert isinstance(store, FileWatchStore)
    row = store._rows[task_id]
    task = row.task
    if "next_run_at" in values:
        at = values.pop("next_run_at")
        task = task.model_copy(
            update={
                # Watch timestamps are aware UTC: a naive value would raise on
                # the due comparison against an aware "now" (issue #3268).
                "next_execution_time": datetime.fromtimestamp(at, tz=timezone.utc)
                if at
                else None
            }
        )
    await store._commit({task_id: replace(row, task=task, **values)})


@pytest_asyncio.fixture
async def watch_vfs(tmp_path: Path):
    return local_viking_fs(tmp_path / "agfs")


@pytest_asyncio.fixture
async def watch_tracker(watch_vfs, monkeypatch):
    tracker = TaskTracker(PersistentTaskStore(watch_vfs._async_agfs))
    monkeypatch.setattr("openviking.resource.watch_manager.get_task_tracker", lambda: tracker)
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    yield tracker
    await tracker.close()


@pytest_asyncio.fixture
async def watch_store(watch_vfs) -> AsyncIterator[FileWatchStore]:
    store = FileWatchStore(watch_vfs)
    await store.initialize()
    yield store
    await store.close()


@pytest_asyncio.fixture
async def watch_manager(watch_store: FileWatchStore, watch_vfs) -> WatchManager:
    return WatchManager(watch_store, WatchPathLocks(watch_vfs))
