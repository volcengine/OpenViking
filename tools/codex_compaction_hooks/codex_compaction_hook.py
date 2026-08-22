# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Small, fail-closed Codex compaction hook.

Hook input is untrusted.  Only fixed messages are returned to Codex, while a
private, bounded correlation record is kept below ``CODEX_HOME``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess  # noqa: F401 - tests prove the critical path never uses it.
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

STATE_SUBDIRECTORY = "state/compaction-hooks"
MAX_STDIN_BYTES = 64 * 1024
INTERNAL_TIMEOUT_SECONDS = 5.0
MAX_RECORDS = 256
MAX_DIRECTORY_ENTRIES = 1024
RECORD_TTL_SECONDS = 24 * 60 * 60

_RECORD_NAME = re.compile(r"^[0-9a-f]{64}\.json$")
_TEMP_NAME = re.compile(r"^\.[0-9a-f]{64}\.[0-9a-f]{32}\.tmp$")
_PROCESS_LOCK = threading.Lock()

_SUCCESS = {
    "continue": True,
    "systemMessage": (
        "Continue from the compacted context. Preserve explicit stop conditions "
        "and verify required evidence before claiming completion."
    ),
}
_SESSION_COMPACT = {
    "continue": True,
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": (
            "Compaction continuity: preserve explicit stop conditions and verify "
            "required evidence before claiming completion."
        ),
    },
}
_NO_CONTEXT = {"continue": True}
_FAILURE = {
    "continue": False,
    "stopReason": "Compaction hook invariant check failed.",
}


def _digest(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _directory_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _open_child_directory(parent_fd: int, name: str, *, create: bool) -> int:
    if name in {"", ".", ".."} or "/" in name:
        raise OSError("unsafe state directory component")
    try:
        child_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        child_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    return child_fd


def _validate_private_directory(directory_fd: int, *, exact_mode: bool) -> None:
    info = os.fstat(directory_fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise OSError("unsafe state directory")
    if exact_mode:
        os.fchmod(directory_fd, 0o700)
        if os.fstat(directory_fd).st_mode & 0o777 != 0o700:
            raise OSError("unsafe state directory permissions")


def _open_absolute_directory(path: Path) -> int:
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    if not absolute.is_absolute() or absolute == Path("/"):
        raise OSError("unsafe CODEX_HOME boundary")
    current_fd = os.open("/", _directory_flags())
    try:
        for part in absolute.parts[1:]:
            child_fd = _open_child_directory(current_fd, part, create=True)
            os.close(current_fd)
            current_fd = child_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_private_state_root(codex_home: Path | str) -> int:
    """Return an owned directory fd that remains safe if path names are swapped."""
    base_fd = _open_absolute_directory(Path(codex_home))
    current_fd = base_fd
    try:
        _validate_private_directory(base_fd, exact_mode=False)
        for part in Path(STATE_SUBDIRECTORY).parts:
            child_fd = _open_child_directory(current_fd, part, create=True)
            if current_fd != base_fd:
                os.close(current_fd)
            current_fd = child_fd
            _validate_private_directory(current_fd, exact_mode=True)
        return current_fd
    except Exception:
        if current_fd != base_fd:
            os.close(current_fd)
        raise
    finally:
        os.close(base_fd)


def _record_name(event: dict[str, Any]) -> str:
    correlation = hashlib.sha256(
        (_digest(event.get("session_id")) + ":" + _digest(event.get("turn_id"))).encode("ascii")
    ).hexdigest()
    return f"{correlation}.json"


def _validate_record_name(record_name: str) -> None:
    if _RECORD_NAME.fullmatch(record_name) is None:
        raise OSError("unsafe record name")


def _record_info(directory_fd: int, record_name: str) -> os.stat_result | None:
    try:
        return os.stat(record_name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _validate_record_info(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o777 != 0o600
        or info.st_size > 4096
    ):
        raise OSError("unsafe record")


def _atomic_write(directory_fd: int, record_name: str, payload: dict[str, Any]) -> None:
    _validate_record_name(record_name)
    existing = _record_info(directory_fd, record_name)
    if existing is not None:
        _validate_record_info(existing)

    temp_name = f".{record_name[:-5]}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        current = _record_info(directory_fd, record_name)
        if current is not None:
            _validate_record_info(current)
        os.replace(
            temp_name,
            record_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temp_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _read_record(directory_fd: int, record_name: str) -> dict[str, Any]:
    _validate_record_name(record_name)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(record_name, flags, dir_fd=directory_fd)
    info = os.fstat(fd)
    _validate_record_info(info)
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("invalid record")
    return value


def _unlink_unchanged(directory_fd: int, name: str, expected: os.stat_result) -> None:
    try:
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise OSError("record changed during retention cleanup")
    os.unlink(name, dir_fd=directory_fd)


def _prune_records(directory_fd: int, *, now: float, reserve: int) -> None:
    records: list[tuple[float, str, os.stat_result]] = []
    expired_temps: list[tuple[str, os.stat_result]] = []
    with os.scandir(directory_fd) as entries:
        for count, entry in enumerate(entries, start=1):
            if count > MAX_DIRECTORY_ENTRIES:
                raise OSError("too many compaction state entries")
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if _RECORD_NAME.fullmatch(entry.name):
                _validate_record_info(info)
                records.append((info.st_mtime, entry.name, info))
            elif _TEMP_NAME.fullmatch(entry.name):
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                    raise OSError("unsafe temporary record")
                if info.st_mtime < now - RECORD_TTL_SECONDS:
                    expired_temps.append((entry.name, info))

    keep = max(0, MAX_RECORDS - reserve)
    current = [record for record in records if record[0] >= now - RECORD_TTL_SECONDS]
    current.sort(key=lambda item: (item[0], item[1]), reverse=True)
    removals = [record for record in records if record[0] < now - RECORD_TTL_SECONDS]
    removals.extend(current[keep:])
    for _mtime, name, info in removals:
        _unlink_unchanged(directory_fd, name, info)
    for name, info in expired_temps:
        _unlink_unchanged(directory_fd, name, info)
    if removals or expired_temps:
        os.fsync(directory_fd)


def _run_with_deadline(timeout_seconds: float, operation: Any) -> Any:
    if timeout_seconds <= 0 or not hasattr(signal, "setitimer"):
        raise RuntimeError("external hook deadline unavailable")

    def timeout_handler(_signum: int, _frame: Any) -> None:
        raise TimeoutError("external hook deadline")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        return operation()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def process_event(
    event: dict[str, Any],
    *,
    codex_home: Path | str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    if not isinstance(event, dict):
        return dict(_FAILURE)

    event_name = event.get("hook_event_name")
    if event_name == "SessionStart":
        return dict(_SESSION_COMPACT if event.get("source") == "compact" else _NO_CONTEXT)
    if event_name not in {"PreCompact", "PostCompact"}:
        return dict(_FAILURE)

    try:
        base = Path(
            codex_home
            if codex_home is not None
            else os.environ.get("CODEX_HOME", "").strip() or Path.home() / ".codex"
        )
        directory_fd = _open_private_state_root(base)
        record_name = _record_name(event)
        session_digest = _digest(event.get("session_id"))
        turn_digest = _digest(event.get("turn_id"))
        if not event.get("session_id") or not event.get("turn_id"):
            raise ValueError("missing correlation")

        try:
            with _PROCESS_LOCK:
                fcntl.flock(directory_fd, fcntl.LOCK_EX)
                try:
                    _prune_records(
                        directory_fd,
                        now=time.time(),
                        reserve=1 if event_name == "PreCompact" else 0,
                    )
                    if event_name == "PreCompact":
                        _atomic_write(
                            directory_fd,
                            record_name,
                            {
                                "schema": 1,
                                "session": session_digest,
                                "turn": turn_digest,
                                "prepared": True,
                            },
                        )
                    else:
                        record = _read_record(directory_fd, record_name)
                        if (
                            record.get("schema") != 1
                            or record.get("session") != session_digest
                            or record.get("turn") != turn_digest
                            or record.get("prepared") is not True
                        ):
                            raise ValueError("correlation mismatch")
                        _atomic_write(
                            directory_fd,
                            record_name,
                            {
                                "schema": 1,
                                "session": session_digest,
                                "turn": turn_digest,
                                "prepared": True,
                                "completed": True,
                            },
                        )
                finally:
                    fcntl.flock(directory_fd, fcntl.LOCK_UN)
        finally:
            os.close(directory_fd)

        if time.monotonic() - started >= INTERNAL_TIMEOUT_SECONDS:
            raise TimeoutError("internal deadline")
        return dict(_SUCCESS)
    except Exception:
        return dict(_FAILURE)


def main() -> int:
    def run_once() -> tuple[dict[str, Any], int]:
        payload = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        if len(payload) > MAX_STDIN_BYTES:
            return dict(_FAILURE), 2
        try:
            event = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            event = None
        output = process_event(event)
        return output, 0 if output.get("continue") is True else 2

    try:
        output, status = _run_with_deadline(INTERNAL_TIMEOUT_SECONDS, run_once)
    except Exception:
        output, status = dict(_FAILURE), 2
    sys.stdout.write(json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
