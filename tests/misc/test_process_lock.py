"""Tests for PID-based advisory lock on data directories."""

import os
import tempfile

from openviking.utils.process_lock import (
    LOCK_FILENAME,
    DataDirectoryLocked,
    acquire_data_dir_lock,
)


class TestProcessLock:
    def test_acquires_lock_on_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = acquire_data_dir_lock(tmpdir)
            assert os.path.isfile(lock_path)
            with open(lock_path) as f:
                assert int(f.read().strip()) == os.getpid()

    def test_same_pid_can_reacquire(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            acquire_data_dir_lock(tmpdir)
            # Should not raise when same process re-acquires.
            acquire_data_dir_lock(tmpdir)

    def test_stale_lock_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, LOCK_FILENAME)
            # Write a PID that does not exist (very high number).
            with open(lock_path, "w") as f:
                f.write("999999999")
            # Should succeed because the PID is dead.
            acquire_data_dir_lock(tmpdir)
            with open(lock_path) as f:
                assert int(f.read().strip()) == os.getpid()

    def test_live_pid_blocks_acquisition(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, LOCK_FILENAME)
            # PID 1 (init/launchd) is always alive.
            with open(lock_path, "w") as f:
                f.write("1")
            try:
                acquire_data_dir_lock(tmpdir)
                raise AssertionError("Should have raised DataDirectoryLocked")
            except DataDirectoryLocked as exc:
                assert "PID 1" in str(exc)
                assert "HTTP mode" in str(exc)

    def test_error_message_includes_remediation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, LOCK_FILENAME)
            with open(lock_path, "w") as f:
                f.write("1")
            try:
                acquire_data_dir_lock(tmpdir)
            except DataDirectoryLocked as exc:
                msg = str(exc)
                assert "openviking-server" in msg
                assert "separate data directories" in msg
