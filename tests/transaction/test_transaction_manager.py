# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for TransactionManager: CRUD, lifecycle, commit/rollback flows, timeout cleanup."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

from openviking.storage.transaction.transaction_manager import TransactionManager
from openviking.storage.transaction.transaction_record import TransactionRecord, TransactionStatus


def _make_manager(**kwargs):
    """Create a TransactionManager with mocked AGFS and journal."""
    agfs = MagicMock()
    defaults = {"agfs_client": agfs, "timeout": 3600, "lock_timeout": 0.0, "lock_expire": 300.0}
    defaults.update(kwargs)
    manager = TransactionManager(**defaults)
    manager._journal = MagicMock()
    manager._journal.list_all.return_value = []
    return manager, agfs


class TestCreateAndGet:
    def test_create_transaction_returns_record(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction(init_info={"operation": "rm"})
        assert isinstance(tx, TransactionRecord)
        assert tx.status == TransactionStatus.INIT
        assert tx.init_info == {"operation": "rm"}

    def test_create_assigns_unique_ids(self):
        manager, _ = _make_manager()
        tx1 = manager.create_transaction()
        tx2 = manager.create_transaction()
        assert tx1.id != tx2.id

    def test_get_transaction_found(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()
        assert manager.get_transaction(tx.id) is tx

    def test_get_transaction_not_found(self):
        manager, _ = _make_manager()
        assert manager.get_transaction("nonexistent") is None

    def test_get_transaction_count(self):
        manager, _ = _make_manager()
        assert manager.get_transaction_count() == 0
        manager.create_transaction()
        assert manager.get_transaction_count() == 1
        manager.create_transaction()
        assert manager.get_transaction_count() == 2

    def test_get_active_transactions(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()
        active = manager.get_active_transactions()
        assert tx.id in active
        # Returned copy, not the internal dict
        active.pop(tx.id)
        assert manager.get_transaction(tx.id) is tx


class TestBegin:
    async def test_begin_updates_status(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()
        ok = await manager.begin(tx.id)
        assert ok is True
        assert tx.status == TransactionStatus.AQUIRE

    async def test_begin_unknown_tx(self):
        manager, _ = _make_manager()
        ok = await manager.begin("unknown-tx")
        assert ok is False


class TestCommitFlow:
    async def test_commit_full_lifecycle(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()

        # Simulate lock acquisition
        tx.update_status(TransactionStatus.EXEC)
        tx.add_lock("/test/.path.ovlock")

        ok = await manager.commit(tx.id)
        assert ok is True
        assert tx.status == TransactionStatus.RELEASED
        # Removed from active transactions
        assert manager.get_transaction(tx.id) is None
        # Journal cleaned up
        manager._journal.delete.assert_called_once_with(tx.id)

    async def test_commit_persists_journal_before_release(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()
        tx.update_status(TransactionStatus.EXEC)

        call_order = []
        original_update = manager._journal.update

        def track_update(data):
            call_order.append(("journal_update", data.get("status")))
            return original_update(data)

        manager._journal.update = track_update
        manager._journal.delete = MagicMock(
            side_effect=lambda _: call_order.append(("journal_delete",))
        )

        await manager.commit(tx.id)
        # Journal update (COMMIT) happens before delete
        assert call_order[0] == ("journal_update", "COMMIT")

    async def test_commit_executes_post_actions(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()
        tx.update_status(TransactionStatus.EXEC)
        tx.post_actions.append({"type": "enqueue_semantic", "params": {"uri": "viking://x"}})

        with patch.object(manager, "_execute_post_actions", new_callable=AsyncMock) as mock_post:
            await manager.commit(tx.id)
        mock_post.assert_called_once()

    async def test_commit_unknown_tx(self):
        manager, _ = _make_manager()
        ok = await manager.commit("nonexistent")
        assert ok is False

    async def test_commit_releases_locks(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()
        tx.update_status(TransactionStatus.EXEC)
        tx.add_lock("/a/.path.ovlock")
        tx.add_lock("/b/.path.ovlock")

        with patch.object(manager._path_lock, "release", new_callable=AsyncMock) as mock_release:
            await manager.commit(tx.id)
        mock_release.assert_called_once()


class TestRollbackFlow:
    async def test_rollback_executes_undo_log(self):
        manager, agfs = _make_manager()
        tx = manager.create_transaction()
        tx.update_status(TransactionStatus.EXEC)

        from openviking.storage.transaction.undo import UndoEntry

        tx.undo_log.append(
            UndoEntry(
                sequence=0, op_type="fs_mv", params={"src": "/a", "dst": "/b"}, completed=True
            )
        )

        ok = await manager.rollback(tx.id)
        assert ok is True
        assert tx.status == TransactionStatus.RELEASED
        agfs.mv.assert_called_once_with("/b", "/a")

    async def test_rollback_removes_from_active(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()
        tx.update_status(TransactionStatus.EXEC)

        await manager.rollback(tx.id)
        assert manager.get_transaction(tx.id) is None

    async def test_rollback_cleans_journal(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()
        tx.update_status(TransactionStatus.EXEC)

        await manager.rollback(tx.id)
        manager._journal.delete.assert_called_once_with(tx.id)

    async def test_rollback_unknown_tx(self):
        manager, _ = _make_manager()
        ok = await manager.rollback("nonexistent")
        assert ok is False

    async def test_rollback_undo_failure_does_not_prevent_cleanup(self):
        """Undo failure is best-effort; lock release and journal cleanup still happen."""
        manager, agfs = _make_manager()
        tx = manager.create_transaction()
        tx.update_status(TransactionStatus.EXEC)

        from openviking.storage.transaction.undo import UndoEntry

        tx.undo_log.append(
            UndoEntry(
                sequence=0, op_type="fs_mv", params={"src": "/a", "dst": "/b"}, completed=True
            )
        )
        agfs.mv.side_effect = Exception("disk error")

        ok = await manager.rollback(tx.id)
        assert ok is True
        manager._journal.delete.assert_called_once()


class TestLockAcquisitionWrappers:
    async def test_acquire_lock_point_success_transitions_to_exec(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()

        with patch.object(
            manager._path_lock, "acquire_point", new_callable=AsyncMock, return_value=True
        ):
            ok = await manager.acquire_lock_point(tx.id, "/test")
        assert ok is True
        assert tx.status == TransactionStatus.EXEC

    async def test_acquire_lock_point_failure_transitions_to_fail(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()

        with patch.object(
            manager._path_lock, "acquire_point", new_callable=AsyncMock, return_value=False
        ):
            ok = await manager.acquire_lock_point(tx.id, "/test")
        assert ok is False
        assert tx.status == TransactionStatus.FAIL

    async def test_acquire_lock_subtree_success(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()

        with patch.object(
            manager._path_lock, "acquire_subtree", new_callable=AsyncMock, return_value=True
        ):
            ok = await manager.acquire_lock_subtree(tx.id, "/test")
        assert ok is True
        assert tx.status == TransactionStatus.EXEC

    async def test_acquire_lock_subtree_uses_config_timeout(self):
        manager, _ = _make_manager(lock_timeout=5.0)
        tx = manager.create_transaction()

        with patch.object(
            manager._path_lock, "acquire_subtree", new_callable=AsyncMock, return_value=True
        ) as mock_acquire:
            await manager.acquire_lock_subtree(tx.id, "/test")
        mock_acquire.assert_called_once_with("/test", tx, timeout=5.0)

    async def test_acquire_lock_subtree_override_timeout(self):
        manager, _ = _make_manager(lock_timeout=5.0)
        tx = manager.create_transaction()

        with patch.object(
            manager._path_lock, "acquire_subtree", new_callable=AsyncMock, return_value=True
        ) as mock_acquire:
            await manager.acquire_lock_subtree(tx.id, "/test", timeout=10.0)
        mock_acquire.assert_called_once_with("/test", tx, timeout=10.0)

    async def test_acquire_lock_mv_success(self):
        manager, _ = _make_manager()
        tx = manager.create_transaction()

        with patch.object(
            manager._path_lock, "acquire_mv", new_callable=AsyncMock, return_value=True
        ):
            ok = await manager.acquire_lock_mv(tx.id, "/src", "/dst")
        assert ok is True
        assert tx.status == TransactionStatus.EXEC

    async def test_acquire_lock_unknown_tx(self):
        manager, _ = _make_manager()
        ok = await manager.acquire_lock_point("nonexistent", "/test")
        assert ok is False


class TestLifecycle:
    async def test_start_sets_running(self):
        manager, _ = _make_manager()
        await manager.start()
        assert manager._running is True
        manager.stop()

    async def test_start_idempotent(self):
        manager, _ = _make_manager()
        await manager.start()
        await manager.start()  # Should not error
        assert manager._running is True
        await manager.stop()

    async def test_stop_clears_state(self):
        manager, _ = _make_manager()
        await manager.start()
        manager.create_transaction()
        await manager.stop()
        assert manager._running is False
        assert manager.get_transaction_count() == 0

    async def test_stop_idempotent(self):
        manager, _ = _make_manager()
        await manager.stop()
        await manager.stop()  # Should not error


class TestTimeoutCleanup:
    async def test_cleanup_timed_out_rolls_back(self):
        manager, _ = _make_manager(timeout=1)
        tx = manager.create_transaction()
        tx.update_status(TransactionStatus.EXEC)
        # Simulate old updated_at
        tx.updated_at = time.time() - 10

        with patch.object(
            manager, "rollback", new_callable=AsyncMock, return_value=True
        ) as mock_rb:
            await manager._cleanup_timed_out()
        mock_rb.assert_called_once_with(tx.id)

    async def test_cleanup_skips_fresh_transactions(self):
        manager, _ = _make_manager(timeout=3600)
        tx = manager.create_transaction()
        tx.update_status(TransactionStatus.EXEC)

        with patch.object(manager, "rollback", new_callable=AsyncMock) as mock_rb:
            await manager._cleanup_timed_out()
        mock_rb.assert_not_called()
