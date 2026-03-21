# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""PID-based advisory lock for data directory exclusivity.

Prevents multiple OpenViking processes from contending for the same data
directory, which causes silent failures in AGFS and VectorDB.
"""

import atexit
import os
import signal

from openviking_cli.utils import get_logger

logger = get_logger(__name__)

LOCK_FILENAME = ".openviking.pid"


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
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it.
        return True
    except OSError:
        # On Windows, os.kill(pid, 0) raises OSError for stale or invalid
        # PIDs instead of ProcessLookupError. Common errors include:
        # - WinError 87 "The parameter is incorrect"
        # - WinError 11 "An attempt was made to load a program with an
        #   incorrect format"
        # Treat any OSError as "not alive" so stale lock files are
        # correctly reclaimed on Windows.
        return False


def acquire_data_dir_lock(data_dir: str) -> str:
    """Acquire an advisory PID lock on *data_dir*.

    Returns the path to the lock file on success.

    Raises ``DataDirectoryLocked`` if another live process already holds the
    lock, with a message that explains the situation and suggests HTTP mode.
    """
    lock_path = os.path.join(data_dir, LOCK_FILENAME)
    my_pid = os.getpid()

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

    # Write our PID (overwrites stale lock from a dead process).
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(lock_path, "w") as f:
            f.write(str(my_pid))
    except OSError as exc:
        logger.warning("Could not write PID lock %s: %s", lock_path, exc)
        return lock_path

    # Schedule cleanup on exit.
    def _cleanup(*_args: object) -> None:
        try:
            if os.path.isfile(lock_path):
                stored = _read_pid_file(lock_path)
                if stored == my_pid:
                    os.remove(lock_path)
        except OSError:
            pass

    atexit.register(_cleanup)
    # Also try to clean up on SIGTERM (graceful shutdown).
    try:
        signal.signal(
            signal.SIGTERM, lambda sig, frame: (_cleanup(), signal.default_int_handler(sig, frame))
        )
    except (OSError, ValueError):
        # signal.signal() can fail in non-main threads.
        pass

    logger.debug("Acquired data directory lock: %s (PID %d)", lock_path, my_pid)
    return lock_path
