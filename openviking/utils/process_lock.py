# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PID-based advisory lock for data directory exclusivity.

Prevents multiple OpenViking processes from contending for the same data
directory, which causes silent failures in AGFS and VectorDB.
"""

import atexit
import os
import signal
import sys
import threading

from openviking_cli.utils import get_logger

logger = get_logger(__name__)

LOCK_FILENAME = ".openviking.pid"
_LOCK_FILES: dict[str, object] = {}
_LOCK_GUARD = threading.RLock()
getattr(os, "register_at_fork", lambda **_: None)(after_in_child=_LOCK_GUARD._at_fork_reinit)
getattr(os, "register_at_fork", lambda **_: None)(after_in_child=_LOCK_FILES.clear)


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


def _lock_file(lock_file) -> None:
    if os.name == "nt":
        import msvcrt
        if os.fstat(lock_file.fileno()).st_size == 0:
            lock_file.write("0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def acquire_data_dir_lock(data_dir: str) -> str:
    """Acquire an advisory PID lock on *data_dir*.

    Returns the path to the lock file on success.

    Raises ``DataDirectoryLocked`` if another live process already holds the
    lock, with a message that explains the situation and suggests HTTP mode.
    """
    lock_path = os.path.join(data_dir, LOCK_FILENAME)
    lock_key = os.path.normcase(os.path.realpath(lock_path))
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

    os.makedirs(data_dir, exist_ok=True)
    with _LOCK_GUARD:
        if lock_key in _LOCK_FILES:
            return lock_path

        lock_file = open(lock_path, "a+")
        try:
            _lock_file(lock_file)
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(str(my_pid))
            lock_file.flush()
        except OSError as exc:
            lock_file.close()
            raise DataDirectoryLocked(f"Could not lock data directory: '{data_dir}'") from exc
        _LOCK_FILES[lock_key] = lock_file

    # Schedule cleanup on exit.
    def _cleanup(*_args: object) -> None:
        with _LOCK_GUARD:
            locked = _LOCK_FILES.pop(lock_key, None)
            if locked is not None:
                try:
                    locked.truncate(0)
                except OSError:
                    pass
                locked.close()

    atexit.register(_cleanup)
    # Also try to clean up on SIGTERM (graceful shutdown).
    try:
        signal.signal(signal.SIGTERM, lambda sig, frame: (_cleanup(), sys.exit(0)))
    except (OSError, ValueError):
        # signal.signal() can fail in non-main threads.
        pass

    logger.debug("Acquired data directory lock: %s (PID %d)", lock_path, my_pid)
    return lock_path
