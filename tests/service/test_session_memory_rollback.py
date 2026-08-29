# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for conflict-safe Session memory rollback."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.service.session_memory_rollback import (
    SessionMemoryRollback,
    _missing,
    _present,
    _validate_diff_structure,
    build_rollback_plan,
)
from openviking_cli.exceptions import FailedPreconditionError

URI = "viking://user/alice/memories/preferences/editor.md"


@pytest.fixture(autouse=True)
def _initialized_task_tracker():
    tracker = MagicMock()
    tracker.has_running = AsyncMock(return_value=False)
    with patch(
        "openviking.service.session_memory_rollback.get_task_tracker",
        return_value=tracker,
    ):
        yield tracker


def _diff(*operations):
    return {"schema_version": 2, "operation_order": list(operations)}


def test_plan_reverses_archives_and_operations_newest_first():
    archive_1 = _diff(
        {
            "kind": "add",
            "uri": URI,
            "memory_type": "preferences",
            "after_raw": "created",
        },
        {
            "kind": "update",
            "uri": URI,
            "memory_type": "preferences",
            "before_raw": "created",
            "after_raw": "first commit",
        },
    )
    archive_2 = _diff(
        {
            "kind": "update",
            "uri": URI,
            "memory_type": "preferences",
            "before_raw": "first commit",
            "after_raw": "second commit",
        }
    )

    plan = build_rollback_plan(
        [("archive_2", archive_2), ("archive_1", archive_1)],
        {URI: _present("second commit")},
        force=False,
    )

    assert plan["summary"] == {"planned": 3, "conflicts": 0, "skipped": 0}
    assert [item["action"] for item in plan["operations"]] == ["write", "write", "delete"]
    assert [item["content"] for item in plan["operations"]] == [
        "first commit",
        "created",
        None,
    ]


def test_plan_restores_deleted_file():
    plan = build_rollback_plan(
        [
            (
                "archive_1",
                _diff(
                    {
                        "kind": "delete",
                        "uri": URI,
                        "memory_type": "preferences",
                        "deleted_raw": "deleted snapshot",
                    }
                ),
            )
        ],
        {URI: _missing()},
        force=False,
    )

    assert plan["summary"]["planned"] == 1
    assert plan["operations"][0]["action"] == "write"
    assert plan["operations"][0]["content"] == "deleted snapshot"


def test_plan_reports_external_change_as_conflict():
    plan = build_rollback_plan(
        [
            (
                "archive_1",
                _diff(
                    {
                        "kind": "update",
                        "uri": URI,
                        "before_raw": "before",
                        "after_raw": "committed",
                    }
                ),
            )
        ],
        {URI: _present("edited externally")},
        force=False,
    )

    assert plan["summary"] == {"planned": 0, "conflicts": 1, "skipped": 0}
    assert plan["operations"][0]["reason"] == "current_content_changed"


def test_force_skips_conflict_and_older_operations_for_same_uri():
    newer = _diff({"kind": "update", "uri": URI, "before_raw": "one", "after_raw": "two"})
    older = _diff({"kind": "add", "uri": URI, "after_raw": "one"})

    plan = build_rollback_plan(
        [("archive_2", newer), ("archive_1", older)],
        {URI: _present("external")},
        force=True,
    )

    assert plan["summary"] == {"planned": 0, "conflicts": 0, "skipped": 2}
    assert [item["reason"] for item in plan["operations"]] == [
        "current_content_changed",
        "blocked_by_newer_conflict",
    ]


def test_legacy_diff_without_raw_snapshot_is_never_guessed():
    plan = build_rollback_plan(
        [("archive_1", {"operations": {"adds": [{"uri": URI, "after": "new"}]}})],
        {URI: _present("new")},
        force=False,
    )

    assert plan["summary"]["conflicts"] == 1
    assert plan["operations"][0]["reason"] == "legacy_snapshot_incomplete"


def test_schema_v2_rejects_truncated_operation_order():
    with pytest.raises(ValueError, match="does not match"):
        _validate_diff_structure(
            {
                "schema_version": 2,
                "operations": {
                    "adds": [{"uri": URI, "after_raw": "new"}],
                    "updates": [],
                    "deletes": [],
                },
                "operation_order": [],
            }
        )


def test_semantically_identical_memory_serializations_do_not_conflict():
    expected = 'body\n\n<!-- MEMORY_FIELDS\n{"memory_type":"preferences","version":1}\n-->'
    actual = 'body\n\n<!-- MEMORY_FIELDS\n{"version": 1, "memory_type": "preferences"}\n-->'
    plan = build_rollback_plan(
        [("archive_1", _diff({"kind": "add", "uri": URI, "after_raw": expected}))],
        {URI: _present(actual)},
        force=False,
    )

    assert plan["summary"]["planned"] == 1
    assert plan["summary"]["conflicts"] == 0


class _FakeAgfs:
    def __init__(self):
        self.pathlock_acquire_tree = AsyncMock(return_value={"owner_id": "rollback"})
        self.pathlock_acquire_exact_batch = AsyncMock(return_value={"owner_id": "rollback"})
        self.pathlock_release = AsyncMock()


@pytest.mark.asyncio
async def test_run_rechecks_commit_state_after_acquiring_session_tree_lock():
    fs = MagicMock()
    fs._async_agfs = _FakeAgfs()
    fs._uri_to_path.side_effect = lambda uri, ctx=None: uri
    rollback = SessionMemoryRollback.__new__(SessionMemoryRollback)
    rollback._viking_fs = fs
    rollback._memory_updater = MagicMock()
    rollback._load_archive_diffs = AsyncMock()
    tracker = MagicMock()
    tracker.has_running = AsyncMock(return_value=True)
    ctx = MagicMock()

    with (
        patch(
            "openviking.service.session_memory_rollback.get_task_tracker",
            return_value=tracker,
        ),
        pytest.raises(FailedPreconditionError, match="commit in progress"),
    ):
        await rollback.run(
            "session-1", ctx, dry_run=False, force=False, delete_session=True
        )

    tracker.has_running.assert_awaited_once_with(
        "session_commit",
        "session-1",
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
    )
    rollback._load_archive_diffs.assert_not_awaited()
    fs._async_agfs.pathlock_release.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_returns_applied_statuses_and_refreshes_after_unlock():
    fs = MagicMock()
    fs._async_agfs = _FakeAgfs()
    fs._uri_to_path.side_effect = lambda uri, ctx=None: uri
    fs.rm = AsyncMock()
    rollback = SessionMemoryRollback.__new__(SessionMemoryRollback)
    rollback._viking_fs = fs
    rollback._memory_updater = MagicMock()
    rollback._load_archive_diffs = AsyncMock(
        return_value=[("archive_1", _diff({"kind": "add", "uri": URI, "after_raw": "new"}))]
    )
    rollback._validate_and_collect_uris = MagicMock(return_value={URI})
    rollback._read_current_states = AsyncMock(return_value={URI: _present("new")})
    rollback._refresh = AsyncMock(return_value={"vectorization_enqueued": 1})
    ctx = MagicMock()

    result = await rollback.run("session-1", ctx, dry_run=False, force=False, delete_session=True)

    assert result["status"] == "completed"
    assert result["operations"][0]["status"] == "applied"
    assert result["summary"]["applied"] == 1
    assert result["session_deleted"] is True
    assert fs._async_agfs.pathlock_release.await_count == 2
    rollback._refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_dry_run_never_mutates_storage_or_refreshes_indexes():
    fs = MagicMock()
    fs._async_agfs = _FakeAgfs()
    fs._uri_to_path.side_effect = lambda uri, ctx=None: uri
    fs.rm = AsyncMock()
    fs.write_file = AsyncMock()
    rollback = SessionMemoryRollback.__new__(SessionMemoryRollback)
    rollback._viking_fs = fs
    rollback._memory_updater = MagicMock()
    rollback._load_archive_diffs = AsyncMock(
        return_value=[("archive_1", _diff({"kind": "add", "uri": URI, "after_raw": "new"}))]
    )
    rollback._validate_and_collect_uris = MagicMock(return_value={URI})
    rollback._read_current_states = AsyncMock(return_value={URI: _present("new")})
    rollback._refresh = AsyncMock()

    result = await rollback.run(
        "session-1", MagicMock(), dry_run=True, force=False, delete_session=True
    )

    assert result["status"] == "ready"
    assert result["delete_session"] is True
    assert result["session_deleted"] is False
    fs.rm.assert_not_awaited()
    fs.write_file.assert_not_awaited()
    rollback._refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_delete_failure_compensates_memory_changes():
    fs = MagicMock()
    fs._async_agfs = _FakeAgfs()
    fs._uri_to_path.side_effect = lambda uri, ctx=None: uri
    fs.rm = AsyncMock(side_effect=[None, RuntimeError("session delete failed")])
    fs.write_file = AsyncMock()
    rollback = SessionMemoryRollback.__new__(SessionMemoryRollback)
    rollback._viking_fs = fs
    rollback._memory_updater = MagicMock()
    rollback._load_archive_diffs = AsyncMock(
        return_value=[("archive_1", _diff({"kind": "add", "uri": URI, "after_raw": "new"}))]
    )
    rollback._validate_and_collect_uris = MagicMock(return_value={URI})
    rollback._read_current_states = AsyncMock(return_value={URI: _present("new")})

    with pytest.raises(RuntimeError, match="session delete failed"):
        await rollback.run(
            "session-1", MagicMock(), dry_run=False, force=False, delete_session=True
        )

    fs.write_file.assert_awaited_once()
    assert fs.write_file.await_args.args[:2] == (URI, "new")


@pytest.mark.asyncio
async def test_apply_failure_compensates_every_original_state():
    fs = MagicMock()
    fs.rm = AsyncMock()
    fs.write_file = AsyncMock(side_effect=[None, RuntimeError("disk failure"), None])
    rollback = SessionMemoryRollback.__new__(SessionMemoryRollback)
    rollback._viking_fs = fs
    operations = [
        {"status": "planned", "action": "write", "uri": URI, "content": "old"},
        {
            "status": "planned",
            "action": "write",
            "uri": URI + ".second",
            "content": "old second",
        },
    ]
    originals = {URI: _present("new"), URI + ".second": _missing()}

    with pytest.raises(RuntimeError, match="disk failure"):
        try:
            await rollback._apply(operations, MagicMock(), "lease")
        except BaseException:
            await rollback._restore_original_states(originals, MagicMock(), "lease")
            raise

    assert fs.write_file.await_args_list[-1].args[1] == "new"
    fs.rm.assert_awaited_once()
