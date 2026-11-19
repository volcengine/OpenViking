# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Same-source Watch recovery decisions for failed derived indexing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.resource.watch_manager import (
    _SOURCE_RECOVERY_MAX_ATTEMPTS,
    WatchManager,
    _same_source_action,
)
from openviking.resource.watch_types import WatchRun, WatchTask

SHA = "a" * 40
CHECKPOINT = {"kind": "git", "head_sha": SHA, "instruction": ""}


def _task(
    *,
    status: str | None = "failed",
    attempts: int = 0,
    checkpoint: dict | None = None,
    error: str | None = "embedding Work failed",
) -> WatchTask:
    return WatchTask.from_dict(
        {
            "path": "https://github.com/example/repo.git",
            "to_uri": "viking://resources/example",
            "source_checkpoint": dict(CHECKPOINT if checkpoint is None else checkpoint),
            "last_status": status,
            "last_error": error,
            "last_task_id": "failed-import",
            "recovery_attempts": attempts,
        }
    )


def _failed(
    checkpoint: dict | None = None,
    previous_status: str | None = "failed",
    previous_attempts: int = 0,
):
    return WatchManager._result(
        "failed",
        {
            "import_complete": True,
            "source_checkpoint": dict(CHECKPOINT if checkpoint is None else checkpoint),
        },
        "embedding Work failed",
        connector=False,
        instruction="",
        previous_checkpoint=dict(CHECKPOINT if checkpoint is None else checkpoint),
        previous_status=previous_status,
        previous_attempts=previous_attempts,
    )


def test_failed_same_source_is_recovered_not_skipped():
    assert _same_source_action(CHECKPOINT, _task(status="failed")) == "refresh"


def test_completed_same_source_still_skips():
    assert _same_source_action(CHECKPOINT, _task(status="completed")) == "skip"


def test_legacy_completed_without_status_still_skips():
    assert _same_source_action(CHECKPOINT, _task(status=None)) == "skip"


def test_recovery_attempts_are_bounded():
    assert (
        _same_source_action(
            CHECKPOINT, _task(status="failed", attempts=_SOURCE_RECOVERY_MAX_ATTEMPTS)
        )
        == "exhausted"
    )


def test_new_source_always_refreshes():
    new_sha = {"kind": "git", "head_sha": "b" * 40, "instruction": ""}
    assert _same_source_action(new_sha, _task(status="failed", attempts=99)) == "refresh"


def test_first_failure_does_not_count_as_a_recovery_attempt():
    result = _failed(previous_status="completed")
    assert result.status == "failed"
    assert result.recovery_attempts == 0


def test_failed_recovery_increments_attempts():
    result = _failed(previous_status="failed")
    assert result.recovery_attempts == 1


def test_success_clears_attempts_and_keeps_plain_checkpoint():
    result = WatchManager._result(
        "completed",
        {
            "import_complete": True,
            "source_checkpoint": {**CHECKPOINT, "recovery_attempts": 1},
        },
        None,
        connector=False,
        instruction="",
        previous_checkpoint={**CHECKPOINT, "recovery_attempts": 1},
        previous_status="failed",
    )
    assert result.source_checkpoint == CHECKPOINT
    assert result.recovery_attempts == 0


def test_source_failure_without_checkpoint_counts_an_attempt():
    result = WatchManager._result(
        "failed",
        {"import_complete": False},
        "clone failed",
        connector=False,
        instruction="",
        previous_checkpoint=dict(CHECKPOINT),
        previous_status="failed",
        previous_attempts=1,
    )
    assert result.source_checkpoint is None
    assert result.recovery_attempts == 2


def test_new_source_failure_resets_the_budget():
    new_checkpoint = {"kind": "git", "head_sha": "b" * 40, "instruction": ""}
    result = WatchManager._result(
        "failed",
        {"import_complete": True, "source_checkpoint": new_checkpoint},
        "embedding Work failed",
        connector=False,
        instruction="",
        previous_checkpoint=dict(CHECKPOINT),
        previous_status="failed",
        previous_attempts=_SOURCE_RECOVERY_MAX_ATTEMPTS,
    )
    assert result.recovery_attempts == 0


def test_import_failure_keeps_no_checkpoint():
    result = WatchManager._result(
        "failed",
        {"import_complete": False},
        "clone failed",
        connector=False,
        instruction="",
        previous_checkpoint=None,
        previous_status=None,
    )
    assert result.source_checkpoint is None
    assert result.recovery_attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, attempts, outcome",
    [
        ("failed", 0, "refresh"),
        ("completed", 0, "completed"),
        ("failed", _SOURCE_RECOVERY_MAX_ATTEMPTS, "failed"),
    ],
)
async def test_same_source_submission(
    status, attempts, outcome, watch_store, watch_vfs, monkeypatch
):
    refresh = AsyncMock(return_value={"status": "success", "task_id": "new"})
    manager = WatchManager(
        watch_store, viking_fs=watch_vfs, resource_service=SimpleNamespace(refresh_resource=refresh)
    )
    monkeypatch.setattr(manager, "_probe_git_checkpoint", AsyncMock(return_value=CHECKPOINT))
    task = _task(status=status, attempts=attempts)
    result = await manager._submit_resource(WatchRun("watch", None, task), AsyncMock())
    if outcome == "refresh":
        assert result == {"status": "success", "task_id": "new"}
        refresh.assert_awaited_once()
    else:
        assert result.status == outcome
        refresh.assert_not_awaited()
        if outcome == "failed":
            assert (
                result.error
                == "Git Watch automatic recovery attempts exhausted: embedding Work failed"
            )


def test_legacy_record_defaults_to_zero_attempts():
    data = _task(status=None).to_storage_dict()
    del data["recovery_attempts"]
    del data["last_status"]
    legacy = WatchTask.from_dict(data)
    assert legacy.recovery_attempts == 0
    assert _same_source_action(CHECKPOINT, legacy) == "skip"
