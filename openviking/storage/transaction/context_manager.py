# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Transaction context manager for OpenViking.

Provides an async context manager that wraps a set of operations in a
transaction with automatic rollback on failure.
"""

from typing import Any, Dict, List, Optional

from openviking.storage.errors import LockAcquisitionError, TransactionError
from openviking.storage.transaction.transaction_record import TransactionRecord
from openviking.storage.transaction.undo import UndoEntry
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class TransactionContext:
    """Async context manager for transactional operations.

    Usage::

        async with TransactionContext(tx_manager, "rm", [path], lock_mode="subtree") as tx:
            seq = tx.record_undo("fs_rm", {"uri": uri})
            # ... do work ...
            tx.mark_completed(seq)
            await tx.commit()
    """

    def __init__(
        self,
        tx_manager: Any,
        operation: str,
        lock_paths: List[str],
        lock_mode: str = "point",
        mv_dst_path: Optional[str] = None,
        src_is_dir: bool = True,
    ):
        self._tx_manager = tx_manager
        self._operation = operation
        self._lock_paths = lock_paths
        self._lock_mode = lock_mode
        self._mv_dst_path = mv_dst_path
        self._src_is_dir = src_is_dir
        self._record: Optional[TransactionRecord] = None
        self._committed = False
        self._sequence = 0

    @property
    def record(self) -> TransactionRecord:
        if self._record is None:
            raise TransactionError("Transaction not started")
        return self._record

    async def __aenter__(self) -> "TransactionContext":
        self._record = self._tx_manager.create_transaction(
            init_info={
                "operation": self._operation,
                "lock_paths": self._lock_paths,
                "lock_mode": self._lock_mode,
                "mv_dst_path": self._mv_dst_path,
            }
        )
        tx_id = self._record.id

        # Write journal BEFORE acquiring locks so that crash recovery can
        # find orphan locks via init_info even if the process dies between
        # lock creation and journal update.
        try:
            self._tx_manager.journal.write(self._record.to_journal())
        except Exception as e:
            logger.warning(f"[Transaction] Failed to write journal for {tx_id}: {e}")

        success = False
        if self._lock_mode == "subtree":
            for path in self._lock_paths:
                success = await self._tx_manager.acquire_lock_subtree(tx_id, path)
                if not success:
                    break
        elif self._lock_mode == "mv":
            if len(self._lock_paths) < 1 or not self._mv_dst_path:
                raise TransactionError("mv lock mode requires lock_paths[0] and mv_dst_path")
            success = await self._tx_manager.acquire_lock_mv(
                tx_id,
                self._lock_paths[0],
                self._mv_dst_path,
                src_is_dir=self._src_is_dir,
            )
        else:
            # "point" mode (default)
            for path in self._lock_paths:
                success = await self._tx_manager.acquire_lock_point(tx_id, path)
                if not success:
                    break

        if not success:
            await self._tx_manager.rollback(tx_id)
            raise LockAcquisitionError(
                f"Failed to acquire {self._lock_mode} lock for {self._lock_paths}"
            )

        # Update journal with actual lock paths now populated in the record.
        try:
            self._tx_manager.journal.update(self._record.to_journal())
        except Exception as e:
            logger.warning(f"[Transaction] Failed to update journal for {tx_id}: {e}")

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if not self._committed:
            try:
                await self._tx_manager.rollback(self._record.id)
            except Exception as e:
                logger.error(f"Rollback failed during __aexit__: {e}")
        return False

    def record_undo(self, op_type: str, params: Dict[str, Any]) -> int:
        seq = self._sequence
        self._sequence += 1
        entry = UndoEntry(sequence=seq, op_type=op_type, params=params)
        self.record.undo_log.append(entry)

        try:
            self._tx_manager.journal.update(self.record.to_journal())
        except Exception:
            pass

        return seq

    def mark_completed(self, sequence: int) -> None:
        for entry in self.record.undo_log:
            if entry.sequence == sequence:
                entry.completed = True
                break

        try:
            self._tx_manager.journal.update(self.record.to_journal())
        except Exception:
            pass

    def add_post_action(self, action_type: str, params: Dict[str, Any]) -> None:
        self.record.post_actions.append({"type": action_type, "params": params})

    async def commit(self) -> None:
        self._committed = True
        success = await self._tx_manager.commit(self._record.id)
        if not success:
            raise TransactionError(f"Failed to commit transaction {self._record.id}")
