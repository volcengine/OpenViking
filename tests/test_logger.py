# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the resilient TimedRotatingFileHandler used by CLI logging."""

import logging
import os
import sys
import time

import pytest

from openviking_cli.utils.logger import _ResilientTimedRotatingFileHandler


@pytest.fixture(autouse=True)
def _isolated_logger():
    """Ensure handlers do not leak into the root logger between tests."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    for h in saved_handlers:
        root.removeHandler(h)
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


def _make_handler(tmp_path, when="S", interval=1):
    log_file = tmp_path / "server.log"
    handler = _ResilientTimedRotatingFileHandler(
        str(log_file),
        when=when,
        interval=interval,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    return log_file, handler


def test_successful_rollover_rotates_file(tmp_path):
    log_file, handler = _make_handler(tmp_path, when="S", interval=1)
    logger = logging.getLogger("test_rollover_ok")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("before rollover")
    handler.flush()
    assert log_file.exists()

    # Force the next emit to cross the per-second rollover boundary.
    time.sleep(1.05)
    logger.info("after rollover")
    handler.flush()

    rotated = [
        p for p in tmp_path.iterdir()
        if p.name.startswith("server.log.") and p.is_file()
    ]
    assert rotated, "a dated backup should exist after a successful rollover"
    assert log_file.exists()


def test_failed_rollover_reopens_stream(tmp_path, monkeypatch):
    log_file, handler = _make_handler(tmp_path)
    logger = logging.getLogger("test_stream_recovery")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("prime")
    handler.flush()
    assert handler.stream is not None

    # Simulate a filesystem failure during the rename step of doRollover.
    # At that point stdlib has already closed and nulled self.stream.
    def _fail_rename(src, dst, *args, **kwargs):
        raise PermissionError("simulated locked destination")

    monkeypatch.setattr(os, "rename", _fail_rename)
    # Also block the Windows-specific path so the failure reproduces there.
    if hasattr(os, "replace"):
        monkeypatch.setattr(os, "replace", _fail_rename)

    time.sleep(1.05)
    logger.info("trigger failed rollover")
    handler.flush()

    assert handler.stream is not None, (
        "stream must be reopened so subsequent writes are not silently lost"
    )
    # The original file must still accept writes.
    logger.info("post failure")
    handler.flush()
    assert "post failure" in log_file.read_text(encoding="utf-8")


def test_failed_rollover_warns_to_stderr(tmp_path, monkeypatch, capsys):
    log_file, handler = _make_handler(tmp_path)
    logger = logging.getLogger("test_stderr_warning")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("prime")
    handler.flush()

    def _fail_rename(src, dst, *args, **kwargs):
        raise OSError("simulated IO error")

    monkeypatch.setattr(os, "rename", _fail_rename)
    if hasattr(os, "replace"):
        monkeypatch.setattr(os, "replace", _fail_rename)

    time.sleep(1.05)
    logger.info("trigger")
    handler.flush()

    captured = capsys.readouterr()
    assert "log rotation failed" in captured.err
    assert log_file.name in captured.err


def test_rollover_is_retried_after_failure(tmp_path, monkeypatch):
    log_file, handler = _make_handler(tmp_path)
    logger = logging.getLogger("test_retry")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("prime")
    handler.flush()

    failures = {"count": 0}

    def _flaky_rename(src, dst, *args, **kwargs):
        failures["count"] += 1
        if failures["count"] == 1:
            raise PermissionError("temporary lock")
        # Second attempt delegates to the real implementation.
        monkeypatch.undo()
        return os.rename(src, dst)

    monkeypatch.setattr(os, "rename", _flaky_rename)
    if hasattr(os, "replace"):
        monkeypatch.setattr(os, "replace", _flaky_rename)

    time.sleep(1.05)
    logger.info("first attempt fails")
    handler.flush()
    assert handler.stream is not None
    # rolloverAt must have been pushed into the future so we can try again.
    assert handler.rolloverAt > time.time()

    time.sleep(1.05)
    logger.info("second attempt succeeds")
    handler.flush()

    rotated = [
        p for p in tmp_path.iterdir()
        if p.name.startswith("server.log.") and p.is_file()
    ]
    assert rotated, "a later successful emit should complete the rollover"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="fcntl is POSIX-only")
def test_rollover_lock_is_held_during_rollover(tmp_path, monkeypatch):
    import fcntl

    log_file, handler = _make_handler(tmp_path)
    observed: dict[str, "bool | None"] = {"lock_present_during": None}

    from logging.handlers import TimedRotatingFileHandler

    real_do_rollover = TimedRotatingFileHandler.doRollover

    def _check_lock_during_rollover(self):
        lock_fd = os.open(str(self._rollover_lock_path), os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed["lock_present_during"] = False
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except BlockingIOError:
            observed["lock_present_during"] = True
        finally:
            os.close(lock_fd)
        return real_do_rollover(self)

    monkeypatch.setattr(
        TimedRotatingFileHandler, "doRollover", _check_lock_during_rollover
    )

    logger = logging.getLogger("test_lock_held")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("prime")
    handler.flush()
    time.sleep(1.05)
    logger.info("trigger rollover")
    handler.flush()

    assert observed["lock_present_during"] is True, (
        "an exclusive advisory lock must be held for the duration of doRollover"
    )


@pytest.mark.skipif(sys.platform.startswith("win"), reason="fcntl is POSIX-only")
def test_rollover_proceeds_when_lock_file_unwritable(tmp_path):
    # If the advisory lock file cannot be opened (e.g. an unexpected entry
    # already exists at that path), the handler must silently fall back to
    # in-process locking instead of breaking rotation. The log directory itself
    # is valid; only the lock-side open fails.
    log_file, handler = _make_handler(tmp_path)
    # A directory at the lock path makes os.open(O_CREAT | O_RDWR) raise EISDIR.
    handler._rollover_lock_path.mkdir()

    logger = logging.getLogger("test_lock_fallback")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("before rollover")
    handler.flush()

    time.sleep(1.05)
    logger.info("after rollover")
    handler.flush()

    rotated = [
        p for p in tmp_path.iterdir()
        if p.name.startswith("server.log.") and p.is_file()
    ]
    assert rotated, (
        "rollover must still succeed when the cross-process lock is unavailable"
    )
    assert log_file.exists()
    assert handler.stream is not None
