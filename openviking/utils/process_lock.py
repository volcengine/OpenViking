# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PID-based advisory lock for data directory exclusivity.

Prevents multiple OpenViking processes from contending for the same data
directory, which causes silent failures in AGFS and VectorDB.
"""

import atexit
import os
import sys
import threading

from openviking_cli.utils import get_logger

logger = get_logger(__name__)

LOCK_FILENAME = ".openviking.pid"

# A PID file protects the whole process, while multiple embedded services may
# legitimately share that process and workspace.  Keep process-local ownership
# counts so closing one service cannot expose another live service to a second
# process.  The file remains the cross-process source of truth.
_LOCK_STATE_GUARD = threading.Lock()
_LOCK_REF_COUNTS: dict[str, int] = {}
_ATEXIT_REGISTERED: set[str] = set()


class DataDirectoryLocked(RuntimeError):
    """Raised when another OpenViking process holds the data directory lock."""


def _read_pid_file(lock_path: str) -> int:
    """Read PID from lock file. Returns 0 if unreadable."""
    try:
        with open(lock_path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID is still running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it.
        pass
    except (OSError, SystemError):
        if sys.platform == "win32":
            return False
        raise

    # PID exists, but on Linux PIDs are recycled. Verify this is actually
    # an OpenViking process by checking /proc/{pid}/cmdline to avoid false
    # positives from PID reuse (see issue #1088).
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().decode("utf-8", errors="replace").lower()
            if "openviking" not in cmdline and "openviking-server" not in cmdline:
                logger.info(
                    "PID %d is alive but not an OpenViking process (cmdline: %.100s). "
                    "Assuming stale lock from recycled PID.",
                    pid,
                    cmdline[:100],
                )
                return False
        except OSError:
            # /proc not available or process exited between kill and open
            pass

    return True


def _remove_owned_lock(lock_path: str, owner_pid: int) -> None:
    """Remove *lock_path* only while it still belongs to *owner_pid*."""
    try:
        if os.path.isfile(lock_path) and _read_pid_file(lock_path) == owner_pid:
            os.remove(lock_path)
    except OSError:
        pass


def release_data_dir_lock(lock_path: str, *, pid: int | None = None) -> None:
    """Release a data-directory lock when it is still owned by *pid*.

    The ownership check keeps a delayed cleanup callback from removing a lock
    that a replacement process has already acquired.
    """
    owner_pid = os.getpid() if pid is None else pid
    normalized_path = os.path.abspath(lock_path)

    with _LOCK_STATE_GUARD:
        holder_count = _LOCK_REF_COUNTS.get(normalized_path, 0)
        if holder_count > 1:
            _LOCK_REF_COUNTS[normalized_path] = holder_count - 1
            return
        if holder_count == 1:
            _LOCK_REF_COUNTS.pop(normalized_path, None)

        # A zero count is retained as a compatibility path for callers that
        # acquired the lock before this module state was initialized/reloaded.
        _remove_owned_lock(normalized_path, owner_pid)


def acquire_data_dir_lock(data_dir: str) -> str:
    """Acquire an advisory PID lock on *data_dir*.

    Returns the path to the lock file on success.

    Raises ``DataDirectoryLocked`` if another live process already holds the
    lock, with a message that explains the situation and suggests HTTP mode.
    """
    lock_path = os.path.abspath(os.path.join(data_dir, LOCK_FILENAME))
    my_pid = os.getpid()

    with _LOCK_STATE_GUARD:
        existing_pid = _read_pid_file(lock_path)
        if existing_pid and existing_pid != my_pid and _is_pid_alive(existing_pid):
            raise DataDirectoryLocked(
                f"Another OpenViking process (PID {existing_pid}) is already using "
                f"the data directory '{data_dir}'. Running multiple OpenViking "
                f"instances on the same data directory causes silent storage "
                f"contention and data corruption.\n\n"
                f"To fix this, use one of these approaches:\n"
                f"  1. Use HTTP mode: start a single openviking-server and connect "
                f"via --transport http (recommended for multi-session hosts)\n"
                f"  2. Use separate data directories for each instance\n"
                f"  3. Stop the other process (PID {existing_pid}) first"
            )

        # Write our PID (overwrites stale lock from a dead process).  Reentrant
        # acquisitions still refresh the file in case an external actor
        # removed it while this process remained alive.
        try:
            os.makedirs(data_dir, exist_ok=True)
            with open(lock_path, "w") as f:
                f.write(str(my_pid))
        except OSError as exc:
            logger.warning("Could not write PID lock %s: %s", lock_path, exc)
            return lock_path

        _LOCK_REF_COUNTS[lock_path] = _LOCK_REF_COUNTS.get(lock_path, 0) + 1

        # One force-cleanup callback per path is enough.  At interpreter exit
        # every in-process holder is terminal, so refcounts must not prevent
        # cleanup.
        if lock_path not in _ATEXIT_REGISTERED:

            def _cleanup(*_args: object) -> None:
                with _LOCK_STATE_GUARD:
                    _LOCK_REF_COUNTS.pop(lock_path, None)
                    _remove_owned_lock(lock_path, my_pid)

            atexit.register(_cleanup)
            _ATEXIT_REGISTERED.add(lock_path)

    logger.debug("Acquired data directory lock: %s (PID %d)", lock_path, my_pid)
    return lock_path
