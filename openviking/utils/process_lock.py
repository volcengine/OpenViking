# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OS-backed file lock for exclusive access to a workspace.

The file stays on disk; only the held OS lock indicates ownership. All instances
must use this protocol: stop older PID-lock-based servers before upgrading.
"""

import errno
import os
import subprocess
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


def _lock_holder_pids(lock_path: str) -> list[int]:
    """Best-effort PIDs of foreign processes with ``lock_path`` open.

    ``flock()`` does not report which process owns a contended lock, but the
    holder necessarily keeps the lock file open, so open-file tables are a
    good enough proxy. Returns an empty list when nothing useful is found.
    """
    own_pid = os.getpid()
    pids: list[int] = []
    if sys.platform == "darwin":
        try:
            probe = subprocess.run(
                ["lsof", "-t", lock_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return pids
        for token in probe.stdout.split():
            if token.isdigit() and int(token) != own_pid:
                pids.append(int(token))
    elif os.path.isdir("/proc"):
        try:
            entries = os.listdir("/proc")
        except OSError:
            return pids
        for entry in entries:
            if not entry.isdigit() or int(entry) == own_pid:
                continue
            fd_dir = os.path.join("/proc", entry, "fd")
            try:
                fd_names = os.listdir(fd_dir)
            except OSError:
                continue
            for fd_name in fd_names:
                try:
                    if os.path.realpath(os.path.join(fd_dir, fd_name)) == lock_path:
                        pids.append(int(entry))
                        break
                except OSError:
                    continue
    return pids


def _process_command(pid: int) -> str:
    """Best-effort short command name for ``pid`` ("" when unknown)."""
    if os.path.isdir("/proc"):
        try:
            with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as handle:
                return handle.read().strip()[:80]
        except OSError:
            return ""
    if sys.platform == "darwin":
        try:
            probe = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if probe.returncode == 0:
            return probe.stdout.strip()[:80]
    return ""


def _lock_holder_hint(lock_path: str) -> str:
    """One-line description of the lock holder; "" when it cannot be found.

    Pure diagnostics: never raises and never alters lock semantics.
    """
    try:
        pids = _lock_holder_pids(lock_path)
        if not pids:
            return ""
        holders = []
        for pid in pids[:3]:
            command = _process_command(pid)
            holders.append(f"PID {pid} ({command})" if command else f"PID {pid}")
        summary = ", ".join(holders)
        if len(pids) > 3:
            summary += f" and {len(pids) - 3} more"
        return f"The lock file is currently open by: {summary}."
    except Exception:
        return ""


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
            message = (
                f"Another OpenViking process is already using the data directory '{data_dir}'. "
                "Running multiple OpenViking instances on the same data directory causes "
                "storage contention and data corruption.\n"
            )
            holder_hint = _lock_holder_hint(lock_path)
            if holder_hint:
                message += f"\n{holder_hint}\n"
            message += (
                "\nTo fix this, use one of these approaches:\n"
                "  1. Start a single OpenViking server and connect clients over HTTP\n"
                "  2. Use separate data directories for each instance\n"
                "  3. Stop the other OpenViking process first"
            )
            raise DataDirectoryLocked(message) from exc
        except BaseException:
            os.close(descriptor)
            raise

        _LOCKS[lock_path] = (descriptor, 1)

    logger.debug("Acquired data directory lock: %s", lock_path)
    return lock_path
