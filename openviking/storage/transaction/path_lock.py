# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
PathLock: Path-based lock implementation using lock files.

Implements directory-level locking through .path.ovlock files.
"""

import asyncio
import os
from pathlib import PurePath
from typing import List, Optional

from openviking.storage.transaction.filesystem import FileSystemBase
from openviking.utils.logger import get_logger

logger = get_logger(__name__)

LOCK_FILE_NAME = ".path.ovlock"
DEFAULT_MAX_PARALLEL_LOCKS = 8


class PathLock:
    """Path-based lock implementation using lock files.

    Lock protocol:
    - Lock file exists at {path}/.path.ovlock
    - Lock file content contains the transaction ID
    - Lock file existence indicates the path is locked
    """

    def __init__(self, fs: FileSystemBase, max_parallel: int = DEFAULT_MAX_PARALLEL_LOCKS):
        """Initialize PathLock.

        Args:
            fs: FileSystemBase instance for file operations
            max_parallel: Maximum number of parallel lock operations
        """
        self.fs = fs
        self.max_parallel = max_parallel

    async def acquire(self, path: str, transaction_id: str) -> bool:
        """Acquire lock for a single path (normal operation).

        Lock acquisition flow:
        1. Check if target directory exists
        2. Check if target directory is locked by another transaction
        3. Check if parent directory is locked by another transaction
        4. Create .path.ovlock file with transaction ID
        5. Double-check parent directory is not locked
        6. Verify lock file contains current transaction ID
        7. Return success if all checks pass

        Args:
            path: Target path to lock
            transaction_id: Transaction ID that owns the lock

        Returns:
            True if lock acquired successfully, False otherwise
        """
        try:
            await asyncio.to_thread(self._check_dir_exists, path)

            if await self.is_locked(path):
                owner = await self.get_lock_owner(path)
                if owner != transaction_id:
                    logger.warning(f"[PathLock] Path {path} already locked by {owner}")
                    return False

            parent_path = str(PurePath(path).parent)
            if parent_path and parent_path != path:
                if await self.is_locked(parent_path):
                    owner = await self.get_lock_owner(parent_path)
                    if owner != transaction_id:
                        logger.warning(f"[PathLock] Parent {parent_path} already locked by {owner}")
                        return False

            lock_file_path = os.path.join(path, LOCK_FILE_NAME)
            await asyncio.to_thread(self.fs.write, lock_file_path, transaction_id.encode("utf-8"))

            if parent_path and parent_path != path:
                if await self.is_locked(parent_path):
                    owner = await self.get_lock_owner(parent_path)
                    if owner != transaction_id:
                        await self._remove_lock_file(lock_file_path)
                        logger.warning(
                            f"[PathLock] Parent {parent_path} locked during acquisition by {owner}"
                        )
                        return False

            content = await asyncio.to_thread(self.fs.read, lock_file_path)
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            if content != transaction_id:
                await self._remove_lock_file(lock_file_path)
                logger.warning(
                    f"[PathLock] Lock file corrupted for {path}, expected {transaction_id}, got {content}"
                )
                return False

            logger.debug(f"[PathLock] Acquired lock for {path} by {transaction_id}")
            return True

        except Exception as e:
            logger.error(f"[PathLock] Failed to acquire lock for {path}: {e}")
            return False

    async def acquire_multiple(self, paths: List[str], transaction_id: str) -> bool:
        """Acquire locks for multiple paths in parallel.

        Args:
            paths: List of target paths to lock
            transaction_id: Transaction ID that owns the locks

        Returns:
            True if all locks acquired successfully, False otherwise
        """
        acquired_locks = []

        try:
            for path in paths:
                if await self.acquire(path, transaction_id):
                    acquired_locks.append(path)
                else:
                    logger.warning(f"[PathLock] Failed to acquire lock for {path}, rolling back")
                    await self.release(acquired_locks)
                    return False

            return True

        except Exception as e:
            logger.error(f"[PathLock] Error in acquire_multiple: {e}")
            await self.release(acquired_locks)
            return False

    async def acquire_recursive(
        self, path: str, transaction_id: str, parallel: bool = True
    ) -> bool:
        """Acquire locks recursively for a directory tree (for rm operations).

        Args:
            path: Root directory path
            transaction_id: Transaction ID that owns the locks
            parallel: Whether to use parallel lock acquisition

        Returns:
            True if all locks acquired successfully, False otherwise
        """
        try:
            if not parallel:
                return await self._acquire_recursive_serial(path, transaction_id)
            else:
                return await self._acquire_recursive_parallel(path, transaction_id)

        except Exception as e:
            logger.error(f"[PathLock] Failed to acquire recursive lock for {path}: {e}")
            return False

    async def _acquire_recursive_serial(self, path: str, transaction_id: str) -> bool:
        """Acquire locks recursively in serial mode.

        Args:
            path: Root directory path
            transaction_id: Transaction ID that owns the locks

        Returns:
            True if all locks acquired successfully, False otherwise
        """
        acquired_locks = []

        try:
            subdirs = await self._collect_subdirectories(path)
            subdirs.append(path)

            for subdir in subdirs:
                if await self.acquire(subdir, transaction_id):
                    acquired_locks.append(subdir)
                else:
                    logger.warning(f"[PathLock] Failed to acquire lock for {subdir}, rolling back")
                    await self.release(acquired_locks)
                    return False

            return True

        except Exception as e:
            logger.error(f"[PathLock] Error in serial recursive acquire: {e}")
            await self.release(acquired_locks)
            return False

    async def _acquire_recursive_parallel(self, path: str, transaction_id: str) -> bool:
        """Acquire locks recursively in parallel mode (bottom-up).

        Parallel lock acquisition flow:
        1. Traverse directory tree to collect all subdirectories
        2. Sort subdirectories by depth (deepest first)
        3. Batch create .path.ovlock files with limited parallelism
        4. Finally lock the root directory
        5. If any lock fails, release all acquired locks in reverse order

        Args:
            path: Root directory path
            transaction_id: Transaction ID that owns the locks

        Returns:
            True if all locks acquired successfully, False otherwise
        """
        acquired_locks = []

        try:
            subdirs = await self._collect_subdirectories(path)
            subdirs_by_depth = self._sort_by_depth(subdirs)

            for batch in self._batch_paths(subdirs_by_depth, self.max_parallel):
                tasks = [self.acquire(p, transaction_id) for p in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for subdir_path, result in zip(batch, results):
                    if isinstance(result, Exception) or not result:
                        logger.warning(
                            f"[PathLock] Failed to acquire lock for {subdir_path}, rolling back"
                        )
                        await self.release(acquired_locks)
                        return False
                    acquired_locks.append(subdir_path)

            if await self.acquire(path, transaction_id):
                acquired_locks.append(path)
            else:
                logger.warning(f"[PathLock] Failed to acquire lock for root {path}, rolling back")
                await self.release(acquired_locks)
                return False

            return True

        except Exception as e:
            logger.error(f"[PathLock] Error in parallel recursive acquire: {e}")
            await self.release(acquired_locks)
            return False

    async def acquire_for_move(self, src_path: str, dst_path: str, transaction_id: str) -> bool:
        """Acquire locks for move operation (source and destination).

        Args:
            src_path: Source directory path
            dst_path: Destination directory path
            transaction_id: Transaction ID that owns the locks

        Returns:
            True if all locks acquired successfully, False otherwise
        """
        acquired_locks = []

        try:
            if await self.acquire_recursive(src_path, transaction_id):
                acquired_locks.extend(await self._collect_subdirectories(src_path))
                acquired_locks.append(src_path)
            else:
                logger.warning(f"[PathLock] Failed to acquire lock for source {src_path}")
                return False

            if await self.acquire(dst_path, transaction_id):
                acquired_locks.append(dst_path)
            else:
                logger.warning(
                    f"[PathLock] Failed to acquire lock for destination {dst_path}, rolling back"
                )
                await self.release(acquired_locks)
                return False

            return True

        except Exception as e:
            logger.error(f"[PathLock] Error in acquire_for_move: {e}")
            await self.release(acquired_locks)
            return False

    async def release(self, locks: List[str]) -> None:
        """Release locks for specified paths.

        Args:
            locks: List of paths to release locks for
        """
        for path in locks:
            try:
                lock_file_path = os.path.join(path, LOCK_FILE_NAME)
                await self._remove_lock_file(lock_file_path)
                logger.debug(f"[PathLock] Released lock for {path}")
            except Exception as e:
                logger.error(f"[PathLock] Failed to release lock for {path}: {e}")

    async def is_locked(self, path: str) -> bool:
        """Check if a path is locked.

        Args:
            path: Path to check

        Returns:
            True if path is locked, False otherwise
        """
        try:
            lock_file_path = os.path.join(path, LOCK_FILE_NAME)
            await asyncio.to_thread(self.fs.stat, lock_file_path)
            return True
        except Exception:
            return False

    async def get_lock_owner(self, path: str) -> Optional[str]:
        """Get the lock owner (transaction ID) for a path.

        Args:
            path: Path to check

        Returns:
            Transaction ID if path is locked, None otherwise
        """
        try:
            lock_file_path = os.path.join(path, LOCK_FILE_NAME)
            content = await asyncio.to_thread(self.fs.read, lock_file_path)
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            return content
        except Exception:
            return None

    def _check_dir_exists(self, path: str) -> None:
        """Check if directory exists (synchronous)."""
        self.fs.stat(path)

    async def _remove_lock_file(self, lock_file_path: str) -> None:
        """Remove lock file (synchronous AGFS call)."""
        try:
            await asyncio.to_thread(self.fs.rm, lock_file_path, recursive=False)
        except Exception:
            pass

    async def _collect_subdirectories(self, path: str) -> List[str]:
        """Collect all subdirectories under a path.

        Args:
            path: Root directory path

        Returns:
            List of all subdirectory paths
        """
        subdirs = []

        async def _walk(current_path: str):
            try:
                entries = await asyncio.to_thread(self.fs.ls, current_path)
                for entry in entries:
                    name = entry.get("name", "")
                    if name in [".", ".."]:
                        continue
                    if entry.get("isDir"):
                        full_path = os.path.join(current_path, name)
                        subdirs.append(full_path)
                        await _walk(full_path)
            except Exception as e:
                logger.error(f"[PathLock] Error walking {current_path}: {e}")

        await _walk(path)
        return subdirs

    def _sort_by_depth(self, paths: List[str]) -> List[str]:
        """Sort paths by depth (deepest first).

        Args:
            paths: List of paths to sort

        Returns:
            Sorted paths with deepest first
        """
        return sorted(paths, key=lambda p: -p.count(os.sep))

    def _batch_paths(self, paths: List[str], batch_size: int) -> List[List[str]]:
        """Split paths into batches.

        Args:
            paths: List of paths
            batch_size: Maximum batch size

        Returns:
            List of path batches
        """
        return [paths[i : i + batch_size] for i in range(0, len(paths), batch_size)]
