# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Transaction manager for OpenViking.

Global singleton that manages transaction lifecycle and lock mechanisms.
"""

import asyncio
import threading
import time
from typing import Any, Dict, List, Optional

from pyagfs import AGFSClient

from openviking.storage.transaction.path_lock import PathLock
from openviking.storage.transaction.transaction_record import (
    TransactionRecord,
    TransactionStatus,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# Global singleton instance
_transaction_manager: Optional["TransactionManager"] = None
_lock = threading.Lock()


class TransactionManager:
    """Transaction manager for OpenViking.

    Global singleton that manages transaction lifecycle and lock mechanisms.
    Responsible for:
    - Allocating transaction IDs
    - Managing transaction lifecycle (start, commit, rollback)
    - Providing transaction lock mechanism interface, preventing deadlocks
    - Persisting transaction state to journal for crash recovery
    """

    def __init__(
        self,
        agfs_client: AGFSClient,
        timeout: int = 3600,
        max_parallel_locks: int = 8,
        lock_timeout: float = 0.0,
        lock_expire: float = 300.0,
    ):
        """Initialize transaction manager.

        Args:
            agfs_client: AGFS client for file system operations
            timeout: Transaction timeout in seconds (default: 3600)
            max_parallel_locks: Maximum number of parallel lock operations (default: 8)
            lock_timeout: Path lock acquisition timeout in seconds.
                0 (default) = fail immediately if locked.
                > 0 = wait/retry up to this many seconds.
            lock_expire: Stale lock expiry threshold in seconds (default: 300s).
        """
        from openviking.storage.transaction.journal import TransactionJournal

        self._agfs = agfs_client
        self._timeout = timeout
        self._max_parallel_locks = max_parallel_locks
        self._lock_timeout = lock_timeout
        self._path_lock = PathLock(agfs_client, lock_expire=lock_expire)
        self._journal = TransactionJournal(agfs_client)

        # Active transactions: {transaction_id: TransactionRecord}
        self._transactions: Dict[str, TransactionRecord] = {}

        # Background task for timeout cleanup
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

        logger.info(
            f"TransactionManager initialized (timeout={timeout}s, max_parallel_locks={max_parallel_locks})"
        )

    @property
    def journal(self):
        return self._journal

    async def start(self) -> None:
        """Start transaction manager.

        Starts the background cleanup task and recovers any pending transactions
        left from a previous process crash.
        """
        if self._running:
            logger.debug("TransactionManager already running")
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        # Recover any transactions that were interrupted by a previous crash.
        # Journal entries are written BEFORE lock acquisition, so every orphan
        # lock has a corresponding journal entry that recovery can use to clean it up.
        await self._recover_pending_transactions()

        logger.info("TransactionManager started")

    def stop(self) -> None:
        """Stop transaction manager.

        Stops the background cleanup task and releases all resources.
        """
        if not self._running:
            logger.debug("TransactionManager already stopped")
            return

        self._running = False

        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None

        # Release all active transactions
        for tx_id in list(self._transactions.keys()):
            self._transactions.pop(tx_id, None)

        logger.info("TransactionManager stopped")

    async def _cleanup_loop(self) -> None:
        """Background loop for cleaning up timed-out transactions."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Check every minute
                await self._cleanup_timed_out()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    async def _cleanup_timed_out(self) -> None:
        """Clean up timed-out transactions."""
        current_time = time.time()
        timed_out = []

        for tx_id, tx in self._transactions.items():
            if current_time - tx.updated_at > self._timeout:
                timed_out.append(tx_id)

        for tx_id in timed_out:
            logger.warning(f"Transaction timed out: {tx_id}")
            await self.rollback(tx_id)

    async def _recover_pending_transactions(self) -> None:
        """Recover pending transactions from journal after a crash.

        Reads all journal entries and rolls back any transactions that were
        not cleanly committed or rolled back.
        """
        try:
            pending_ids = self._journal.list_all()
        except Exception as e:
            logger.warning(f"Failed to list journal entries for recovery: {e}")
            return

        if not pending_ids:
            return

        logger.info(f"Found {len(pending_ids)} pending transaction(s) to recover")

        for tx_id in pending_ids:
            try:
                await self._recover_one(tx_id)
            except Exception as e:
                logger.error(f"Failed to recover transaction {tx_id}: {e}")

    async def _recover_one(self, tx_id: str) -> None:
        """Recover a single transaction from journal.

        Recovery strategy by status:
          COMMITTED + post_actions  → replay post_actions (enqueue etc.), then clean up
          COMMITTED, no post_actions / RELEASED → just clean up
          EXEC / FAIL / RELEASING   → rollback completed+partial ops, then clean up
          INIT / ACQUIRE            → nothing executed yet, just clean up
        """
        from openviking.storage.transaction.undo import execute_rollback

        try:
            data = self._journal.read(tx_id)
        except Exception as e:
            logger.warning(f"Cannot read journal for tx {tx_id}: {e}")
            return

        tx = TransactionRecord.from_journal(data)
        logger.info(f"Recovering transaction {tx_id} (status={tx.status})")

        if tx.status == TransactionStatus.COMMIT:
            # Transaction was committed — replay any unfinished post_actions
            if tx.post_actions:
                logger.info(
                    f"Replaying {len(tx.post_actions)} post_action(s) for committed tx {tx_id}"
                )
                try:
                    await self._execute_post_actions(tx.post_actions)
                except Exception as e:
                    logger.warning(f"Post-action replay failed for tx {tx_id}: {e}")
        elif tx.status in (TransactionStatus.INIT, TransactionStatus.AQUIRE):
            # Transaction never executed any operations — nothing to rollback.
            # However, locks may have been created before the journal was updated
            # with the actual locks list. Use init_info.lock_paths to find and
            # clean up orphan lock files owned by this transaction.
            logger.info(f"Transaction {tx_id} never executed, cleaning up orphan locks")
            if not tx.locks:
                await self._cleanup_orphan_locks_from_init_info(tx_id, tx.init_info)
        else:
            # EXEC / FAIL / RELEASING: process crashed mid-operation — rollback
            # Pass recover_all=True so partial (completed=False) ops are also reversed,
            # e.g. a directory mv that started but never finished still leaves residue.
            try:
                execute_rollback(tx.undo_log, self._agfs, recover_all=True)
            except Exception as e:
                logger.warning(f"Rollback during recovery failed for tx {tx_id}: {e}")

        # Release any lock files still present
        await self._path_lock.release(tx)

        # Clean up journal
        try:
            self._journal.delete(tx_id)
        except Exception:
            pass

        logger.info(f"Recovered transaction {tx_id}")

    async def _cleanup_orphan_locks_from_init_info(
        self, tx_id: str, init_info: Dict[str, Any]
    ) -> None:
        """Clean up orphan lock files using lock path hints from init_info.

        When a crash occurs between lock creation and journal update, the
        journal's ``locks`` list is empty but ``init_info.lock_paths`` records
        the paths that were intended to be locked. This method checks those
        paths and removes any lock files still owned by this transaction.
        """
        from openviking.storage.transaction.path_lock import LOCK_FILE_NAME, _parse_fencing_token

        lock_paths = init_info.get("lock_paths", [])
        lock_mode = init_info.get("lock_mode", "point")
        mv_dst_path = init_info.get("mv_dst_path")

        # Collect all candidate paths to check
        paths_to_check = list(lock_paths)
        if lock_mode == "mv" and mv_dst_path:
            paths_to_check.append(mv_dst_path)

        for path in paths_to_check:
            lock_file = f"{path.rstrip('/')}/{LOCK_FILE_NAME}"
            try:
                token = self._path_lock._read_token(lock_file)
                if token is None:
                    continue
                owner_id, _, _ = _parse_fencing_token(token)
                if owner_id == tx_id:
                    await self._path_lock._remove_lock_file(lock_file)
                    logger.info(f"Removed orphan lock for tx {tx_id}: {lock_file}")
            except Exception as e:
                logger.warning(f"Failed to check orphan lock {lock_file}: {e}")

    def create_transaction(self, init_info: Optional[Dict[str, Any]] = None) -> TransactionRecord:
        """Create a new transaction.

        Args:
            init_info: Transaction initialization information

        Returns:
            New transaction record
        """
        tx = TransactionRecord(init_info=init_info or {})
        self._transactions[tx.id] = tx
        logger.debug(f"Transaction created: {tx.id}")
        return tx

    def get_transaction(self, transaction_id: str) -> Optional[TransactionRecord]:
        """Get transaction by ID.

        Args:
            transaction_id: Transaction ID

        Returns:
            Transaction record or None if not found
        """
        return self._transactions.get(transaction_id)

    async def begin(self, transaction_id: str) -> bool:
        """Begin a transaction.

        Args:
            transaction_id: Transaction ID

        Returns:
            True if transaction started successfully, False otherwise
        """
        tx = self.get_transaction(transaction_id)
        if not tx:
            logger.error(f"Transaction not found: {transaction_id}")
            return False

        tx.update_status(TransactionStatus.AQUIRE)
        logger.debug(f"Transaction begun: {transaction_id}")
        return True

    async def commit(self, transaction_id: str) -> bool:
        """Commit a transaction.

        Executes post-actions, releases all locks, and removes the journal entry.

        Args:
            transaction_id: Transaction ID

        Returns:
            True if transaction committed successfully, False otherwise
        """
        tx = self.get_transaction(transaction_id)
        if not tx:
            logger.error(f"Transaction not found: {transaction_id}")
            return False

        # Update status to COMMIT
        tx.update_status(TransactionStatus.COMMIT)

        # Persist final committed state before releasing
        try:
            self._journal.update(tx.to_journal())
        except Exception:
            pass

        # Execute post-actions (best-effort, errors are logged but don't fail commit)
        if tx.post_actions:
            await self._execute_post_actions(tx.post_actions)

        # Release all locks
        tx.update_status(TransactionStatus.RELEASING)
        await self._path_lock.release(tx)

        # Update status to RELEASED
        tx.update_status(TransactionStatus.RELEASED)

        # Remove from active transactions
        self._transactions.pop(transaction_id, None)

        # Clean up journal entry (last step — lock is already released)
        try:
            self._journal.delete(transaction_id)
        except Exception as e:
            logger.warning(f"Failed to delete journal on commit for {transaction_id}: {e}")

        logger.debug(f"Transaction committed: {transaction_id}")
        return True

    async def rollback(self, transaction_id: str) -> bool:
        """Rollback a transaction.

        Executes undo log entries in reverse order, releases all locks,
        and removes the journal entry.

        Args:
            transaction_id: Transaction ID

        Returns:
            True if transaction rolled back successfully, False otherwise
        """
        from openviking.storage.transaction.undo import execute_rollback

        tx = self.get_transaction(transaction_id)
        if not tx:
            logger.error(f"Transaction not found: {transaction_id}")
            return False

        # Update status to FAIL
        tx.update_status(TransactionStatus.FAIL)

        # Persist rollback state
        try:
            self._journal.update(tx.to_journal())
        except Exception:
            pass

        # Execute undo log (best-effort)
        if tx.undo_log:
            try:
                execute_rollback(tx.undo_log, self._agfs)
            except Exception as e:
                logger.warning(
                    f"Undo log execution failed during rollback of {transaction_id}: {e}"
                )

        # Release all locks
        tx.update_status(TransactionStatus.RELEASING)
        await self._path_lock.release(tx)

        # Update status to RELEASED
        tx.update_status(TransactionStatus.RELEASED)

        # Remove from active transactions
        self._transactions.pop(transaction_id, None)

        # Clean up journal entry (last step — lock is already released)
        try:
            self._journal.delete(transaction_id)
        except Exception as e:
            logger.warning(f"Failed to delete journal on rollback for {transaction_id}: {e}")

        logger.debug(f"Transaction rolled back: {transaction_id}")
        return True

    async def _execute_post_actions(self, post_actions: List[Dict[str, Any]]) -> None:
        """Execute post-commit actions.

        Post-actions are executed after a successful commit. Errors are logged
        but do not affect the commit outcome.

        Args:
            post_actions: List of post-action dicts with 'type' and 'params' keys
        """
        for action in post_actions:
            action_type = action.get("type", "")
            params = action.get("params", {})
            try:
                if action_type == "enqueue_semantic":
                    await self._post_enqueue_semantic(params)
                else:
                    logger.warning(f"Unknown post-action type: {action_type}")
            except Exception as e:
                logger.warning(f"Post-action '{action_type}' failed: {e}")

    async def _post_enqueue_semantic(self, params: Dict[str, Any]) -> None:
        """Execute enqueue_semantic post-action."""
        from openviking.storage.queuefs import get_queue_manager
        from openviking.storage.queuefs.semantic_msg import SemanticMsg

        queue_manager = get_queue_manager()
        if queue_manager is None:
            logger.debug("No queue manager available, skipping enqueue_semantic post-action")
            return

        uri = params.get("uri")
        context_type = params.get("context_type", "resource")
        account_id = params.get("account_id", "default")
        if not uri:
            return

        msg = SemanticMsg(uri=uri, context_type=context_type, account_id=account_id)
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC)
        await semantic_queue.enqueue(msg)

    async def acquire_lock_point(self, transaction_id: str, path: str) -> bool:
        """Acquire POINT lock for write/semantic-processing operations.

        Args:
            transaction_id: Transaction ID
            path: Directory path to lock

        Returns:
            True if lock acquired successfully, False otherwise
        """
        tx = self.get_transaction(transaction_id)
        if not tx:
            logger.error(f"Transaction not found: {transaction_id}")
            return False

        tx.update_status(TransactionStatus.AQUIRE)
        success = await self._path_lock.acquire_point(path, tx, timeout=self._lock_timeout)

        if success:
            tx.update_status(TransactionStatus.EXEC)
        else:
            tx.update_status(TransactionStatus.FAIL)

        return success

    async def acquire_lock_subtree(
        self, transaction_id: str, path: str, timeout: Optional[float] = None
    ) -> bool:
        """Acquire SUBTREE lock for rm/mv-source operations.

        Args:
            transaction_id: Transaction ID
            path: Directory path to lock (root of the subtree)
            timeout: Maximum time to wait for the lock in seconds (default: from config)

        Returns:
            True if lock acquired successfully, False otherwise
        """
        tx = self.get_transaction(transaction_id)
        if not tx:
            logger.error(f"Transaction not found: {transaction_id}")
            return False

        tx.update_status(TransactionStatus.AQUIRE)
        effective_timeout = timeout if timeout is not None else self._lock_timeout
        success = await self._path_lock.acquire_subtree(path, tx, timeout=effective_timeout)

        if success:
            tx.update_status(TransactionStatus.EXEC)
        else:
            tx.update_status(TransactionStatus.FAIL)

        return success

    async def acquire_lock_mv(
        self,
        transaction_id: str,
        src_path: str,
        dst_path: str,
        timeout: Optional[float] = None,
    ) -> bool:
        """Acquire path lock for mv operation.

        Args:
            transaction_id: Transaction ID
            src_path: Source directory path
            dst_path: Destination parent directory path
            timeout: Maximum time to wait for each lock in seconds (default: from config)

        Returns:
            True if lock acquired successfully, False otherwise
        """
        tx = self.get_transaction(transaction_id)
        if not tx:
            logger.error(f"Transaction not found: {transaction_id}")
            return False

        tx.update_status(TransactionStatus.AQUIRE)
        effective_timeout = timeout if timeout is not None else self._lock_timeout
        success = await self._path_lock.acquire_mv(
            src_path, dst_path, tx, timeout=effective_timeout
        )

        if success:
            tx.update_status(TransactionStatus.EXEC)
        else:
            tx.update_status(TransactionStatus.FAIL)

        return success

    def get_active_transactions(self) -> Dict[str, TransactionRecord]:
        """Get all active transactions.

        Returns:
            Dictionary of active transactions {transaction_id: TransactionRecord}
        """
        return self._transactions.copy()

    def get_transaction_count(self) -> int:
        """Get the number of active transactions.

        Returns:
            Number of active transactions
        """
        return len(self._transactions)


def init_transaction_manager(
    agfs: AGFSClient,
    tx_timeout: int = 3600,
    max_parallel_locks: int = 8,
    lock_timeout: float = 0.0,
    lock_expire: float = 300.0,
) -> TransactionManager:
    """Initialize transaction manager singleton.

    Args:
        agfs: AGFS client instance
        tx_timeout: Transaction timeout in seconds (default: 3600)
        max_parallel_locks: Maximum number of parallel lock operations (default: 8)
        lock_timeout: Path lock acquisition timeout in seconds.
            0 (default) = fail immediately if locked.
            > 0 = wait/retry up to this many seconds.
        lock_expire: Stale lock expiry threshold in seconds (default: 300s).

    Returns:
        TransactionManager instance
    """
    global _transaction_manager

    with _lock:
        if _transaction_manager is not None:
            logger.debug("TransactionManager already initialized")
            return _transaction_manager

        # Create transaction manager
        _transaction_manager = TransactionManager(
            agfs_client=agfs,
            timeout=tx_timeout,
            max_parallel_locks=max_parallel_locks,
            lock_timeout=lock_timeout,
            lock_expire=lock_expire,
        )

        logger.info("TransactionManager initialized as singleton")
        return _transaction_manager


def get_transaction_manager() -> Optional[TransactionManager]:
    """Get transaction manager singleton.

    Returns:
        TransactionManager instance or None if not initialized
    """
    return _transaction_manager
