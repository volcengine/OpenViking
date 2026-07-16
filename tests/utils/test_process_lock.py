# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for process-lock shutdown behavior."""

import os
import signal
import threading
from pathlib import Path

import pytest

import openviking.utils.process_lock as process_lock_module
from openviking.utils.process_lock import (
    LOCK_FILENAME,
    acquire_data_dir_lock,
    release_data_dir_lock,
)


def test_acquire_data_dir_lock_preserves_sigterm_handler(tmp_path: Path):
    """The lock must not replace the host process's shutdown handler."""

    def host_sigterm_handler(_signum, _frame):
        return None

    previous_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, host_sigterm_handler)
    try:
        acquire_data_dir_lock(str(tmp_path))

        assert signal.getsignal(signal.SIGTERM) is host_sigterm_handler
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def test_acquire_data_dir_lock_registers_atexit_cleanup(tmp_path: Path, monkeypatch):
    """Normal interpreter shutdown still removes the PID file via atexit."""
    callbacks = []
    monkeypatch.setattr(process_lock_module.atexit, "register", callbacks.append)

    acquire_data_dir_lock(str(tmp_path))

    assert len(callbacks) == 1
    lock_path = tmp_path / LOCK_FILENAME
    assert lock_path.exists()

    callbacks[0]()

    assert not lock_path.exists()


def test_release_data_dir_lock_removes_owned_lock(tmp_path: Path):
    lock_path = acquire_data_dir_lock(str(tmp_path))

    release_data_dir_lock(lock_path)

    assert not Path(lock_path).exists()


def test_reentrant_lock_is_removed_only_after_last_release(tmp_path: Path):
    first_lock_path = acquire_data_dir_lock(str(tmp_path))
    second_lock_path = acquire_data_dir_lock(str(tmp_path))

    release_data_dir_lock(first_lock_path)

    assert Path(first_lock_path).exists()

    release_data_dir_lock(second_lock_path)

    assert not Path(second_lock_path).exists()


def test_acquire_and_refcount_update_are_atomic(tmp_path: Path, monkeypatch):
    lock_path = tmp_path / LOCK_FILENAME
    first_write_closed = threading.Event()
    allow_first_acquire = threading.Event()
    second_acquire_started = threading.Event()
    second_acquire_done = threading.Event()
    thread_errors = []
    acquired_paths = []
    real_open = open

    class BlockingClose:
        def __init__(self, file_obj):
            self._file_obj = file_obj

        def __enter__(self):
            return self._file_obj.__enter__()

        def __exit__(self, exc_type, exc_value, traceback):
            result = self._file_obj.__exit__(exc_type, exc_value, traceback)
            first_write_closed.set()
            if not allow_first_acquire.wait(timeout=5):
                raise TimeoutError("first acquire was not released")
            return result

    def blocking_open(path, mode="r", *args, **kwargs):
        file_obj = real_open(path, mode, *args, **kwargs)
        if os.fspath(path) == str(lock_path) and "w" in mode and not first_write_closed.is_set():
            return BlockingClose(file_obj)
        return file_obj

    def acquire_first():
        try:
            acquired_paths.append(acquire_data_dir_lock(str(tmp_path)))
        except BaseException as exc:  # pragma: no cover - surfaced by the assertion below
            thread_errors.append(exc)

    def acquire_second():
        try:
            second_acquire_started.set()
            acquired_paths.append(acquire_data_dir_lock(str(tmp_path)))
        except BaseException as exc:  # pragma: no cover - surfaced by the assertion below
            thread_errors.append(exc)
        finally:
            second_acquire_done.set()

    monkeypatch.setattr(process_lock_module, "open", blocking_open, raising=False)
    first_thread = threading.Thread(target=acquire_first)
    second_thread = threading.Thread(target=acquire_second)
    first_thread.start()
    assert first_write_closed.wait(timeout=5)
    second_thread.start()
    assert second_acquire_started.wait(timeout=5)
    try:
        assert not second_acquire_done.wait(timeout=0.2)
    finally:
        allow_first_acquire.set()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert not thread_errors
    assert len(acquired_paths) == 2
    release_data_dir_lock(acquired_paths[0])
    assert lock_path.exists()
    release_data_dir_lock(acquired_paths[1])
    assert not lock_path.exists()


def test_reentrant_acquire_does_not_rewrite_owned_pid_file(tmp_path: Path, monkeypatch):
    first_lock_path = acquire_data_dir_lock(str(tmp_path))
    real_open = open

    def fail_writes(path, mode="r", *args, **kwargs):
        if os.fspath(path) == first_lock_path and "w" in mode:
            raise OSError("simulated write failure")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(process_lock_module, "open", fail_writes, raising=False)

    second_lock_path = acquire_data_dir_lock(str(tmp_path))
    release_data_dir_lock(second_lock_path)

    assert Path(first_lock_path).exists()

    release_data_dir_lock(first_lock_path)
    assert not Path(first_lock_path).exists()


def test_initial_write_failure_is_not_reported_as_acquired(tmp_path: Path, monkeypatch):
    def fail_writes(_path, _mode="r", *_args, **_kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(process_lock_module, "open", fail_writes, raising=False)

    with pytest.raises(OSError, match="simulated write failure"):
        acquire_data_dir_lock(str(tmp_path))


def test_release_data_dir_lock_preserves_another_process_lock(tmp_path: Path):
    lock_path = tmp_path / LOCK_FILENAME
    another_pid = os.getpid() + 1
    lock_path.write_text(str(another_pid))

    release_data_dir_lock(str(lock_path))

    assert lock_path.read_text() == str(another_pid)
