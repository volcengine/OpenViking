# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for TransactionContext."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.errors import LockAcquisitionError
from openviking.storage.transaction.context_manager import TransactionContext
from openviking.storage.transaction.transaction_record import TransactionRecord, TransactionStatus


def _make_tx_manager(lock_succeeds=True):
    """Create a mock TransactionManager with async methods."""
    tx_manager = MagicMock()
    record = TransactionRecord(id="tx-test", status=TransactionStatus.INIT)

    tx_manager.create_transaction.return_value = record
    tx_manager.acquire_lock_point = AsyncMock(return_value=lock_succeeds)
    tx_manager.acquire_lock_subtree = AsyncMock(return_value=lock_succeeds)
    tx_manager.acquire_lock_mv = AsyncMock(return_value=lock_succeeds)
    tx_manager.commit = AsyncMock(return_value=True)
    tx_manager.rollback = AsyncMock(return_value=True)

    journal = MagicMock()
    tx_manager.journal = journal

    return tx_manager, record


class TestTransactionContextNormal:
    async def test_commit_success(self):
        tx_manager, record = _make_tx_manager()

        async with TransactionContext(tx_manager, "test_op", ["/path"]) as tx:
            seq = tx.record_undo("fs_write_new", {"uri": "/path/file"})
            tx.mark_completed(seq)
            await tx.commit()

        tx_manager.commit.assert_called_once_with("tx-test")
        tx_manager.rollback.assert_not_called()

    async def test_rollback_on_exception(self):
        tx_manager, record = _make_tx_manager()

        with pytest.raises(ValueError):
            async with TransactionContext(tx_manager, "test_op", ["/path"]) as tx:
                seq = tx.record_undo("fs_write_new", {"uri": "/path/file"})
                tx.mark_completed(seq)
                raise ValueError("something went wrong")

        tx_manager.rollback.assert_called_once_with("tx-test")
        tx_manager.commit.assert_not_called()

    async def test_rollback_on_no_commit(self):
        tx_manager, record = _make_tx_manager()

        async with TransactionContext(tx_manager, "test_op", ["/path"]) as tx:
            tx.record_undo("fs_write_new", {"uri": "/path/file"})
            # Forgot to call tx.commit()

        tx_manager.rollback.assert_called_once_with("tx-test")

    async def test_lock_failure_raises(self):
        tx_manager, record = _make_tx_manager(lock_succeeds=False)

        with pytest.raises(LockAcquisitionError):
            async with TransactionContext(tx_manager, "test_op", ["/path"]) as _tx:
                pass


class TestTransactionContextLockModes:
    async def test_subtree_lock_mode(self):
        tx_manager, record = _make_tx_manager()

        async with TransactionContext(tx_manager, "rm_op", ["/path"], lock_mode="subtree") as tx:
            await tx.commit()

        tx_manager.acquire_lock_subtree.assert_called_once()

    async def test_mv_lock_mode(self):
        tx_manager, record = _make_tx_manager()

        async with TransactionContext(
            tx_manager, "mv_op", ["/src"], lock_mode="mv", mv_dst_path="/dst"
        ) as tx:
            await tx.commit()

        tx_manager.acquire_lock_mv.assert_called_once_with("tx-test", "/src", "/dst")

    async def test_point_lock_mode(self):
        tx_manager, record = _make_tx_manager()

        async with TransactionContext(tx_manager, "write_op", ["/path"], lock_mode="point") as tx:
            await tx.commit()

        tx_manager.acquire_lock_point.assert_called_once()


class TestTransactionContextUndoLog:
    async def test_undo_entries_tracked(self):
        tx_manager, record = _make_tx_manager()

        async with TransactionContext(tx_manager, "test", ["/path"]) as tx:
            s0 = tx.record_undo("fs_mkdir", {"uri": "/a"})
            s1 = tx.record_undo("fs_write_new", {"uri": "/a/f.txt"})
            tx.mark_completed(s0)
            tx.mark_completed(s1)
            await tx.commit()

        assert len(record.undo_log) == 2
        assert record.undo_log[0].completed is True
        assert record.undo_log[1].completed is True


class TestTransactionContextPostActions:
    async def test_post_actions_added(self):
        tx_manager, record = _make_tx_manager()

        async with TransactionContext(tx_manager, "test", ["/path"]) as tx:
            tx.add_post_action("enqueue_semantic", {"uri": "viking://test"})
            await tx.commit()

        assert len(record.post_actions) == 1
        assert record.post_actions[0]["type"] == "enqueue_semantic"


class TestTransactionContextEdgeCases:
    async def test_commit_failure_raises_transaction_error(self):
        """When TransactionManager.commit() returns False, TransactionError is raised."""
        from openviking.storage.errors import TransactionError

        tx_manager, record = _make_tx_manager()
        tx_manager.commit = AsyncMock(return_value=False)

        with pytest.raises(TransactionError, match="Failed to commit"):
            async with TransactionContext(tx_manager, "test", ["/path"]) as tx:
                await tx.commit()

    async def test_mv_mode_missing_dst_raises(self):
        """mv lock mode without mv_dst_path raises TransactionError."""
        from openviking.storage.errors import TransactionError

        tx_manager, record = _make_tx_manager()

        with pytest.raises(TransactionError, match="mv lock mode requires"):
            async with TransactionContext(
                tx_manager, "mv_op", ["/src"], lock_mode="mv", mv_dst_path=None
            ) as _tx:
                pass

    async def test_mark_completed_nonexistent_sequence_is_noop(self):
        """mark_completed with a sequence not in undo_log doesn't crash."""
        tx_manager, record = _make_tx_manager()

        async with TransactionContext(tx_manager, "test", ["/path"]) as tx:
            seq = tx.record_undo("fs_mkdir", {"uri": "/a"})
            tx.mark_completed(999)  # Nonexistent sequence
            # Original entry should remain unmarked
            assert record.undo_log[0].completed is False
            tx.mark_completed(seq)
            assert record.undo_log[0].completed is True
            await tx.commit()

    async def test_journal_update_failure_does_not_break_transaction(self):
        """Journal update failures during record_undo/mark_completed are silently ignored."""
        tx_manager, record = _make_tx_manager()
        tx_manager.journal.update.side_effect = Exception("disk full")

        # Should not raise despite journal failures
        async with TransactionContext(tx_manager, "test", ["/path"]) as tx:
            seq = tx.record_undo("fs_mkdir", {"uri": "/a"})
            tx.mark_completed(seq)
            await tx.commit()

        assert len(record.undo_log) == 1
        assert record.undo_log[0].completed is True

    async def test_record_property_before_enter_raises(self):
        """Accessing tx.record before __aenter__ raises TransactionError."""
        from openviking.storage.errors import TransactionError

        tx_manager, _ = _make_tx_manager()
        ctx = TransactionContext(tx_manager, "test", ["/path"])

        with pytest.raises(TransactionError, match="Transaction not started"):
            _ = ctx.record

    async def test_multiple_undo_entries_sequence_increments(self):
        tx_manager, record = _make_tx_manager()

        async with TransactionContext(tx_manager, "test", ["/path"]) as tx:
            s0 = tx.record_undo("fs_mkdir", {"uri": "/a"})
            s1 = tx.record_undo("fs_write_new", {"uri": "/a/f"})
            s2 = tx.record_undo("fs_mv", {"src": "/a", "dst": "/b"})
            assert s0 == 0
            assert s1 == 1
            assert s2 == 2
            await tx.commit()

    async def test_multiple_lock_paths_point_mode(self):
        """Multiple lock_paths in point mode: each path gets acquire_lock_point called."""
        tx_manager, record = _make_tx_manager()

        async with TransactionContext(
            tx_manager, "multi", ["/path1", "/path2"], lock_mode="point"
        ) as tx:
            await tx.commit()

        assert tx_manager.acquire_lock_point.call_count == 2

    async def test_subtree_multiple_paths_stops_on_first_failure(self):
        """If acquiring subtree lock on first path fails, second path is not attempted."""
        tx_manager, record = _make_tx_manager(lock_succeeds=False)

        with pytest.raises(LockAcquisitionError):
            async with TransactionContext(
                tx_manager, "rm", ["/path1", "/path2"], lock_mode="subtree"
            ) as _tx:
                pass

        # Only called once (failed on first path)
        assert tx_manager.acquire_lock_subtree.call_count == 1
