# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Integration tests: multi-step rollback covering FS + VectorDB coordination."""

from unittest.mock import AsyncMock, MagicMock

from openviking.storage.transaction.undo import UndoEntry, execute_rollback


class TestRmRollback:
    def test_vectordb_records_restored_on_fs_failure(self):
        """When FS rm fails (incomplete), VectorDB delete is rolled back via snapshot."""
        agfs = MagicMock()
        vector_store = AsyncMock()
        ctx = MagicMock()

        snapshot = [{"id": "r1", "uri": "viking://a", "content": "data"}]
        undo_log = [
            UndoEntry(
                sequence=0,
                op_type="vectordb_delete",
                params={"uris": ["viking://a"], "records_snapshot": snapshot},
                completed=True,  # VectorDB delete succeeded
            ),
            UndoEntry(
                sequence=1,
                op_type="fs_rm",
                params={"uri": "/local/test", "recursive": True},
                completed=False,  # FS rm never ran
            ),
        ]

        execute_rollback(undo_log, agfs, vector_store=vector_store, ctx=ctx)

        # Only vectordb_delete (completed=True) is reversed
        vector_store.upsert.assert_called_once_with(snapshot[0])
        # fs_rm is incomplete, so it's skipped (also fs_rm is never reversible anyway)
        agfs.rm.assert_not_called()

    def test_fs_rm_not_reversible_even_when_completed(self):
        """fs_rm is intentionally irreversible: even completed=True is skipped."""
        agfs = MagicMock()
        undo_log = [
            UndoEntry(
                sequence=0,
                op_type="fs_rm",
                params={"uri": "/local/test"},
                completed=True,
            ),
        ]
        execute_rollback(undo_log, agfs)
        agfs.rm.assert_not_called()
        agfs.mv.assert_not_called()


class TestMvRollback:
    def test_file_moved_back_on_vectordb_failure(self):
        """When VectorDB update fails (incomplete), FS mv is reversed."""
        agfs = MagicMock()

        undo_log = [
            UndoEntry(
                sequence=0,
                op_type="fs_mv",
                params={"src": "/local/a", "dst": "/local/b"},
                completed=True,  # FS mv succeeded
            ),
            UndoEntry(
                sequence=1,
                op_type="vectordb_update_uri",
                params={
                    "old_uri": "viking://a",
                    "new_uri": "viking://b",
                    "old_parent_uri": "viking://",
                },
                completed=False,  # VectorDB update never ran
            ),
        ]

        execute_rollback(undo_log, agfs)

        # Only fs_mv (completed=True) is reversed
        agfs.mv.assert_called_once_with("/local/b", "/local/a")


class TestRecoverAll:
    def test_recover_all_reverses_incomplete_entries(self):
        """recover_all=True (crash recovery mode) also reverses incomplete entries."""
        agfs = MagicMock()

        undo_log = [
            UndoEntry(
                sequence=0,
                op_type="fs_mkdir",
                params={"uri": "/local/newdir"},
                completed=True,
            ),
            UndoEntry(
                sequence=1,
                op_type="fs_mv",
                params={"src": "/local/a", "dst": "/local/b"},
                completed=False,  # Crash happened mid-operation
            ),
        ]

        execute_rollback(undo_log, agfs, recover_all=True)

        # Both entries should be reversed (in reverse sequence order)
        assert agfs.mv.call_count == 1
        agfs.mv.assert_called_once_with("/local/b", "/local/a")
        agfs.rm.assert_called_once_with("/local/newdir")

    def test_recover_all_false_skips_incomplete(self):
        """recover_all=False (normal rollback) skips incomplete entries."""
        agfs = MagicMock()

        undo_log = [
            UndoEntry(
                sequence=0,
                op_type="fs_mv",
                params={"src": "/local/a", "dst": "/local/b"},
                completed=False,
            ),
        ]

        execute_rollback(undo_log, agfs, recover_all=False)
        agfs.mv.assert_not_called()


class TestVectorDBRollbackEdgeCases:
    def test_multi_record_vectordb_delete_rollback(self):
        """Multiple VectorDB records in snapshot should all be restored."""
        agfs = MagicMock()
        vector_store = AsyncMock()
        ctx = MagicMock()

        snapshot = [
            {"id": "r1", "uri": "viking://a", "content": "data1"},
            {"id": "r2", "uri": "viking://b", "content": "data2"},
            {"id": "r3", "uri": "viking://c", "content": "data3"},
        ]
        undo_log = [
            UndoEntry(
                sequence=0,
                op_type="vectordb_delete",
                params={
                    "uris": ["viking://a", "viking://b", "viking://c"],
                    "records_snapshot": snapshot,
                },
                completed=True,
            ),
        ]
        execute_rollback(undo_log, agfs, vector_store=vector_store, ctx=ctx)

        assert vector_store.upsert.call_count == 3

    def test_empty_snapshot_vectordb_delete_rollback(self):
        """Empty snapshot → nothing to restore, no error."""
        agfs = MagicMock()
        vector_store = AsyncMock()
        ctx = MagicMock()

        undo_log = [
            UndoEntry(
                sequence=0,
                op_type="vectordb_delete",
                params={"uris": [], "records_snapshot": []},
                completed=True,
            ),
        ]
        execute_rollback(undo_log, agfs, vector_store=vector_store, ctx=ctx)
        vector_store.upsert.assert_not_called()

    def test_vectordb_delete_partial_restore_failure(self):
        """If restoring one record fails, others should still be attempted."""
        agfs = MagicMock()
        vector_store = AsyncMock()
        ctx = MagicMock()

        call_count = 0

        async def upsert_side_effect(record):
            nonlocal call_count
            call_count += 1
            if record["id"] == "r2":
                raise Exception("upsert failed")

        vector_store.upsert = AsyncMock(side_effect=upsert_side_effect)

        snapshot = [
            {"id": "r1", "uri": "viking://a"},
            {"id": "r2", "uri": "viking://b"},  # This one fails
            {"id": "r3", "uri": "viking://c"},
        ]
        undo_log = [
            UndoEntry(
                sequence=0,
                op_type="vectordb_delete",
                params={"records_snapshot": snapshot},
                completed=True,
            ),
        ]
        execute_rollback(undo_log, agfs, vector_store=vector_store, ctx=ctx)

        # All 3 should be attempted (best-effort per record)
        assert call_count == 3

    def test_vectordb_upsert_rollback_without_vector_store_is_noop(self):
        """vectordb_upsert rollback without vector_store does nothing."""
        agfs = MagicMock()
        undo_log = [
            UndoEntry(
                sequence=0,
                op_type="vectordb_upsert",
                params={"record_id": "r1"},
                completed=True,
            ),
        ]
        # Should not raise
        execute_rollback(undo_log, agfs, vector_store=None)

    def test_unknown_op_type_does_not_crash(self):
        """Unknown op_type is logged but doesn't raise."""
        agfs = MagicMock()
        undo_log = [
            UndoEntry(
                sequence=0,
                op_type="some_future_op",
                params={"foo": "bar"},
                completed=True,
            ),
        ]
        execute_rollback(undo_log, agfs)
