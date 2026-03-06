# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Integration test: crash recovery from journal."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

from openviking.storage.transaction.transaction_manager import TransactionManager


class TestCrashRecovery:
    def _make_manager(self, journal_entries=None):
        """Create a TransactionManager with mocked AGFS and journal data."""
        agfs = MagicMock()
        manager = TransactionManager(agfs_client=agfs, timeout=3600)

        if journal_entries:
            manager._journal = MagicMock()
            manager._journal.list_all.return_value = list(journal_entries.keys())
            manager._journal.read.side_effect = lambda tx_id: journal_entries[tx_id]
            manager._journal.delete = MagicMock()
        else:
            manager._journal = MagicMock()
            manager._journal.list_all.return_value = []

        return manager, agfs

    async def test_recover_committed_with_post_actions(self):
        """COMMIT + post_actions → replay post_actions, clean up."""
        entries = {
            "tx-1": {
                "id": "tx-1",
                "status": "COMMIT",
                "locks": ["/local/test/.path.ovlock"],
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [],
                "post_actions": [
                    {
                        "type": "enqueue_semantic",
                        "params": {
                            "uri": "viking://test",
                            "context_type": "resource",
                            "account_id": "acc",
                        },
                    }
                ],
            }
        }
        manager, agfs = self._make_manager(entries)

        with patch(
            "openviking.storage.transaction.transaction_manager.TransactionManager._execute_post_actions",
            new_callable=AsyncMock,
        ) as mock_post:
            await manager._recover_pending_transactions()

        mock_post.assert_called_once()
        agfs.rm.assert_called_once_with("/local/test/.path.ovlock")
        manager._journal.delete.assert_called_once_with("tx-1")

    async def test_recover_committed_no_post_actions(self):
        """COMMIT + no post_actions → just clean up, no rollback."""
        entries = {
            "tx-2": {
                "id": "tx-2",
                "status": "COMMIT",
                "locks": [],
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [
                    # Even if undo_log has entries, COMMIT should NOT rollback
                    {
                        "sequence": 0,
                        "op_type": "fs_mv",
                        "params": {"src": "/a", "dst": "/b"},
                        "completed": True,
                    }
                ],
                "post_actions": [],
            }
        }
        manager, agfs = self._make_manager(entries)
        await manager._recover_pending_transactions()

        agfs.mv.assert_not_called()  # No rollback for committed transactions
        manager._journal.delete.assert_called_once_with("tx-2")

    async def test_recover_exec_triggers_rollback(self):
        """EXEC status → execute rollback regardless of transaction age."""
        entries = {
            "tx-3": {
                "id": "tx-3",
                "status": "EXEC",
                "locks": ["/local/x/.path.ovlock"],
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [
                    {
                        "sequence": 0,
                        "op_type": "fs_mv",
                        "params": {"src": "/local/a", "dst": "/local/b"},
                        "completed": True,
                    }
                ],
                "post_actions": [],
            }
        }
        manager, agfs = self._make_manager(entries)
        await manager._recover_pending_transactions()

        agfs.mv.assert_called_once_with("/local/b", "/local/a")
        manager._journal.delete.assert_called_once_with("tx-3")

    async def test_recover_fail_triggers_rollback(self):
        """FAIL status → execute rollback."""
        entries = {
            "tx-fail": {
                "id": "tx-fail",
                "status": "FAIL",
                "locks": [],
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [
                    {
                        "sequence": 0,
                        "op_type": "fs_mkdir",
                        "params": {"uri": "/local/newdir"},
                        "completed": True,
                    }
                ],
                "post_actions": [],
            }
        }
        manager, agfs = self._make_manager(entries)
        await manager._recover_pending_transactions()

        agfs.rm.assert_called_once_with("/local/newdir")
        manager._journal.delete.assert_called_once_with("tx-fail")

    async def test_recover_exec_recover_all_includes_incomplete(self):
        """EXEC recovery uses recover_all=True: also reverses incomplete entries."""
        entries = {
            "tx-partial": {
                "id": "tx-partial",
                "status": "EXEC",
                "locks": [],
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [
                    {
                        "sequence": 0,
                        "op_type": "fs_mv",
                        "params": {"src": "/local/a", "dst": "/local/b"},
                        "completed": False,  # not completed, but recover_all=True should still reverse it
                    }
                ],
                "post_actions": [],
            }
        }
        manager, agfs = self._make_manager(entries)
        await manager._recover_pending_transactions()

        agfs.mv.assert_called_once_with("/local/b", "/local/a")
        manager._journal.delete.assert_called_once_with("tx-partial")

    async def test_recover_init_just_cleans_up(self):
        """INIT status → no rollback (nothing executed), just release locks and clean journal."""
        entries = {
            "tx-4": {
                "id": "tx-4",
                "status": "INIT",
                "locks": ["/local/y/.path.ovlock"],
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [],
                "post_actions": [],
            }
        }
        manager, agfs = self._make_manager(entries)
        await manager._recover_pending_transactions()

        agfs.rm.assert_called_once_with("/local/y/.path.ovlock")
        manager._journal.delete.assert_called_once_with("tx-4")

    async def test_recover_multiple_transactions(self):
        """Multiple journals are all recovered."""
        entries = {
            "tx-a": {
                "id": "tx-a",
                "status": "INIT",
                "locks": [],
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [],
                "post_actions": [],
            },
            "tx-b": {
                "id": "tx-b",
                "status": "COMMIT",
                "locks": [],
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [],
                "post_actions": [],
            },
        }
        manager, agfs = self._make_manager(entries)
        await manager._recover_pending_transactions()
        assert manager._journal.delete.call_count == 2

    async def test_recover_init_empty_locks_cleans_orphan_via_init_info(self):
        """INIT with empty locks but init_info.lock_paths → clean up orphan lock files."""
        entries = {
            "tx-orphan": {
                "id": "tx-orphan",
                "status": "INIT",
                "locks": [],  # Empty: crash happened before journal recorded locks
                "init_info": {
                    "operation": "rm",
                    "lock_paths": ["/local/orphan-dir"],
                    "lock_mode": "subtree",
                },
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [],
                "post_actions": [],
            }
        }
        manager, agfs = self._make_manager(entries)

        # Simulate: the lock file exists and is owned by this transaction
        from openviking.storage.transaction.path_lock import _make_fencing_token

        token = _make_fencing_token("tx-orphan", "S")
        agfs.cat.return_value = token.encode("utf-8")

        await manager._recover_pending_transactions()

        # Should have removed the orphan lock file
        agfs.rm.assert_called()
        rm_paths = [call[0][0] for call in agfs.rm.call_args_list]
        assert any(".path.ovlock" in p for p in rm_paths)
        manager._journal.delete.assert_called_once_with("tx-orphan")

    async def test_recover_init_orphan_lock_owned_by_other_tx_not_removed(self):
        """INIT with orphan lock path, but lock file owned by a different tx → not removed."""
        entries = {
            "tx-innocent": {
                "id": "tx-innocent",
                "status": "INIT",
                "locks": [],
                "init_info": {
                    "operation": "rm",
                    "lock_paths": ["/local/shared-dir"],
                    "lock_mode": "subtree",
                },
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [],
                "post_actions": [],
            }
        }
        manager, agfs = self._make_manager(entries)

        # Lock file owned by a different transaction
        from openviking.storage.transaction.path_lock import _make_fencing_token

        token = _make_fencing_token("tx-OTHER-owner", "S")
        agfs.cat.return_value = token.encode("utf-8")

        await manager._recover_pending_transactions()

        # rm should NOT be called for the lock file (only journal delete)
        rm_calls = [call[0][0] for call in agfs.rm.call_args_list] if agfs.rm.called else []
        assert not any(".path.ovlock" in p for p in rm_calls)
        manager._journal.delete.assert_called_once_with("tx-innocent")

    async def test_recover_aquire_status(self):
        """AQUIRE status → same as INIT, clean up only."""
        entries = {
            "tx-acq": {
                "id": "tx-acq",
                "status": "AQUIRE",
                "locks": ["/local/z/.path.ovlock"],
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [],
                "post_actions": [],
            }
        }
        manager, agfs = self._make_manager(entries)
        await manager._recover_pending_transactions()

        agfs.rm.assert_called_once_with("/local/z/.path.ovlock")
        manager._journal.delete.assert_called_once_with("tx-acq")

    async def test_recover_releasing_status_triggers_rollback(self):
        """RELEASING status → process crashed while releasing, rollback undo log."""
        entries = {
            "tx-rel": {
                "id": "tx-rel",
                "status": "RELEASING",
                "locks": ["/local/r/.path.ovlock"],
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [
                    {
                        "sequence": 0,
                        "op_type": "fs_mkdir",
                        "params": {"uri": "/local/tmpdir"},
                        "completed": True,
                    }
                ],
                "post_actions": [],
            }
        }
        manager, agfs = self._make_manager(entries)
        await manager._recover_pending_transactions()

        # Should rollback the undo log
        rm_paths = [call[0][0] for call in agfs.rm.call_args_list]
        assert "/local/tmpdir" in rm_paths
        manager._journal.delete.assert_called_once_with("tx-rel")

    async def test_recover_mv_orphan_locks_include_dst(self):
        """INIT mv operation with init_info → check both lock_paths and mv_dst_path for orphan locks."""
        entries = {
            "tx-mv-orphan": {
                "id": "tx-mv-orphan",
                "status": "INIT",
                "locks": [],
                "init_info": {
                    "operation": "mv",
                    "lock_paths": ["/local/src-dir"],
                    "lock_mode": "mv",
                    "mv_dst_path": "/local/dst-dir",
                },
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [],
                "post_actions": [],
            }
        }
        manager, agfs = self._make_manager(entries)

        from openviking.storage.transaction.path_lock import _make_fencing_token

        token = _make_fencing_token("tx-mv-orphan", "P")
        agfs.cat.return_value = token.encode("utf-8")

        await manager._recover_pending_transactions()

        # Should check both src and dst paths for orphan locks
        cat_paths = [call[0][0] for call in agfs.cat.call_args_list]
        assert any("src-dir" in p for p in cat_paths)
        assert any("dst-dir" in p for p in cat_paths)

    async def test_recover_journal_read_failure_skips_gracefully(self):
        """If reading a journal entry fails, skip that tx and continue with others."""
        agfs = MagicMock()
        manager = TransactionManager(agfs_client=agfs, timeout=3600)
        manager._journal = MagicMock()
        manager._journal.list_all.return_value = ["tx-bad", "tx-good"]

        def read_side_effect(tx_id):
            if tx_id == "tx-bad":
                raise Exception("corrupted journal")
            return {
                "id": "tx-good",
                "status": "INIT",
                "locks": [],
                "created_at": time.time(),
                "updated_at": time.time(),
                "undo_log": [],
                "post_actions": [],
            }

        manager._journal.read.side_effect = read_side_effect
        manager._journal.delete = MagicMock()

        await manager._recover_pending_transactions()

        # tx-good should still be cleaned up
        manager._journal.delete.assert_called_once_with("tx-good")
