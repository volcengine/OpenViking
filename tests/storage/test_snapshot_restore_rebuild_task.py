# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role.ROOT)


class _Tracker:
    def __init__(self) -> None:
        self.completed: list[tuple[str, dict]] = []
        self.failed: list[tuple[str, str]] = []

    async def register_running_task(self, task_id: str, **_kwargs) -> str:
        return "work-1"

    async def start(self, task_id: str, **_kwargs) -> None:
        return None

    async def complete(self, task_id: str, payload: dict | None = None, **_kwargs) -> None:
        self.completed.append((task_id, payload or {}))

    async def fail(self, task_id: str, message: str, **_kwargs) -> None:
        self.failed.append((task_id, message))

    async def unregister_running_task(self, task_id: str) -> None:
        return None


def _patch_restore_dependencies(monkeypatch: pytest.MonkeyPatch, tracker: _Tracker) -> None:
    import openviking.service.reindex_executor as reindex_mod
    import openviking.service.task_tracker as tracker_mod
    import openviking.service.task_work_index as work_mod

    monkeypatch.setattr(tracker_mod, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(reindex_mod, "ReindexExecutor", SimpleNamespace)
    monkeypatch.setattr(
        work_mod,
        "bind_task_context",
        lambda *_args, **_kwargs: contextlib.nullcontext(),
    )


def _restore_fs(rebuild_result: bool) -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs._restore_reindex_context = lambda ctx: ctx

    async def rebuild(*_args, **_kwargs) -> bool:
        return rebuild_result

    fs._run_vector_rebuild = rebuild
    return fs


@pytest.mark.asyncio
async def test_tracked_restore_rebuild_fails_when_a_vector_task_fails(monkeypatch):
    """The pollable restore task must not report completed after a rebuild failure."""
    tracker = _Tracker()
    _patch_restore_dependencies(monkeypatch, tracker)
    fs = _restore_fs(False)

    await fs._run_restore_rebuild_tracked(
        "task-1",
        {("reindex_file", "viking://resources/a.md", 0)},
        _ctx(),
    )

    assert tracker.completed == []
    assert len(tracker.failed) == 1
    assert "1 of 1" in tracker.failed[0][1]


@pytest.mark.asyncio
async def test_tracked_restore_rebuild_completes_when_all_tasks_succeed(monkeypatch):
    tracker = _Tracker()
    _patch_restore_dependencies(monkeypatch, tracker)
    fs = _restore_fs(True)

    await fs._run_restore_rebuild_tracked(
        "task-1",
        {("reindex_file", "viking://resources/a.md", 0)},
        _ctx(),
    )

    assert tracker.failed == []
    assert tracker.completed == [("task-1", {"status": "completed", "task_count": 1})]
