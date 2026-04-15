# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Stale RocksDB LOCK file cleanup for Windows and containerized startup.

On Windows, RocksDB LOCK files can persist after a process crash because
Windows does not always release file handles immediately after process
termination. This also shows up in some Docker Desktop-on-Windows setups,
where the container reports ``sys.platform == "linux"`` but the underlying
storage semantics still leave stale ``LOCK`` files behind. These scenarios
cause subsequent ``PersistStore`` opens to fail with:

    IO error: <path>/LOCK: The process cannot access the file because it
    is being used by another process.

Cleanup is intentionally conservative:
- On native Windows, we attempt ``os.remove()`` directly. A live lock usually
  raises ``PermissionError``, so we leave it alone.
- In containers, we first probe the ``LOCK`` file with a non-blocking POSIX
  file lock. We only remove the file if that probe succeeds, which avoids
  unlinking a live RocksDB lock in normal Linux environments.
"""

from __future__ import annotations

import glob
import os
import sys

from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# RocksDB creates a LOCK file inside the store directory.
# The standard layout is: <data_dir>/vectordb/<collection>/store/LOCK
# but we also check <data_dir>/vectordb/*/LOCK for non-standard layouts.
_LOCK_GLOB_PATTERNS = [
    os.path.join("**", "store", "LOCK"),
    os.path.join("**", "LOCK"),
]
_CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")


def _is_containerized() -> bool:
    """Best-effort detection for containerized runtimes."""
    return any(os.path.exists(marker) for marker in _CONTAINER_MARKERS)


def _should_clean_stale_rocksdb_locks() -> bool:
    """Return whether startup should attempt stale LOCK cleanup."""
    return sys.platform == "win32" or _is_containerized()


def _can_reclaim_posix_lock(lock_path: str) -> bool:
    """Return True when a POSIX LOCK file can be safely reclaimed.

    We only use this in containerized non-Windows environments. If the
    non-blocking probe cannot prove the lock is free, we skip cleanup.
    """
    try:
        import fcntl
    except ImportError:
        logger.debug("fcntl unavailable, skipping RocksDB LOCK probe: %s", lock_path)
        return False

    try:
        with open(lock_path, "r+b") as lock_file:
            try:
                fcntl.lockf(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logger.debug("RocksDB LOCK is held by a live process, skipping: %s", lock_path)
                return False
            except OSError as exc:
                logger.debug("Could not probe RocksDB LOCK %s: %s", lock_path, exc)
                return False

            try:
                fcntl.lockf(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            return True
    except OSError as exc:
        logger.debug("Could not open RocksDB LOCK %s for probe: %s", lock_path, exc)
        return False


def clean_stale_rocksdb_locks(data_dir: str) -> int:
    """Remove stale RocksDB LOCK files under *data_dir*.

    Scans for LOCK files matching known PersistStore paths and attempts to
    remove each one. Live locks are skipped either by ``PermissionError`` on
    Windows or by a failed non-blocking POSIX lock probe in containers.

    Args:
        data_dir: Root data directory (the path passed to
            ``LocalCollectionAdapter`` or ``VectorDBBackendConfig.path``).

    Returns:
        Number of stale LOCK files successfully removed.
    """
    if not _should_clean_stale_rocksdb_locks():
        return 0

    removed = 0
    seen: set[str] = set()

    for pattern in _LOCK_GLOB_PATTERNS:
        full_pattern = os.path.join(data_dir, pattern)
        for lock_path in glob.glob(full_pattern, recursive=True):
            # Normalize to avoid processing the same file twice from
            # overlapping glob patterns.
            normalized = os.path.normcase(os.path.abspath(lock_path))
            if normalized in seen:
                continue
            seen.add(normalized)

            try:
                if sys.platform != "win32" and not _can_reclaim_posix_lock(lock_path):
                    continue
                os.remove(lock_path)
                removed += 1
                logger.info("Removed stale RocksDB LOCK: %s", lock_path)
            except FileNotFoundError:
                # Another startup path may have removed it already.
                continue
            except PermissionError:
                # File is held by a live process — leave it alone.
                logger.debug(
                    "RocksDB LOCK is held by a live process, skipping: %s",
                    lock_path,
                )
            except OSError as exc:
                logger.warning("Could not remove RocksDB LOCK %s: %s", lock_path, exc)

    if removed:
        logger.info("Cleaned %d stale RocksDB LOCK file(s) under %s", removed, data_dir)

    return removed
