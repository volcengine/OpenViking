# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Stale RocksDB LOCK file cleanup for Windows.

On Windows, RocksDB LOCK files can persist after a process crash because
Windows does not always release file handles immediately after process
termination.  This causes subsequent ``PersistStore`` opens to fail with:

    IO error: <path>/LOCK: The process cannot access the file because it
    is being used by another process.

The strategy is simple: attempt ``os.remove()`` on each LOCK file.
- If the file is held by a live process, ``PermissionError`` is raised and
  we leave it alone.
- If the file is stale (no process holds it), the remove succeeds and the
  next ``PersistStore`` open will recreate it cleanly.

This is safe on all platforms but only necessary on Windows.
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


def clean_stale_rocksdb_locks(data_dir: str) -> int:
    """Remove stale RocksDB LOCK files under *data_dir*.

    Scans for LOCK files matching known PersistStore paths and attempts to
    remove each one.  Files held by a live process raise ``PermissionError``
    and are skipped.

    Args:
        data_dir: Root data directory (the path passed to
            ``LocalCollectionAdapter`` or ``VectorDBBackendConfig.path``).

    Returns:
        Number of stale LOCK files successfully removed.
    """
    if sys.platform != "win32":
        # On POSIX systems, RocksDB uses flock() which is automatically
        # released when the process dies.  No cleanup needed.
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
                os.remove(lock_path)
                removed += 1
                logger.info("Removed stale RocksDB LOCK: %s", lock_path)
            except PermissionError:
                # File is held by a live process — leave it alone.
                logger.debug(
                    "RocksDB LOCK is held by a live process, skipping: %s",
                    lock_path,
                )
            except OSError as exc:
                logger.warning(
                    "Could not remove RocksDB LOCK %s: %s", lock_path, exc
                )

    if removed:
        logger.info(
            "Cleaned %d stale RocksDB LOCK file(s) under %s", removed, data_dir
        )

    return removed
