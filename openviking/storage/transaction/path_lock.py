# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Path lock implementation for transaction management.

Provides path-based locking mechanism to prevent concurrent directory operations.
Lock protocol: viking://resources/.../.path.ovlock file exists = locked
"""

import asyncio
from typing import List, Optional

from pyagfs import AGFSClient

from openviking.storage.transaction.transaction_record import TransactionRecord
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# Lock file name
LOCK_FILE_NAME = ".path.ovlock"


class PathLock:
    """Path lock manager for transaction-based directory locking.

    Implements path-based locking using lock files (.path.ovlock) to prevent
    concurrent operations on the same directory tree.
    """

    def __init__(self, agfs_client: AGFSClient):
        """Initialize path lock manager.

        Args:
            agfs_client: AGFS client for file system operations
        """
        self._agfs = agfs_client

    def _get_lock_path(self, path: str) -> str:
        """Get lock file path for a directory.

        Args:
            path: Directory path to lock

        Returns:
            Lock file path (path/.path.ovlock)
        """
        # Remove trailing slash if present
        path = path.rstrip("/")
        return f"{path}/{LOCK_FILE_NAME}"

    def _get_parent_path(self, path: str) -> Optional[str]:
        """Get parent directory path.

        Args:
            path: Directory path

        Returns:
            Parent directory path or None if at root
        """
        path = path.rstrip("/")
        if "/" not in path:
            return None
        parent = path.rsplit("/", 1)[0]
        return parent if parent else None

    async def _is_locked_by_other(self, lock_path: str, transaction_id: str) -> bool:
        """Check if path is locked by another transaction.

        Args:
            lock_path: Lock file path
            transaction_id: Current transaction ID

        Returns:
            True if locked by another transaction, False otherwise
        """
        try:
            content = self._agfs.cat(lock_path)
            if isinstance(content, bytes):
                lock_owner = content.decode("utf-8").strip()
            else:
                lock_owner = str(content).strip()
            return lock_owner != transaction_id
        except Exception:
            # Lock file doesn't exist or can't be read - not locked
            return False

    async def _create_lock_file(self, lock_path: str, transaction_id: str) -> None:
        """Create lock file with transaction ID.

        Args:
            lock_path: Lock file path
            transaction_id: Transaction ID to write to lock file
        """
        self._agfs.write(lock_path, transaction_id.encode("utf-8"))

    async def _verify_lock_ownership(self, lock_path: str, transaction_id: str) -> bool:
        """Verify lock file is owned by current transaction.

        Args:
            lock_path: Lock file path
            transaction_id: Current transaction ID

        Returns:
            True if lock is owned by current transaction, False otherwise
        """
        try:
            content = self._agfs.cat(lock_path)
            if isinstance(content, bytes):
                lock_owner = content.decode("utf-8").strip()
            else:
                lock_owner = str(content).strip()
            return lock_owner == transaction_id
        except Exception:
            return False

    async def _remove_lock_file(self, lock_path: str) -> None:
        """Remove lock file.

        Args:
            lock_path: Lock file path
        """
        try:
            self._agfs.rm(lock_path)
        except Exception:
            # Lock file might not exist, ignore
            pass

    async def acquire_normal(self, path: str, transaction: TransactionRecord) -> bool:
        """Acquire path lock for normal operations.

        Lock acquisition flow for normal operations:
        1. Check if target directory exists
        2. Check if target directory is locked by another transaction
        3. Check if parent directory is locked by another transaction
        4. Create .path.ovlock file with transaction ID
        5. Check again if parent directory is locked by another transaction
        6. Read lock file to confirm it contains current transaction ID
        7. Return success if all checks pass

        Args:
            path: Directory path to lock
            transaction: Transaction record

        Returns:
            True if lock acquired successfully, False otherwise
        """
        transaction_id = transaction.id
        lock_path = self._get_lock_path(path)
        parent_path = self._get_parent_path(path)

        # Step 1: Check if target directory exists
        try:
            self._agfs.stat(path)
        except Exception:
            logger.warning(f"Directory does not exist: {path}")
            return False

        # Step 2: Check if target directory is locked by another transaction
        if await self._is_locked_by_other(lock_path, transaction_id):
            logger.warning(f"Path already locked by another transaction: {path}")
            return False

        # Step 3: Check if parent directory is locked by another transaction
        if parent_path:
            parent_lock_path = self._get_lock_path(parent_path)
            if await self._is_locked_by_other(parent_lock_path, transaction_id):
                logger.warning(f"Parent path locked by another transaction: {parent_path}")
                return False

        # Step 4: Create lock file
        try:
            await self._create_lock_file(lock_path, transaction_id)
        except Exception as e:
            logger.error(f"Failed to create lock file: {e}")
            return False

        # Step 5: Check again if parent directory is locked
        if parent_path:
            parent_lock_path = self._get_lock_path(parent_path)
            if await self._is_locked_by_other(parent_lock_path, transaction_id):
                logger.warning(f"Parent path locked after lock creation: {parent_path}")
                await self._remove_lock_file(lock_path)
                return False

        # Step 6: Verify lock ownership
        if not await self._verify_lock_ownership(lock_path, transaction_id):
            logger.error(f"Lock ownership verification failed: {path}")
            return False

        # Step 7: Success - add lock to transaction
        transaction.add_lock(lock_path)
        logger.debug(f"Lock acquired: {lock_path}")
        return True

    async def _collect_subdirectories(self, path: str) -> List[str]:
        """Collect all subdirectory paths recursively.

        Args:
            path: Root directory path

        Returns:
            List of all subdirectory paths
        """
        subdirs = []
        try:
            entries = self._agfs.ls(path)
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("isDir"):
                        entry_path = entry.get("name", "")
                        if entry_path:
                            subdirs.append(entry_path)
                            # Recursively collect subdirectories
                            subdirs.extend(await self._collect_subdirectories(entry_path))
        except Exception as e:
            logger.warning(f"Failed to list directory {path}: {e}")

        return subdirs

    async def acquire_rm(
        self, path: str, transaction: TransactionRecord, max_parallel: int = 8
    ) -> bool:
        """Acquire path lock for rm operation using bottom-up parallel locking.

        Lock acquisition flow for rm operations (parallel bottom-up mode):
        1. Collect all subdirectory paths recursively
        2. Sort by depth (deepest first)
        3. Create lock files in batches with limited parallelism
        4. Lock the target directory last
        5. If any lock fails, release all acquired locks in reverse order

        Args:
            path: Directory path to lock
            transaction: Transaction record
            max_parallel: Maximum number of parallel lock operations

        Returns:
            True if all locks acquired successfully, False otherwise
        """
        transaction_id = transaction.id
        lock_path = self._get_lock_path(path)
        acquired_locks = []

        # Step 1: Collect all subdirectories
        subdirs = await self._collect_subdirectories(path)

        # Step 2: Sort by depth (deepest first)
        subdirs.sort(key=lambda p: p.count("/"), reverse=True)

        # Step 3: Create lock files in batches
        try:
            # Lock subdirectories in batches
            for i in range(0, len(subdirs), max_parallel):
                batch = subdirs[i : i + max_parallel]
                tasks = []
                for subdir in batch:
                    subdir_lock_path = self._get_lock_path(subdir)
                    tasks.append(self._create_lock_file(subdir_lock_path, transaction_id))

                # Execute batch in parallel
                await asyncio.gather(*tasks)
                acquired_locks.extend([self._get_lock_path(s) for s in batch])

            # Step 4: Lock target directory
            await self._create_lock_file(lock_path, transaction_id)
            acquired_locks.append(lock_path)

            # Add all locks to transaction
            for lock in acquired_locks:
                transaction.add_lock(lock)

            logger.debug(f"RM locks acquired for {len(acquired_locks)} paths")
            return True

        except Exception as e:
            logger.error(f"Failed to acquire RM locks: {e}")
            # Step 5: Release all acquired locks in reverse order
            for lock in reversed(acquired_locks):
                await self._remove_lock_file(lock)
            return False

    async def acquire_mv(
        self,
        src_path: str,
        dst_path: str,
        transaction: TransactionRecord,
        max_parallel: int = 8,
    ) -> bool:
        """Acquire path lock for mv operation.

        Lock acquisition flow for mv operations:
        1. Lock source directory (using RM-style locking)
        2. Lock destination directory (using normal locking)

        Args:
            src_path: Source directory path
            dst_path: Destination directory path
            transaction: Transaction record
            max_parallel: Maximum number of parallel lock operations

        Returns:
            True if all locks acquired successfully, False otherwise
        """
        # Step 1: Lock source directory
        if not await self.acquire_rm(src_path, transaction, max_parallel):
            logger.warning(f"Failed to lock source path: {src_path}")
            return False

        # Step 2: Lock destination directory
        if not await self.acquire_normal(dst_path, transaction):
            logger.warning(f"Failed to lock destination path: {dst_path}")
            # Release source locks
            await self.release(transaction)
            return False

        logger.debug(f"MV locks acquired: {src_path} -> {dst_path}")
        return True

    async def release(self, transaction: TransactionRecord) -> None:
        """Release all locks held by the transaction.

        Args:
            transaction: Transaction record
        """
        # Release locks in reverse order (LIFO)
        for lock_path in reversed(transaction.locks):
            await self._remove_lock_file(lock_path)
            transaction.remove_lock(lock_path)

        logger.debug(f"Released {len(transaction.locks)} locks for transaction {transaction.id}")
