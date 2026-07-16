# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for process-lock shutdown behavior."""

import signal
from pathlib import Path

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


def test_release_data_dir_lock_preserves_another_process_lock(tmp_path: Path):
    lock_path = tmp_path / LOCK_FILENAME
    lock_path.write_text("1")

    release_data_dir_lock(str(lock_path))

    assert lock_path.read_text() == "1"
