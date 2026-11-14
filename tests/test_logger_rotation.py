# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import logging
import os
import time
from unittest.mock import Mock

import pytest

from openviking_cli.utils import logger as logger_module


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("delay", [False, True])
def test_timed_rollover_preserves_records(tmp_path, monkeypatch, capsys, failure, delay):
    log_path = tmp_path / "server.log"
    handler = logger_module._RustAwareTimedRotatingFileHandler(
        log_path, when="midnight", backupCount=7, encoding="utf-8", delay=delay
    )
    reopen = Mock()
    monkeypatch.setattr(logger_module, "_reopen_rust_tracing_file", reopen)
    rename = os.rename
    attempts = []

    def rotate(source, destination):
        attempts.append(destination)
        assert handler.stream is None
        if failure:
            raise PermissionError("simulated sharing violation")
        rename(source, destination)

    monkeypatch.setattr(os, "rename", rotate)

    def record(message):
        return logging.makeLogRecord({"msg": message, "levelno": logging.INFO})

    try:
        handler.emit(record("before"))
        handler.rolloverAt = int(time.time()) - 1
        for message in ("trigger", "later", "last"):
            handler.emit(record(message))
        handler.flush()
        assert log_path.read_text(encoding="utf-8") == (
            "before\ntrigger\nlater\nlast\n" if failure else "trigger\nlater\nlast\n"
        )
        assert len(attempts) == 1
        assert handler.rolloverAt > time.time()
        reopen.assert_called_once_with()
        if failure:
            assert not list(tmp_path.glob("server.log.*"))
            assert capsys.readouterr().err.count("log rotation failed") == 1
            # A failed rollover is retried at the next boundary, not every record.
            failure = False
            handler.rolloverAt = int(time.time()) - 1
            handler.emit(record("retry"))
            assert log_path.read_text(encoding="utf-8") == "retry\n"
            assert len(attempts) == 2
            assert reopen.call_count == 2
        else:
            assert len(list(tmp_path.glob("server.log.*"))) == 1
            assert next(tmp_path.glob("server.log.*")).read_text(encoding="utf-8") == "before\n"
            assert capsys.readouterr().err == ""
    finally:
        handler.close()


def test_failed_rollover_keeps_record_when_stderr_is_unavailable(tmp_path, monkeypatch):
    log_path = tmp_path / "server.log"
    handler = logger_module._RustAwareTimedRotatingFileHandler(log_path, when="midnight")
    monkeypatch.setattr(logger_module, "_reopen_rust_tracing_file", Mock())
    monkeypatch.setattr(os, "rename", Mock(side_effect=PermissionError("sharing violation")))
    monkeypatch.setattr(
        logger_module.sys, "stderr", Mock(write=Mock(side_effect=OSError("closed")))
    )
    try:
        handler.rolloverAt = int(time.time()) - 1
        handler.emit(logging.makeLogRecord({"msg": "still written", "levelno": logging.INFO}))
        assert log_path.read_text(encoding="utf-8") == "still written\n"
    finally:
        handler.close()


@pytest.mark.parametrize("windows", [False, True])
def test_sibling_handlers_share_rollover(tmp_path, monkeypatch, windows):
    from types import SimpleNamespace

    from openviking_cli.utils import log_rotation

    if windows:
        from contextlib import nullcontext

        # Simulate handle lifetime only, not msvcrt locking.
        monkeypatch.setattr(log_rotation, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
        monkeypatch.setattr(log_rotation, "interprocess_lock", lambda _path: nullcontext())
    reopen = Mock()
    monkeypatch.setattr(logger_module, "_reopen_rust_tracing_file", reopen)
    path = tmp_path / "server.log"
    handlers = [
        logger_module._RustAwareTimedRotatingFileHandler(path, when="midnight") for _ in range(2)
    ]
    rename = os.rename

    def rotate(source, destination):
        if windows and any(handler.stream is not None for handler in handlers):
            raise PermissionError("sibling still holds the file")
        rename(source, destination)

    monkeypatch.setattr(os, "rename", rotate)
    try:
        for index, handler in enumerate(handlers):
            handler.emit(logging.makeLogRecord({"msg": f"before {index}"}))
            if windows:
                assert handler.stream is None
            handler.rolloverAt = int(time.time()) - 1
        reopen.reset_mock()
        for index, handler in enumerate(handlers):
            handler.emit(logging.makeLogRecord({"msg": f"after {index}"}))
            if windows:
                assert handler.stream is None
        backups = list(tmp_path.glob("server.log.*"))
        assert len(backups) == 1
        assert backups[0].read_text() == "before 0\nbefore 1\n"
        assert path.read_text() == "after 0\nafter 1\n"
        assert reopen.call_count == 2
    finally:
        for handler in handlers:
            handler.close()


@pytest.mark.skipif(
    os.name == "nt" or os.getuid() == 0, reason="requires POSIX directory permissions"
)
def test_lock_directory_unwritable_keeps_logging(tmp_path):
    path = tmp_path / "server.log"
    handler = logger_module._RustAwareTimedRotatingFileHandler(path, when="midnight")
    try:
        tmp_path.chmod(0o500)
        handler.emit(logging.makeLogRecord({"msg": "still written"}))
        assert path.read_text() == "still written\n"
    finally:
        tmp_path.chmod(0o700)
        handler.close()


def test_rust_reopen_can_log_without_reentering_the_process_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from openviking_cli.utils import log_rotation

    locked = False

    @contextmanager
    def lock(_path):
        nonlocal locked
        assert not locked, "nested process lock would deadlock"
        locked = True
        try:
            yield
        finally:
            locked = False

    handler = logger_module._RustAwareTimedRotatingFileHandler(
        tmp_path / "server.log", when="midnight"
    )

    def reopen():
        assert not locked
        handler.emit(logging.makeLogRecord({"msg": "from reopen"}))

    monkeypatch.setattr(log_rotation, "interprocess_lock", lock)
    monkeypatch.setattr(logger_module, "_reopen_rust_tracing_file", reopen)
    try:
        handler.rolloverAt = int(time.time()) - 1
        handler.emit(logging.makeLogRecord({"msg": "trigger"}))
        assert (tmp_path / "server.log").read_text() == "trigger\nfrom reopen\n"
    finally:
        handler.close()


def _hold_process_lock(path, ready, release):
    from openviking_cli.utils.log_rotation import interprocess_lock

    with interprocess_lock(path):
        ready.set()
        assert release.wait(timeout=30), "parent did not release the lock holder"


def test_native_process_lock_excludes_other_processes(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    path = tmp_path / ".server.log.lock"
    worker = context.Process(target=_hold_process_lock, args=(path, ready, release))
    descriptor = None
    try:
        worker.start()
        assert ready.wait(timeout=30), f"lock holder exitcode={worker.exitcode}"
        descriptor = os.open(path, os.O_RDWR)
        if os.name == "nt":
            import msvcrt

            def acquire():
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)

            def unlock():
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock():
                fcntl.flock(descriptor, fcntl.LOCK_UN)

        with pytest.raises(OSError):
            acquire()
        release.set()
        worker.join(timeout=10)
        assert worker.exitcode == 0, f"lock holder exitcode={worker.exitcode}"
        acquire()
        unlock()
    finally:
        release.set()
        if descriptor is not None:
            os.close(descriptor)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=5)
        assert not worker.is_alive(), f"lock holder {worker.pid} did not exit"


def _write_worker_records(path, barrier, index, deadline):
    handler = logger_module._RustAwareTimedRotatingFileHandler(path, when="midnight")
    try:
        handler.emit(logging.makeLogRecord({"msg": f"before {index}"}))
        handler.rolloverAt = deadline
        barrier.wait(timeout=30)
        for number in range(20):
            handler.emit(logging.makeLogRecord({"msg": f"after {index} {number}"}))
    finally:
        handler.close()


@pytest.mark.skipif(os.name == "nt", reason="real POSIX worker-process regression")
def test_worker_processes_keep_post_rollover_records_out_of_backup(tmp_path):
    _assert_worker_rotation(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows handles and msvcrt locks")
def test_windows_worker_processes_keep_post_rollover_records_out_of_backup(tmp_path):
    _assert_worker_rotation(tmp_path)


def _assert_worker_rotation(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    path = tmp_path / "server.log"
    # One absolute boundary prevents different backup names across clock ticks.
    deadline = int(time.time()) - 60
    workers = [
        context.Process(target=_write_worker_records, args=(path, barrier, i, deadline))
        for i in range(2)
    ]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=45)
            assert worker.exitcode == 0, f"worker {worker.pid}: exitcode={worker.exitcode}"
        backups = list(tmp_path.glob("server.log.*"))
        assert len(backups) == 1
        assert sorted(backups[0].read_text().splitlines()) == ["before 0", "before 1"]
        assert sorted(path.read_text().splitlines()) == sorted(
            f"after {index} {number}" for index in range(2) for number in range(20)
        )
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=5)
        assert not any(worker.is_alive() for worker in workers), "workers did not exit"


def test_failed_rollover_with_nonpositive_interval_does_not_loop(tmp_path, monkeypatch):
    handler = logger_module._RustAwareTimedRotatingFileHandler(
        tmp_path / "server.log", when="midnight"
    )
    rename = Mock(side_effect=PermissionError("sharing violation"))
    monkeypatch.setattr(logger_module, "_reopen_rust_tracing_file", Mock())
    try:
        # Windows opens lazily: create the source before testing failed rename.
        handler.emit(logging.makeLogRecord({"msg": "before"}))
        monkeypatch.setattr(os, "rename", rename)
        monkeypatch.setattr(handler, "computeRollover", lambda now: now)
        handler.interval = 0
        handler.rolloverAt = int(time.time()) - 1
        handler.emit(logging.makeLogRecord({"msg": "still written"}))
        assert handler.rolloverAt > time.time()
        rename.assert_called_once()
        assert (tmp_path / "server.log").read_text() == "before\nstill written\n"
    finally:
        handler.close()


def test_lock_timeout_falls_back_without_raising(tmp_path, monkeypatch):
    from openviking_cli.utils import log_rotation

    handler = logger_module._RustAwareTimedRotatingFileHandler(
        tmp_path / "server.log", when="midnight"
    )
    monkeypatch.setattr(
        log_rotation, "interprocess_lock", Mock(side_effect=OSError("lock timeout"))
    )
    logger = logging.Logger("lock-timeout")
    logger.addHandler(handler)
    try:
        logger.warning("still written")
        assert (tmp_path / "server.log").read_text() == "still written\n"
    finally:
        handler.close()
