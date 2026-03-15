# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for undo log and rollback executor."""

from unittest.mock import AsyncMock, MagicMock

from openviking.storage.transaction.undo import UndoEntry, execute_rollback


class TestUndoEntry:
    def test_to_dict(self):
        entry = UndoEntry(sequence=0, op_type="fs_mv", params={"src": "/a", "dst": "/b"})
        d = entry.to_dict()
        assert d["sequence"] == 0
        assert d["op_type"] == "fs_mv"
        assert d["params"] == {"src": "/a", "dst": "/b"}
        assert d["completed"] is False

    def test_from_dict(self):
        data = {"sequence": 1, "op_type": "fs_rm", "params": {"uri": "/x"}, "completed": True}
        entry = UndoEntry.from_dict(data)
        assert entry.sequence == 1
        assert entry.op_type == "fs_rm"
        assert entry.completed is True

    def test_roundtrip(self):
        entry = UndoEntry(
            sequence=5, op_type="vectordb_upsert", params={"record_id": "r1"}, completed=True
        )
        restored = UndoEntry.from_dict(entry.to_dict())
        assert restored.sequence == entry.sequence
        assert restored.op_type == entry.op_type
        assert restored.params == entry.params
        assert restored.completed == entry.completed


class TestExecuteRollback:
    def test_rollback_fs_mv(self):
        agfs = MagicMock()
        undo_log = [
            UndoEntry(
                sequence=0, op_type="fs_mv", params={"src": "/a", "dst": "/b"}, completed=True
            ),
        ]
        execute_rollback(undo_log, agfs)
        agfs.mv.assert_called_once_with("/b", "/a")

    def test_rollback_fs_rm_skipped(self):
        agfs = MagicMock()
        undo_log = [
            UndoEntry(sequence=0, op_type="fs_rm", params={"uri": "/a"}, completed=True),
        ]
        execute_rollback(undo_log, agfs)
        agfs.mv.assert_not_called()
        agfs.rm.assert_not_called()

    def test_rollback_fs_mkdir(self):
        agfs = MagicMock()
        undo_log = [
            UndoEntry(sequence=0, op_type="fs_mkdir", params={"uri": "/a/b"}, completed=True),
        ]
        execute_rollback(undo_log, agfs)
        agfs.rm.assert_called_once_with("/a/b")

    def test_rollback_fs_write_new(self):
        agfs = MagicMock()
        undo_log = [
            UndoEntry(
                sequence=0, op_type="fs_write_new", params={"uri": "/a/f.txt"}, completed=True
            ),
        ]
        execute_rollback(undo_log, agfs)
        agfs.rm.assert_called_once_with("/a/f.txt", recursive=True)

    def test_rollback_vectordb_upsert(self):
        agfs = MagicMock()
        vector_store = AsyncMock()
        undo_log = [
            UndoEntry(
                sequence=0,
                op_type="vectordb_upsert",
                params={"record_id": "r1"},
                completed=True,
            ),
        ]
        execute_rollback(undo_log, agfs, vector_store=vector_store)
        vector_store.delete.assert_called_once_with(["r1"])

    def test_rollback_vectordb_update_uri(self):
        agfs = MagicMock()
        ctx = MagicMock()
        vector_store = AsyncMock()
        undo_log = [
            UndoEntry(
                sequence=0,
                op_type="vectordb_update_uri",
                params={
                    "old_uri": "viking://a",
                    "new_uri": "viking://b",
                    "old_parent_uri": "viking://",
                },
                completed=True,
            ),
        ]
        execute_rollback(undo_log, agfs, vector_store=vector_store, ctx=ctx)
        vector_store.update_uri_mapping.assert_called_once_with(
            ctx=ctx, uri="viking://b", new_uri="viking://a", new_parent_uri="viking://"
        )

    def test_rollback_reverse_order(self):
        """Rollback should process entries in reverse sequence order."""
        agfs = MagicMock()
        call_order = []
        original_mv = agfs.mv
        original_rm = agfs.rm

        def track_mv(*args):
            call_order.append(("mv", args))
            return original_mv(*args)

        def track_rm(*args, **kwargs):
            call_order.append(("rm", args))
            return original_rm(*args, **kwargs)

        agfs.mv = track_mv
        agfs.rm = track_rm

        undo_log = [
            UndoEntry(
                sequence=0, op_type="fs_mv", params={"src": "/a", "dst": "/b"}, completed=True
            ),
            UndoEntry(sequence=1, op_type="fs_mkdir", params={"uri": "/c"}, completed=True),
        ]
        execute_rollback(undo_log, agfs)
        # seq=1 should be rolled back first (mkdir→rm), then seq=0 (mv→reverse mv)
        assert call_order[0][0] == "rm"
        assert call_order[1][0] == "mv"

    def test_rollback_skips_incomplete(self):
        agfs = MagicMock()
        undo_log = [
            UndoEntry(
                sequence=0, op_type="fs_mv", params={"src": "/a", "dst": "/b"}, completed=False
            ),
        ]
        execute_rollback(undo_log, agfs)
        agfs.mv.assert_not_called()

    def test_rollback_best_effort(self):
        """A failing rollback entry should not prevent others from running."""
        agfs = MagicMock()
        agfs.rm.side_effect = Exception("boom")
        agfs.mv = MagicMock()

        undo_log = [
            UndoEntry(
                sequence=0, op_type="fs_mv", params={"src": "/a", "dst": "/b"}, completed=True
            ),
            UndoEntry(sequence=1, op_type="fs_mkdir", params={"uri": "/c"}, completed=True),
        ]
        execute_rollback(undo_log, agfs)
        # fs_mkdir rollback failed (rm raises), but fs_mv rollback should still run
        agfs.mv.assert_called_once_with("/b", "/a")
