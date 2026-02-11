# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
TransactionManager: Global singleton for transaction lifecycle management.

Manages transaction lifecycle, lock acquisition, and timeout prevention.
"""

import asyncio
import atexit
import threading
import time
from typing import Any, Dict, Optional

from openviking.storage.transaction.filesystem import FileSystemBase
from openviking.storage.transaction.path_lock import PathLock
from openviking.storage.transaction.transaction_record import (
    TransactionRecord,
    TransactionStatus,
)
from openviking.storage.transaction.transaction_store import TransactionStore
from openviking.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_TRANSACTION_TIMEOUT = 3600
DEFAULT_MAX_PARALLEL_LOCKS = 8

_instance: Optional["TransactionManager"] = None


def init_transaction_manager(
    fs: FileSystemBase,
    timeout: int = DEFAULT_TRANSACTION_TIMEOUT,
    max_parallel_locks: int = DEFAULT_MAX_PARALLEL_LOCKS,
    lock_impl: Optional[PathLock] = None,
) -> "TransactionManager":
    """Initialize TransactionManager singleton.

    Args:
        fs: FileSystemBase instance for file operations
        timeout: Transaction timeout in seconds
        max_parallel_locks: Maximum number of parallel lock operations
        lock_impl: Optional custom lock implementation (default: PathLock)
    """
    global _instance
    _instance = TransactionManager(
        fs=fs,
        timeout=timeout,
        max_parallel_locks=max_parallel_locks,
        lock_impl=lock_impl,
    )
    return _instance


def get_transaction_manager() -> "TransactionManager":
    """Get TransactionManager singleton."""
    if _instance is None:
        raise RuntimeError(
            "TransactionManager not initialized. Call init_transaction_manager() first."
        )
    return _instance


class TransactionManager:
    """Transaction manager for managing transaction lifecycle and lock mechanism.

    Responsibilities:
    - Allocate transaction IDs
    - Manage transaction lifecycle (begin, commit, rollback)
    - Provide lock mechanism interface
    - Prevent deadlocks through timeout mechanism
    """

    def __init__(
        self,
        fs: FileSystemBase,
        timeout: int = DEFAULT_TRANSACTION_TIMEOUT,
        max_parallel_locks: int = DEFAULT_MAX_PARALLEL_LOCKS,
        lock_impl: Optional[PathLock] = None,
    ):
        """Initialize TransactionManager.

        Args:
            fs: FileSystemBase instance for file operations
            timeout: Transaction timeout in seconds
            max_parallel_locks: Maximum number of parallel lock operations
            lock_impl: Optional custom lock implementation (default: PathLock)
        """
        self._fs = fs
        self.timeout = timeout
        self.max_parallel_locks = max_parallel_locks
        self._lock: Optional[PathLock] = lock_impl
        self._store = TransactionStore(fs)
        self._started = False
        self._timeout_check_interval = 10
        self._timeout_check_thread: Optional[threading.Thread] = None
        self._timeout_check_stop_event: Optional[threading.Event] = None

        atexit.register(self.stop)
        logger.info(
            f"[TransactionManager] Initialized with timeout={timeout}s, max_parallel_locks={max_parallel_locks}"
        )

    def start(self) -> None:
        """Start TransactionManager, initialize lock mechanism and timeout checker."""
        if self._started:
            return

        if self._lock is None:
            self._lock = PathLock(self._fs, max_parallel=self.max_parallel_locks)
        self._started = True

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._store.initialize())
        loop.close()

        self._timeout_check_stop_event = threading.Event()
        self._timeout_check_thread = threading.Thread(
            target=self._timeout_check_loop,
            args=(self._timeout_check_stop_event,),
            daemon=True,
        )
        self._timeout_check_thread.start()

        logger.info("[TransactionManager] Started")

    def _timeout_check_loop(self, stop_event: threading.Event) -> None:
        """Background thread loop to check for timed-out transactions."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while not stop_event.is_set():
                try:
                    loop.run_until_complete(self._check_timeouts())
                except Exception as e:
                    logger.error(f"[TransactionManager] Timeout check error: {e}")
                stop_event.wait(self._timeout_check_interval)
        finally:
            loop.close()

    async def _check_timeouts(self) -> None:
        """Check and clean up timed-out transactions."""
        current_time = time.time()
        timed_out_transactions = []

        transactions = await self._store.list_all()
        for transaction_id, record in list(transactions.items()):
            if (
                record.status in [TransactionStatus.ACQUIRE, TransactionStatus.EXEC]
                and (current_time - record.created_at) > self.timeout
            ):
                timed_out_transactions.append(transaction_id)

        for transaction_id in timed_out_transactions:
            logger.warning(
                f"[TransactionManager] Transaction {transaction_id} timed out, rolling back"
            )
            await self.rollback(transaction_id)

    def stop(self) -> None:
        """Stop TransactionManager and release resources."""
        if not self._started:
            return

        if self._timeout_check_stop_event:
            self._timeout_check_stop_event.set()
        if self._timeout_check_thread:
            self._timeout_check_thread.join()
        self._timeout_check_stop_event = None
        self._timeout_check_thread = None

        self._lock = None
        self._started = False
        logger.info("[TransactionManager] Stopped")

    def is_running(self) -> bool:
        """Check if TransactionManager is running."""
        return self._started

    async def begin_transaction(self, init_info: Optional[Dict[str, Any]] = None) -> str:
        """Begin a new transaction.

        Args:
            init_info: Transaction initialization information

        Returns:
            Transaction ID
        """
        if not self._started:
            self.start()

        record = TransactionRecord(init_info=init_info or {})
        await self._store.add(record)
        logger.debug(f"[TransactionManager] Began transaction {record.id}")
        return record.id

    async def commit(self, transaction_id: str) -> bool:
        """Commit a transaction.

        Args:
            transaction_id: Transaction ID to commit

        Returns:
            True if committed successfully, False otherwise
        """
        record = await self._store.get(transaction_id)
        if record is None:
            logger.warning(f"[TransactionManager] Transaction {transaction_id} not found")
            return False

        if record.status == TransactionStatus.RELEASED:
            logger.warning(f"[TransactionManager] Transaction {transaction_id} already released")
            return False

        record.update_status(TransactionStatus.COMMIT)
        await self._store.update(record)

        await self._release_locks(record)

        record.update_status(TransactionStatus.RELEASED)
        await self._store.update(record)
        await self._store.delete(transaction_id)

        logger.debug(f"[TransactionManager] Committed transaction {transaction_id}")
        return True

    async def rollback(self, transaction_id: str) -> bool:
        """Rollback a transaction.

        Args:
            transaction_id: Transaction ID to rollback

        Returns:
            True if rolled back successfully, False otherwise
        """
        record = await self._store.get(transaction_id)
        if record is None:
            logger.warning(f"[TransactionManager] Transaction {transaction_id} not found")
            return False

        if record.status == TransactionStatus.RELEASED:
            logger.warning(f"[TransactionManager] Transaction {transaction_id} already released")
            return False

        record.update_status(TransactionStatus.FAIL)
        await self._store.update(record)

        await self._release_locks(record)

        record.update_status(TransactionStatus.RELEASED)
        await self._store.update(record)
        await self._store.delete(transaction_id)

        logger.debug(f"[TransactionManager] Rolled back transaction {transaction_id}")
        return True

    async def _release_locks(self, record: TransactionRecord) -> None:
        """Release all locks for a transaction.

        Args:
            record: Transaction record
        """
        if not record.locks:
            return

        record.update_status(TransactionStatus.RELEASING)
        await self._store.update(record)

        if self._lock:
            await self._lock.release(record.locks)

        record.locks.clear()

    async def acquire_lock(self, transaction_id: str, path: str) -> bool:
        """Acquire a lock for a transaction.

        Args:
            transaction_id: Transaction ID
            path: Path to lock

        Returns:
            True if lock acquired successfully, False otherwise
        """
        record = await self._store.get(transaction_id)
        if record is None:
            logger.warning(f"[TransactionManager] Transaction {transaction_id} not found")
            return False

        if not self._lock:
            logger.error("[TransactionManager] Lock mechanism not initialized")
            return False

        record.update_status(TransactionStatus.ACQUIRE)
        await self._store.update(record)

        if await self._lock.acquire(path, transaction_id):
            record.add_lock(path)
            await self._store.update(record)
            return True

        return False

    async def acquire_locks_recursive(
        self, transaction_id: str, path: str, parallel: bool = True
    ) -> bool:
        """Acquire locks recursively for a transaction (for rm operations).

        Args:
            transaction_id: Transaction ID
            path: Root directory path
            parallel: Whether to use parallel lock acquisition

        Returns:
            True if all locks acquired successfully, False otherwise
        """
        record = await self._store.get(transaction_id)
        if record is None:
            logger.warning(f"[TransactionManager] Transaction {transaction_id} not found")
            return False

        if not self._lock:
            logger.error("[TransactionManager] Lock mechanism not initialized")
            return False

        record.update_status(TransactionStatus.ACQUIRE)
        await self._store.update(record)

        if await self._lock.acquire_recursive(path, transaction_id, parallel):
            subdirs = await self._lock._collect_subdirectories(path)
            for subdir in subdirs:
                record.add_lock(subdir)
            record.add_lock(path)
            await self._store.update(record)
            return True

        return False

    async def acquire_locks_for_move(
        self, transaction_id: str, src_path: str, dst_path: str
    ) -> bool:
        """Acquire locks for a move operation.

        Args:
            transaction_id: Transaction ID
            src_path: Source directory path
            dst_path: Destination directory path

        Returns:
            True if all locks acquired successfully, False otherwise
        """
        record = await self._store.get(transaction_id)
        if record is None:
            logger.warning(f"[TransactionManager] Transaction {transaction_id} not found")
            return False

        if not self._lock:
            logger.error("[TransactionManager] Lock mechanism not initialized")
            return False

        record.update_status(TransactionStatus.ACQUIRE)
        await self._store.update(record)

        if await self._lock.acquire_for_move(src_path, dst_path, transaction_id):
            src_subdirs = await self._lock._collect_subdirectories(src_path)
            for subdir in src_subdirs:
                record.add_lock(subdir)
            record.add_lock(src_path)
            record.add_lock(dst_path)
            await self._store.update(record)
            return True

        return False

    async def get_transaction(self, transaction_id: str) -> Optional[TransactionRecord]:
        """Get transaction record by ID.

        Args:
            transaction_id: Transaction ID

        Returns:
            TransactionRecord if found, None otherwise
        """
        return await self._store.get(transaction_id)

    async def list_transactions(self) -> Dict[str, TransactionRecord]:
        """List all active transactions.

        Returns:
            Dictionary of transaction_id -> TransactionRecord
        """
        return await self._store.list_all()

    async def execute_in_transaction(
        self,
        operation_func,
        *args,
        init_info: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Any:
        """Execute an operation within a transaction.

        Args:
            operation_func: Function to execute
            *args: Positional arguments for operation_func
            init_info: Transaction initialization information
            **kwargs: Keyword arguments for operation_func

        Returns:
            Result of operation_func if successful, None otherwise

        Raises:
            Exception: If operation_func raises an exception
        """
        transaction_id = await self.begin_transaction(init_info=init_info)

        try:
            record = await self._store.get(transaction_id)
            if record:
                record.update_status(TransactionStatus.EXEC)
                await self._store.update(record)

            result = await operation_func(*args, **kwargs)

            await self.commit(transaction_id)
            return result

        except Exception as e:
            logger.error(
                f"[TransactionManager] Operation failed in transaction {transaction_id}: {e}"
            )
            await self.rollback(transaction_id)
            raise
