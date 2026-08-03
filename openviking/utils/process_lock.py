# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OS-backed advisory lock for data directory exclusivity.

Prevents multiple OpenViking processes from contending for the same data
directory, which causes silent failures in AGFS and VectorDB. The PID file
remains a human-readable ownership diagnostic.
"""

import atexit
import errno
import os
import sys
import threading
import time

from openviking_cli.utils import get_logger

logger = get_logger(__name__)

LOCK_FILENAME = ".openviking.pid"

# One lock protects the whole process, while multiple embedded services may
# legitimately share that process and workspace.  Keep process-local ownership
# counts so closing one service cannot expose another live service to a second
# process. The held OS lock remains the cross-process source of truth.
if "_LOCK_STATE_GUARD" not in globals():
    _LOCK_STATE_GUARD = threading.Lock()
if "_LOCK_REF_COUNTS" not in globals():
    _LOCK_REF_COUNTS: dict[str, int] = {}
if "_LOCK_DESCRIPTORS" not in globals():
    _LOCK_DESCRIPTORS: dict[str, int] = {}
if "_ATEXIT_REGISTERED" not in globals():
    _ATEXIT_REGISTERED: set[str] = set()
_LOCK_GUARD_SUFFIX = ".lock"
if "_LOCK_STATE_PID" not in globals():
    _LOCK_STATE_PID = os.getpid()


class DataDirectoryLocked(RuntimeError):
    """Raised when another OpenViking process holds the data directory lock."""


def _normalize_lock_path(lock_path: str) -> str:
    """Return one process-local identity for aliases of the same PID file."""
    return os.path.realpath(os.path.abspath(lock_path))


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


def _try_acquire_os_lock(descriptor: int) -> bool:
    """Try to hold an exclusive OS lock on *descriptor* without blocking."""
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock_busy_errnos = {errno.EACCES, errno.EAGAIN}
        if hasattr(errno, "EDEADLK"):
            lock_busy_errnos.add(errno.EDEADLK)
        if exc.errno in lock_busy_errnos:
            return False
        raise
    return True


def _release_os_lock(descriptor: int) -> None:
    """Release and close a descriptor previously locked by this module."""
    try:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError as exc:
            logger.warning("Could not explicitly unlock descriptor %d: %s", descriptor, exc)
    finally:
        try:
            os.close(descriptor)
        except OSError as exc:
            logger.warning("Could not close lock descriptor %d: %s", descriptor, exc)


def _close_inherited_locks(owner_pid: int) -> None:
    """Drop process-local state inherited from another PID after a fork."""
    global _LOCK_STATE_PID

    if _LOCK_STATE_PID == owner_pid:
        return
    for descriptor in _LOCK_DESCRIPTORS.values():
        try:
            os.close(descriptor)
        except OSError:
            pass
    _LOCK_REF_COUNTS.clear()
    _LOCK_DESCRIPTORS.clear()
    _ATEXIT_REGISTERED.clear()
    _LOCK_STATE_PID = owner_pid


def _reset_lock_state_after_fork() -> None:
    """Make a forked child independent without unlocking the parent."""
    global _LOCK_STATE_GUARD

    _LOCK_STATE_GUARD = threading.Lock()
    _close_inherited_locks(os.getpid())


if hasattr(os, "register_at_fork") and not globals().get("_AT_FORK_REGISTERED", False):
    os.register_at_fork(after_in_child=_reset_lock_state_after_fork)
    _AT_FORK_REGISTERED = True


def _acquire_os_lock_or_read_owner(descriptor: int, lock_path: str) -> tuple[bool, int]:
    """Acquire during handoff or read the process that still owns the lock."""
    deadline = time.monotonic() + 1.0
    last_pid = 0
    while True:
        if _try_acquire_os_lock(descriptor):
            return True, 0
        last_pid = _read_pid_file(lock_path)
        if last_pid and _is_pid_alive(last_pid):
            return False, last_pid
        if time.monotonic() >= deadline:
            return False, last_pid
        time.sleep(0.01)


def _locked_error(data_dir: str, owner_pid: int) -> DataDirectoryLocked:
    """Build the stable user-facing diagnostic for a held data directory."""
    owner = f" (PID {owner_pid})" if owner_pid else ""
    stop_owner = f" (PID {owner_pid})" if owner_pid else ""
    return DataDirectoryLocked(
        f"Another OpenViking process{owner} is already using "
        f"the data directory '{data_dir}'. Running multiple OpenViking "
        f"instances on the same data directory causes silent storage "
        f"contention and data corruption.\n\n"
        f"To fix this, use one of these approaches:\n"
        f"  1. Use HTTP mode: start a single openviking-server and connect "
        f"via --transport http (recommended for multi-session hosts)\n"
        f"  2. Use separate data directories for each instance\n"
        f"  3. Stop the other process{stop_owner} first"
    )


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
    normalized_path = _normalize_lock_path(lock_path)

    with _LOCK_STATE_GUARD:
        _close_inherited_locks(os.getpid())
        holder_count = _LOCK_REF_COUNTS.get(normalized_path, 0)
        if holder_count > 1:
            _LOCK_REF_COUNTS[normalized_path] = holder_count - 1
            return
        if holder_count == 1:
            _LOCK_REF_COUNTS.pop(normalized_path, None)
            descriptor = _LOCK_DESCRIPTORS.pop(normalized_path)
            try:
                _remove_owned_lock(normalized_path, owner_pid)
            finally:
                _release_os_lock(descriptor)
            return

        # A zero count is retained as a compatibility path for callers that
        # acquired the lock before this module state was initialized/reloaded.
        _remove_owned_lock(normalized_path, owner_pid)


def acquire_data_dir_lock(data_dir: str) -> str:
    """Acquire an OS-backed advisory lock on *data_dir*.

    Returns the path to the lock file on success.

    Raises ``DataDirectoryLocked`` if another live process already holds the
    lock, with a message that explains the situation and suggests HTTP mode.
    Raises ``OSError`` when the PID file cannot be created or updated; callers
    must never continue as if an exclusivity lock had been acquired.
    """
    normalized_data_dir = os.path.realpath(os.path.abspath(data_dir))
    lock_path = _normalize_lock_path(os.path.join(normalized_data_dir, LOCK_FILENAME))
    guard_path = f"{lock_path}{_LOCK_GUARD_SUFFIX}"
    my_pid = os.getpid()

    with _LOCK_STATE_GUARD:
        _close_inherited_locks(my_pid)
        holder_count = _LOCK_REF_COUNTS.get(lock_path, 0)
        if holder_count:
            try:
                with open(lock_path, "w") as f:
                    f.write(str(my_pid))
            except OSError as exc:
                logger.warning("Could not write PID lock %s: %s", lock_path, exc)
                raise
            _LOCK_REF_COUNTS[lock_path] = holder_count + 1
            return lock_path

        try:
            os.makedirs(normalized_data_dir, exist_ok=True)
            descriptor = os.open(guard_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            logger.warning("Could not open data directory lock %s: %s", guard_path, exc)
            raise

        os_lock_acquired = False
        try:
            os_lock_acquired, existing_pid = _acquire_os_lock_or_read_owner(descriptor, lock_path)
            if not os_lock_acquired:
                raise _locked_error(data_dir, existing_pid)

            # A live PID without an OS lock belongs to an older OpenViking
            # version. Preserve compatibility instead of stealing its lock.
            existing_pid = _read_pid_file(lock_path)
            if existing_pid and existing_pid != my_pid and _is_pid_alive(existing_pid):
                raise _locked_error(data_dir, existing_pid)

            try:
                with open(lock_path, "w") as f:
                    f.write(str(my_pid))
            except OSError as exc:
                logger.warning("Could not write PID lock %s: %s", lock_path, exc)
                raise
        except BaseException:
            if os_lock_acquired:
                _release_os_lock(descriptor)
            else:
                os.close(descriptor)
            raise

        _LOCK_REF_COUNTS[lock_path] = _LOCK_REF_COUNTS.get(lock_path, 0) + 1
        _LOCK_DESCRIPTORS[lock_path] = descriptor

        # One force-cleanup callback per path is enough.  At interpreter exit
        # every in-process holder is terminal, so refcounts must not prevent
        # cleanup.
        if lock_path not in _ATEXIT_REGISTERED:

            def _cleanup(*_args: object) -> None:
                if os.getpid() != my_pid:
                    return
                with _LOCK_STATE_GUARD:
                    _LOCK_REF_COUNTS.pop(lock_path, None)
                    descriptor = _LOCK_DESCRIPTORS.pop(lock_path, None)
                    try:
                        _remove_owned_lock(lock_path, my_pid)
                    finally:
                        if descriptor is not None:
                            _release_os_lock(descriptor)

            atexit.register(_cleanup)
            _ATEXIT_REGISTERED.add(lock_path)

    logger.debug("Acquired data directory lock: %s (PID %d)", lock_path, my_pid)
    return lock_path
