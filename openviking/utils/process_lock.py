# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OS-backed file lock for exclusive access to a workspace.

The file stays on disk; only the held OS lock indicates ownership. All instances
must use this protocol: stop older PID-lock-based servers before upgrading.
"""

import errno
import os
import sys
import threading

from openviking_cli.utils import get_logger

logger = get_logger(__name__)

LOCK_FILENAME = ".openviking.lock"

# Multiple services in one process can share a workspace. Hold one descriptor
# until the last service closes; process exit also closes it, including crashes.
_LOCK_STATE_GUARD = threading.Lock()
_LOCKS: dict[str, tuple[int, int]] = {}  # normalized path -> (descriptor, references)


class DataDirectoryLocked(RuntimeError):
    """Raised when another OpenViking process holds the workspace lock."""


def release_data_dir_lock(lock_path: str) -> None:
    """Release this process's lock after its last workspace user has closed."""
    lock_path = os.path.realpath(lock_path)
    with _LOCK_STATE_GUARD:
        held = _LOCKS.get(lock_path)
        if held is None:
            return
        descriptor, references = held
        if references > 1:
            _LOCKS[lock_path] = (descriptor, references - 1)
            return

        del _LOCKS[lock_path]
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)
        # Never unlink the file: contenders must all lock the same file, even
        # when one has opened it before the previous owner releases its lock.


def acquire_data_dir_lock(data_dir: str) -> str:
    """Hold an exclusive, non-blocking OS lock until release or process exit.

    Returns the lock path. Contention raises ``DataDirectoryLocked``; filesystem
    or unsupported-lock errors propagate instead of allowing an unlocked start.
    """
    data_dir = os.path.realpath(data_dir)
    lock_path = os.path.realpath(os.path.join(data_dir, LOCK_FILENAME))
    with _LOCK_STATE_GUARD:
        held = _LOCKS.get(lock_path)
        if held is not None:
            descriptor, references = held
            _LOCKS[lock_path] = (descriptor, references + 1)
            return lock_path

        os.makedirs(data_dir, exist_ok=True)
        # os.open creates a non-inheritable descriptor, so exec/spawn children
        # cannot keep the workspace locked after this server exits.
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if sys.platform == "win32":
                import msvcrt

                # Windows can lock a byte beyond EOF; no file content is needed.
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise DataDirectoryLocked(
                f"Another OpenViking process is already using the data directory '{data_dir}'. "
                "Running multiple OpenViking instances on the same data directory causes "
                "storage contention and data corruption.\n\n"
                "To fix this, use one of these approaches:\n"
                "  1. Start a single OpenViking server and connect clients over HTTP\n"
                "  2. Use separate data directories for each instance\n"
                "  3. Stop the other OpenViking process first"
            ) from exc
        except BaseException:
            os.close(descriptor)
            raise

        _LOCKS[lock_path] = (descriptor, 1)

    logger.debug("Acquired data directory lock: %s", lock_path)
    return lock_path
