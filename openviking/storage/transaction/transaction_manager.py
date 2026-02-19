# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Transaction manager for OpenViking.

Global singleton that manages transaction lifecycle and lock mechanisms.
"""

import asyncio
import threading
import time
from typing import Any, Dict, Optional

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
    """

    def __init__(
        self,
        agfs_client: AGFSClient,
        timeout: int = 3600,
        max_parallel_locks: int = 8,
    ):
        """Initialize transaction manager.

        Args:
            agfs_client: AGFS client for file system operations
            timeout: Transaction timeout in seconds (default: 3600)
            max_parallel_locks: Maximum number of parallel lock operations (default: 8)
        """
        self._agfs = agfs_client
        self._timeout = timeout
        self._max_parallel_locks = max_parallel_locks
        self._path_lock = PathLock(agfs_client)

        # Active transactions: {transaction_id: TransactionRecord}
        self._transactions: Dict[str, TransactionRecord] = {}

        # Background task for timeout cleanup
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

        logger.info(
            f"TransactionManager initialized (timeout={timeout}s, max_parallel_locks={max_parallel_locks})"
        )

    async def start(self) -> None:
        """Start transaction manager.

        Starts the background cleanup task for timed-out transactions.
        """
        if self._running:
            logger.debug("TransactionManager already running")
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
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

        # Release all locks
        tx.update_status(TransactionStatus.RELEASING)
        await self._path_lock.release(tx)

        # Update status to RELEASED
        tx.update_status(TransactionStatus.RELEASED)

        # Remove from active transactions
        self._transactions.pop(transaction_id, None)

        logger.debug(f"Transaction committed: {transaction_id}")
        return True

    async def rollback(self, transaction_id: str) -> bool:
        """Rollback a transaction.

        Args:
            transaction_id: Transaction ID

        Returns:
            True if transaction rolled back successfully, False otherwise
        """
        tx = self.get_transaction(transaction_id)
        if not tx:
            logger.error(f"Transaction not found: {transaction_id}")
            return False

        # Update status to FAIL
        tx.update_status(TransactionStatus.FAIL)

        # Release all locks
        tx.update_status(TransactionStatus.RELEASING)
        await self._path_lock.release(tx)

        # Update status to RELEASED
        tx.update_status(TransactionStatus.RELEASED)

        # Remove from active transactions
        self._transactions.pop(transaction_id, None)

        logger.debug(f"Transaction rolled back: {transaction_id}")
        return True

    async def acquire_lock_normal(self, transaction_id: str, path: str) -> bool:
        """Acquire path lock for normal (non-rm/mv) operations.

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
        success = await self._path_lock.acquire_normal(path, tx)

        if success:
            tx.update_status(TransactionStatus.EXEC)
        else:
            tx.update_status(TransactionStatus.FAIL)

        return success

    async def acquire_lock_rm(
        self, transaction_id: str, path: str, max_parallel: Optional[int] = None
    ) -> bool:
        """Acquire path lock for rm operation.

        Args:
            transaction_id: Transaction ID
            path: Directory path to lock
            max_parallel: Maximum number of parallel lock operations (default: from config)

        Returns:
            True if lock acquired successfully, False otherwise
        """
        tx = self.get_transaction(transaction_id)
        if not tx:
            logger.error(f"Transaction not found: {transaction_id}")
            return False

        tx.update_status(TransactionStatus.AQUIRE)
        parallel = max_parallel or self._max_parallel_locks
        success = await self._path_lock.acquire_rm(path, tx, parallel)

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
        max_parallel: Optional[int] = None,
    ) -> bool:
        """Acquire path lock for mv operation.

        Args:
            transaction_id: Transaction ID
            src_path: Source directory path
            dst_path: Destination directory path
            max_parallel: Maximum number of parallel lock operations (default: from config)

        Returns:
            True if lock acquired successfully, False otherwise
        """
        tx = self.get_transaction(transaction_id)
        if not tx:
            logger.error(f"Transaction not found: {transaction_id}")
            return False

        tx.update_status(TransactionStatus.AQUIRE)
        parallel = max_parallel or self._max_parallel_locks
        success = await self._path_lock.acquire_mv(src_path, dst_path, tx, parallel)

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
    agfs_config: Any,
    tx_timeout: int = 3600,
    max_parallel_locks: int = 8,
) -> TransactionManager:
    """Initialize transaction manager singleton.

    Args:
        agfs_config: AGFS configuration (url, timeout, etc.)
        tx_timeout: Transaction timeout in seconds (default: 3600)
        max_parallel_locks: Maximum number of parallel lock operations (default: 8)

    Returns:
        TransactionManager instance
    """
    global _transaction_manager

    with _lock:
        if _transaction_manager is not None:
            logger.debug("TransactionManager already initialized")
            return _transaction_manager

        # Get AGFS URL from config
        agfs_url = getattr(agfs_config, "url", "http://localhost:8080")
        agfs_timeout = getattr(agfs_config, "timeout", 10)

        # Create AGFS client
        agfs_client = AGFSClient(api_base_url=agfs_url, timeout=agfs_timeout)

        # Create transaction manager
        _transaction_manager = TransactionManager(
            agfs_client=agfs_client,
            timeout=tx_timeout,
            max_parallel_locks=max_parallel_locks,
        )

        logger.info("TransactionManager initialized as singleton")
        return _transaction_manager


def get_transaction_manager() -> Optional[TransactionManager]:
    """Get transaction manager singleton.

    Returns:
        TransactionManager instance or None if not initialized
    """
    return _transaction_manager
