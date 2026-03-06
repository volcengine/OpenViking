# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Path lock implementation for transaction management.

Provides path-based locking mechanism to prevent concurrent directory operations.
Lock protocol: viking://resources/.../.path.ovlock file exists = locked

Lock files contain a fencing token in the format ``{tx_id}:{time_ns}:{lock_type}`` so that
stale locks (left by crashed processes) can be detected and removed.

Two lock types:
  POINT (P): Locks a specific directory for write/semantic operations.
             Blocks if any ancestor holds a SUBTREE lock.
  SUBTREE (S): Locks an entire directory subtree for rm/mv-source operations.
               Blocks if any descendant holds any lock.

Livelock prevention: after both parties write their lock files and detect a conflict,
the "later" one (larger (timestamp, tx_id)) backs off and retries.

# TODO(multi-node): File-based locks only work correctly when all nodes share the
# same AGFS backend with strong read-write consistency. For multi-node deployments
# with replicated or partitioned storage, replace this implementation with a
# distributed lock backend (e.g. etcd txn+lease, ZooKeeper ephemeral nodes).
# The PathLock interface should be extracted to allow swappable backends.
# Key requirements for a distributed backend:
#   - Atomic compare-and-set (to avoid write-write races on lock acquisition)
#   - Session-bound leases (so crashed nodes auto-release without TTL polling)
#   - Monotonically increasing fencing tokens (etcd revision works well)
"""

import asyncio
import time
from typing import Optional, Tuple

from pyagfs import AGFSClient

from openviking.storage.transaction.transaction_record import TransactionRecord
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# Lock file name
LOCK_FILE_NAME = ".path.ovlock"

# Lock type constants
LOCK_TYPE_POINT = "P"
LOCK_TYPE_SUBTREE = "S"

# Default poll interval when waiting for a lock (seconds)
_POLL_INTERVAL = 0.2


def _make_fencing_token(tx_id: str, lock_type: str = LOCK_TYPE_POINT) -> str:
    """Create a fencing token for a transaction.

    Format: ``{tx_id}:{time_ns}:{lock_type}`` where time_ns is the current
    wall-clock time in nanoseconds and lock_type is P or S.

    Args:
        tx_id: Transaction ID
        lock_type: Lock type, either LOCK_TYPE_POINT ("P") or LOCK_TYPE_SUBTREE ("S")

    Returns:
        Fencing token string
    """
    return f"{tx_id}:{time.time_ns()}:{lock_type}"


def _parse_fencing_token(token: str) -> Tuple[str, int, str]:
    """Parse a fencing token into (tx_id, timestamp_ns, lock_type).

    Supports:
    - New format: ``{tx_id}:{time_ns}:P`` or ``{tx_id}:{time_ns}:S``
    - Legacy format: ``{tx_id}:{time_ns}`` (defaults to POINT)
    - Very legacy: plain tx_id (ts=0, defaults to POINT)

    Args:
        token: Fencing token string

    Returns:
        (tx_id, timestamp_ns, lock_type) — timestamp_ns is 0 for legacy tokens,
        lock_type defaults to LOCK_TYPE_POINT for legacy tokens.
    """
    # New format ends with ":P" or ":S"
    if token.endswith(f":{LOCK_TYPE_POINT}") or token.endswith(f":{LOCK_TYPE_SUBTREE}"):
        lock_type = token[-1]
        rest = token[:-2]  # strip ":{lock_type}"
        idx = rest.rfind(":")
        if idx >= 0:
            tx_id_part = rest[:idx]
            ts_part = rest[idx + 1 :]
            try:
                return tx_id_part, int(ts_part), lock_type
            except ValueError:
                pass
        return rest, 0, lock_type

    # Legacy format: {tx_id}:{time_ns}
    if ":" in token:
        idx = token.rfind(":")
        tx_id_part = token[:idx]
        ts_part = token[idx + 1 :]
        try:
            return tx_id_part, int(ts_part), LOCK_TYPE_POINT
        except ValueError:
            pass

    return token, 0, LOCK_TYPE_POINT


class PathLock:
    """Path lock manager for transaction-based directory locking.

    Implements path-based locking using lock files (.path.ovlock) to prevent
    concurrent operations on the same directory tree.

    Two lock types:
      POINT (P): Used for write and semantic processing operations.
      SUBTREE (S): Used for rm and mv-source operations.
    """

    def __init__(self, agfs_client: AGFSClient, lock_expire: float = 300.0):
        """Initialize path lock manager.

        Args:
            agfs_client: AGFS client for file system operations
            lock_expire: Stale lock expiry threshold in seconds (default: 300s).
                Locks held longer than this by a crashed process are force-released.
        """
        self._agfs = agfs_client
        self._lock_expire = lock_expire

    def _get_lock_path(self, path: str) -> str:
        """Get lock file path for a directory."""
        path = path.rstrip("/")
        return f"{path}/{LOCK_FILE_NAME}"

    def _get_parent_path(self, path: str) -> Optional[str]:
        """Get parent directory path."""
        path = path.rstrip("/")
        if "/" not in path:
            return None
        parent = path.rsplit("/", 1)[0]
        return parent if parent else None

    def _read_token(self, lock_path: str) -> Optional[str]:
        """Read fencing token from lock file, returning None if absent."""
        try:
            content = self._agfs.cat(lock_path)
            if isinstance(content, bytes):
                return content.decode("utf-8").strip()
            return str(content).strip()
        except Exception:
            return None

    async def _is_locked_by_other(self, lock_path: str, transaction_id: str) -> bool:
        """Check if path is locked by another transaction (any lock type)."""
        token = self._read_token(lock_path)
        if token is None:
            return False
        lock_owner, _, _ = _parse_fencing_token(token)
        return lock_owner != transaction_id

    async def _create_lock_file(
        self, lock_path: str, transaction_id: str, lock_type: str = LOCK_TYPE_POINT
    ) -> None:
        """Create lock file with fencing token."""
        token = _make_fencing_token(transaction_id, lock_type)
        self._agfs.write(lock_path, token.encode("utf-8"))

    async def _verify_lock_ownership(self, lock_path: str, transaction_id: str) -> bool:
        """Verify lock file is owned by current transaction."""
        token = self._read_token(lock_path)
        if token is None:
            return False
        lock_owner, _, _ = _parse_fencing_token(token)
        return lock_owner == transaction_id

    async def _remove_lock_file(self, lock_path: str) -> None:
        """Remove lock file."""
        try:
            self._agfs.rm(lock_path)
        except Exception:
            pass

    def is_lock_stale(self, lock_path: str, expire_seconds: float = 300.0) -> bool:
        """Check if a lock file is stale (left by a crashed process).

        A lock is considered stale if:
        - The lock file does not exist (already cleaned up)
        - The lock file contains a legacy token (no timestamp)
        - The lock has been held longer than ``expire_seconds``

        Args:
            lock_path: Lock file path
            expire_seconds: Lock expiry threshold in seconds (default: 5 minutes)

        Returns:
            True if the lock is stale, False if it is still fresh
        """
        token = self._read_token(lock_path)
        if token is None:
            return True  # No file = stale
        _, ts, _ = _parse_fencing_token(token)
        if ts == 0:
            return True  # Legacy format = consider stale
        age = (time.time_ns() - ts) / 1e9
        return age > expire_seconds

    async def _check_ancestors_for_subtree(self, path: str, exclude_tx_id: str) -> Optional[str]:
        """Walk all ancestor directories and return the first SUBTREE lock held by another tx.

        Args:
            path: Starting directory path (its ancestors are checked, not itself)
            exclude_tx_id: Transaction ID to exclude from conflict detection

        Returns:
            Lock file path of the conflicting SUBTREE lock, or None if no conflict
        """
        parent = self._get_parent_path(path)
        while parent:
            lock_path = self._get_lock_path(parent)
            token = self._read_token(lock_path)
            if token is not None:
                owner_id, _, lock_type = _parse_fencing_token(token)
                if owner_id != exclude_tx_id and lock_type == LOCK_TYPE_SUBTREE:
                    return lock_path
            parent = self._get_parent_path(parent)
        return None

    async def _scan_descendants_for_locks(self, path: str, exclude_tx_id: str) -> Optional[str]:
        """Recursively scan all descendant directories for locks held by another tx.

        Args:
            path: Root directory path to scan (its own lock is NOT checked here)
            exclude_tx_id: Transaction ID to exclude from conflict detection

        Returns:
            Lock file path of the first conflicting lock found, or None if no conflict
        """
        try:
            entries = self._agfs.ls(path)
            if not isinstance(entries, list):
                return None
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name", "")
                if not name or name in (".", ".."):
                    continue
                if not entry.get("isDir", False):
                    continue
                subdir = f"{path.rstrip('/')}/{name}"
                subdir_lock = self._get_lock_path(subdir)
                token = self._read_token(subdir_lock)
                if token is not None:
                    owner_id, _, _ = _parse_fencing_token(token)
                    if owner_id != exclude_tx_id:
                        return subdir_lock
                # Recurse into subdir
                result = await self._scan_descendants_for_locks(subdir, exclude_tx_id)
                if result:
                    return result
        except Exception as e:
            logger.warning(f"Failed to scan descendants of {path}: {e}")
        return None

    async def acquire_point(
        self, path: str, transaction: TransactionRecord, timeout: float = 0.0
    ) -> bool:
        """Acquire POINT lock for write/semantic-processing operations.

        A POINT lock is placed on a single directory. It conflicts with:
        - Any lock (P or S) on the same directory by another transaction
        - Any SUBTREE (S) lock on any ancestor directory

        Lock acquisition flow:
        1. Check target directory exists
        2. Check if target directory is locked by another transaction → wait/stale-remove
        3. Check if any ancestor holds a SUBTREE lock → wait/stale-remove
        4. Write POINT(P) lock file
        5. TOCTOU double-check: re-scan ancestors for SUBTREE locks
           - Conflict found: compare (ts, tx_id); later one backs off and retries
        6. Verify lock ownership
        7. Return success

        Args:
            path: Directory path to lock
            transaction: Transaction record
            timeout: Maximum time to wait for the lock in seconds.
                0 (default) = fail immediately if locked.
                > 0 = poll every _POLL_INTERVAL seconds until acquired or timeout.

        Returns:
            True if lock acquired successfully, False if timeout exceeded
        """
        transaction_id = transaction.id
        lock_path = self._get_lock_path(path)
        deadline = asyncio.get_event_loop().time() + timeout

        # Step 1: Check target directory exists (once, before polling)
        try:
            self._agfs.stat(path)
        except Exception:
            logger.warning(f"[POINT] Directory does not exist: {path}")
            return False

        while True:
            # Step 2: Check if target directory is locked by another transaction
            if await self._is_locked_by_other(lock_path, transaction_id):
                if self.is_lock_stale(lock_path, self._lock_expire):
                    logger.warning(f"[POINT] Removing stale lock: {lock_path}")
                    await self._remove_lock_file(lock_path)
                    continue
                if asyncio.get_event_loop().time() >= deadline:
                    logger.warning(f"[POINT] Timeout waiting for lock on: {path}")
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            # Step 3: Check all ancestors for SUBTREE locks
            ancestor_conflict = await self._check_ancestors_for_subtree(path, transaction_id)
            if ancestor_conflict:
                if self.is_lock_stale(ancestor_conflict, self._lock_expire):
                    logger.warning(
                        f"[POINT] Removing stale ancestor SUBTREE lock: {ancestor_conflict}"
                    )
                    await self._remove_lock_file(ancestor_conflict)
                    continue
                if asyncio.get_event_loop().time() >= deadline:
                    logger.warning(
                        f"[POINT] Timeout waiting for ancestor SUBTREE lock: {ancestor_conflict}"
                    )
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            # Step 4: Write POINT lock file
            try:
                await self._create_lock_file(lock_path, transaction_id, LOCK_TYPE_POINT)
            except Exception as e:
                logger.error(f"[POINT] Failed to create lock file: {e}")
                return False

            # Step 5: TOCTOU double-check ancestors for SUBTREE locks
            backed_off = False
            conflict_after = await self._check_ancestors_for_subtree(path, transaction_id)
            if conflict_after:
                their_token = self._read_token(conflict_after)
                if their_token:
                    their_tx_id, their_ts, _ = _parse_fencing_token(their_token)
                    my_token = self._read_token(lock_path)
                    _, my_ts, _ = (
                        _parse_fencing_token(my_token) if my_token else ("", 0, LOCK_TYPE_POINT)
                    )
                    # Later one (larger (ts, tx_id)) backs off
                    if (my_ts, transaction_id) > (their_ts, their_tx_id):
                        logger.debug(f"[POINT] Backing off (livelock guard) on {path}")
                        await self._remove_lock_file(lock_path)
                        backed_off = True
                # Either: I backed off, or they will back off.
                # In both cases restart the outer loop after a brief wait.
                if asyncio.get_event_loop().time() >= deadline:
                    if not backed_off:
                        await self._remove_lock_file(lock_path)
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            # Step 6: Verify lock ownership
            if not await self._verify_lock_ownership(lock_path, transaction_id):
                logger.debug(f"[POINT] Lock ownership verification failed: {path}")
                if asyncio.get_event_loop().time() >= deadline:
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            # Success
            transaction.add_lock(lock_path)
            logger.debug(f"[POINT] Lock acquired: {lock_path}")
            return True

    async def acquire_subtree(
        self, path: str, transaction: TransactionRecord, timeout: float = 0.0
    ) -> bool:
        """Acquire SUBTREE lock for rm/mv-source operations.

        A SUBTREE lock is placed on a single directory (the root of the subtree).
        It conflicts with:
        - Any lock (P or S) on the same directory by another transaction
        - Any lock (P or S) on any descendant directory by another transaction

        Lock acquisition flow:
        1. Check target directory exists
        2. Check if target directory is locked by another transaction → wait/stale-remove
        3. Scan all descendants for any locks → wait/stale-remove
        4. Write SUBTREE(S) lock file (only one file, at the root path)
        5. TOCTOU double-check: re-scan descendants for any new locks
           - Conflict found: compare (ts, tx_id); later one backs off and retries
        6. Verify lock ownership
        7. Return success

        Args:
            path: Directory path to lock (root of the subtree)
            transaction: Transaction record
            timeout: Maximum time to wait for the lock in seconds.
                0 (default) = fail immediately if locked.
                > 0 = poll every _POLL_INTERVAL seconds until acquired or timeout.

        Returns:
            True if lock acquired successfully, False if timeout exceeded
        """
        transaction_id = transaction.id
        lock_path = self._get_lock_path(path)
        deadline = asyncio.get_event_loop().time() + timeout

        # Step 1: Check target directory exists
        try:
            self._agfs.stat(path)
        except Exception:
            logger.warning(f"[SUBTREE] Directory does not exist: {path}")
            return False

        while True:
            # Step 2: Check if target directory is locked by another transaction
            if await self._is_locked_by_other(lock_path, transaction_id):
                if self.is_lock_stale(lock_path, self._lock_expire):
                    logger.warning(f"[SUBTREE] Removing stale lock: {lock_path}")
                    await self._remove_lock_file(lock_path)
                    continue
                if asyncio.get_event_loop().time() >= deadline:
                    logger.warning(f"[SUBTREE] Timeout waiting for lock on: {path}")
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            # Step 3: Scan all descendants for any locks by other transactions
            desc_conflict = await self._scan_descendants_for_locks(path, transaction_id)
            if desc_conflict:
                if self.is_lock_stale(desc_conflict, self._lock_expire):
                    logger.warning(f"[SUBTREE] Removing stale descendant lock: {desc_conflict}")
                    await self._remove_lock_file(desc_conflict)
                    continue
                if asyncio.get_event_loop().time() >= deadline:
                    logger.warning(
                        f"[SUBTREE] Timeout waiting for descendant lock: {desc_conflict}"
                    )
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            # Step 4: Write SUBTREE lock file (only one file)
            try:
                await self._create_lock_file(lock_path, transaction_id, LOCK_TYPE_SUBTREE)
            except Exception as e:
                logger.error(f"[SUBTREE] Failed to create lock file: {e}")
                return False

            # Step 5: TOCTOU double-check descendants
            backed_off = False
            conflict_after = await self._scan_descendants_for_locks(path, transaction_id)
            if conflict_after:
                their_token = self._read_token(conflict_after)
                if their_token:
                    their_tx_id, their_ts, _ = _parse_fencing_token(their_token)
                    my_token = self._read_token(lock_path)
                    _, my_ts, _ = (
                        _parse_fencing_token(my_token) if my_token else ("", 0, LOCK_TYPE_SUBTREE)
                    )
                    # Later one (larger (ts, tx_id)) backs off
                    if (my_ts, transaction_id) > (their_ts, their_tx_id):
                        logger.debug(f"[SUBTREE] Backing off (livelock guard) on {path}")
                        await self._remove_lock_file(lock_path)
                        backed_off = True
                # Either: I backed off, or they will back off.
                # In both cases restart the outer loop after a brief wait.
                if asyncio.get_event_loop().time() >= deadline:
                    if not backed_off:
                        await self._remove_lock_file(lock_path)
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            # Step 6: Verify lock ownership
            if not await self._verify_lock_ownership(lock_path, transaction_id):
                logger.debug(f"[SUBTREE] Lock ownership verification failed: {path}")
                if asyncio.get_event_loop().time() >= deadline:
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            # Success
            transaction.add_lock(lock_path)
            logger.debug(f"[SUBTREE] Lock acquired: {lock_path}")
            return True

    async def acquire_mv(
        self,
        src_path: str,
        dst_path: str,
        transaction: TransactionRecord,
        timeout: float = 0.0,
    ) -> bool:
        """Acquire path lock for mv operation.

        Lock acquisition flow for mv operations:
        1. Acquire SUBTREE lock on source directory
        2. Acquire POINT lock on destination parent directory

        Args:
            src_path: Source directory path
            dst_path: Destination parent directory path
            transaction: Transaction record
            timeout: Maximum time to wait for each lock in seconds.
                0 (default) = fail immediately if locked.
                > 0 = poll every _POLL_INTERVAL seconds until acquired or timeout.

        Returns:
            True if all locks acquired successfully, False otherwise
        """
        # Step 1: Lock source directory with SUBTREE lock
        if not await self.acquire_subtree(src_path, transaction, timeout=timeout):
            logger.warning(f"[MV] Failed to acquire SUBTREE lock on source: {src_path}")
            return False

        # Step 2: Lock destination parent directory with POINT lock
        if not await self.acquire_point(dst_path, transaction, timeout=timeout):
            logger.warning(f"[MV] Failed to acquire POINT lock on destination: {dst_path}")
            # Release source lock
            await self.release(transaction)
            return False

        logger.debug(f"[MV] Locks acquired: {src_path} -> {dst_path}")
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
