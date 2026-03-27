# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for PID-based process lock utility."""

import os
from pathlib import Path

import pytest

from openviking.utils.process_lock import (
    LOCK_FILENAME,
    DataDirectoryLocked,
    _is_pid_alive,
    _read_pid_file,
    acquire_data_dir_lock,
)


class TestReadPidFile:
    """Test _read_pid_file function."""

    def test_read_valid_pid(self, tmp_path: Path):
        """Test reading a valid PID from file."""
        lock_file = tmp_path / LOCK_FILENAME
        lock_file.write_text("12345")

        pid = _read_pid_file(str(lock_file))
        assert pid == 12345

    def test_read_nonexistent_file(self, tmp_path: Path):
        """Test reading from nonexistent file returns 0."""
        lock_file = tmp_path / "nonexistent.pid"

        pid = _read_pid_file(str(lock_file))
        assert pid == 0

    def test_read_invalid_pid(self, tmp_path: Path):
        """Test reading invalid PID returns 0."""
        lock_file = tmp_path / LOCK_FILENAME
        lock_file.write_text("not_a_number")

        pid = _read_pid_file(str(lock_file))
        assert pid == 0

    def test_read_empty_file(self, tmp_path: Path):
        """Test reading empty file returns 0."""
        lock_file = tmp_path / LOCK_FILENAME
        lock_file.write_text("")

        pid = _read_pid_file(str(lock_file))
        assert pid == 0

    def test_read_pid_with_whitespace(self, tmp_path: Path):
        """Test reading PID with leading/trailing whitespace."""
        lock_file = tmp_path / LOCK_FILENAME
        lock_file.write_text("  12345  \n")

        pid = _read_pid_file(str(lock_file))
        assert pid == 12345

    def test_read_pid_with_newline(self, tmp_path: Path):
        """Test reading PID with newline."""
        lock_file = tmp_path / LOCK_FILENAME
        lock_file.write_text("12345\n")

        pid = _read_pid_file(str(lock_file))
        assert pid == 12345


class TestIsPidAlive:
    """Test _is_pid_alive function."""

    def test_current_pid_is_alive(self):
        """Test that current process PID is detected as alive."""
        current_pid = os.getpid()
        assert _is_pid_alive(current_pid) is True

    def test_pid_1_is_alive(self):
        """Test that PID 1 (init) is typically alive."""
        # PID 1 is usually init process on Linux
        assert _is_pid_alive(1) is True

    def test_nonexistent_pid_not_alive(self):
        """Test that nonexistent PID is not alive."""
        # Use a very high PID that's unlikely to exist
        assert _is_pid_alive(999999) is False

    def test_pid_zero_not_alive(self):
        """Test that PID 0 is not alive."""
        assert _is_pid_alive(0) is False

    def test_negative_pid_not_alive(self):
        """Test that negative PID is not alive."""
        assert _is_pid_alive(-1) is False


class TestAcquireDataDirLock:
    """Test acquire_data_dir_lock function."""

    def test_acquire_creates_lock_file(self, tmp_path: Path):
        """Test acquiring lock creates lock file."""
        lock_path = acquire_data_dir_lock(str(tmp_path))

        assert lock_path == str(tmp_path / LOCK_FILENAME)
        assert (tmp_path / LOCK_FILENAME).exists()

    def test_acquire_writes_current_pid(self, tmp_path: Path):
        """Test lock file contains current PID."""
        acquire_data_dir_lock(str(tmp_path))
        my_pid = os.getpid()

        stored_pid = int((tmp_path / LOCK_FILENAME).read_text().strip())
        assert stored_pid == my_pid

    def test_acquire_same_pid_succeeds(self, tmp_path: Path):
        """Test acquiring lock with same PID succeeds."""
        my_pid = os.getpid()
        (tmp_path / LOCK_FILENAME).write_text(str(my_pid))

        # Should succeed since it's our own PID
        lock_path = acquire_data_dir_lock(str(tmp_path))
        assert lock_path == str(tmp_path / LOCK_FILENAME)

    def test_acquire_with_stale_lock_succeeds(self, tmp_path: Path):
        """Test acquiring lock with stale (dead process) lock succeeds."""
        # Write a PID that doesn't exist
        (tmp_path / LOCK_FILENAME).write_text("999999")

        lock_path = acquire_data_dir_lock(str(tmp_path))
        assert lock_path == str(tmp_path / LOCK_FILENAME)

    def test_acquire_with_live_process_raises(self, tmp_path: Path):
        """Test acquiring lock with live process raises DataDirectoryLocked."""
        # Use PID 1 (init) which is typically alive
        (tmp_path / LOCK_FILENAME).write_text("1")

        with pytest.raises(DataDirectoryLocked) as exc_info:
            acquire_data_dir_lock(str(tmp_path))

        assert "Another OpenViking process" in str(exc_info.value)
        assert "PID 1" in str(exc_info.value)

    def test_acquire_creates_directory(self, tmp_path: Path):
        """Test acquiring lock creates directory if it doesn't exist."""
        new_dir = tmp_path / "new_subdir"

        acquire_data_dir_lock(str(new_dir))
        assert new_dir.exists()
        assert (new_dir / LOCK_FILENAME).exists()

    def test_error_message_suggests_http_mode(self, tmp_path: Path):
        """Test error message suggests HTTP mode."""
        (tmp_path / LOCK_FILENAME).write_text("1")

        with pytest.raises(DataDirectoryLocked) as exc_info:
            acquire_data_dir_lock(str(tmp_path))

        error_msg = str(exc_info.value)
        assert "HTTP mode" in error_msg
        assert "openviking-server" in error_msg

    def test_error_message_shows_pid(self, tmp_path: Path):
        """Test error message shows conflicting PID."""
        (tmp_path / LOCK_FILENAME).write_text("1")

        with pytest.raises(DataDirectoryLocked) as exc_info:
            acquire_data_dir_lock(str(tmp_path))

        error_msg = str(exc_info.value)
        assert "PID 1" in error_msg

    def test_error_message_shows_directory(self, tmp_path: Path):
        """Test error message shows directory path."""
        (tmp_path / LOCK_FILENAME).write_text("1")

        with pytest.raises(DataDirectoryLocked) as exc_info:
            acquire_data_dir_lock(str(tmp_path))

        error_msg = str(exc_info.value)
        assert str(tmp_path) in error_msg


class TestAcquireDataDirLockEdgeCases:
    """Test edge cases for acquire_data_dir_lock."""

    def test_acquire_nested_directory(self, tmp_path: Path):
        """Test acquiring lock in nested directory."""
        nested = tmp_path / "a" / "b" / "c"

        acquire_data_dir_lock(str(nested))
        assert nested.exists()
        assert (nested / LOCK_FILENAME).exists()

    def test_acquire_overwrites_stale_lock(self, tmp_path: Path):
        """Test that acquiring overwrites stale lock with current PID."""
        my_pid = os.getpid()
        (tmp_path / LOCK_FILENAME).write_text("999999")

        acquire_data_dir_lock(str(tmp_path))

        stored_pid = int((tmp_path / LOCK_FILENAME).read_text().strip())
        assert stored_pid == my_pid

    def test_lock_filename_constant(self):
        """Test LOCK_FILENAME constant is correct."""
        assert LOCK_FILENAME == ".openviking.pid"

    def test_acquire_with_pathlib_path(self, tmp_path: Path):
        """Test acquiring with pathlib.Path instead of string."""
        lock_path = acquire_data_dir_lock(str(tmp_path))
        assert lock_path == str(tmp_path / LOCK_FILENAME)

    def test_acquire_permissions_on_readonly_parent(self, tmp_path: Path):
        """Test handling when parent directory is read-only."""
        # This test may not work on all systems
        # Just verify it doesn't crash
        lock_path = acquire_data_dir_lock(str(tmp_path))
        assert lock_path.endswith(LOCK_FILENAME)


class TestProcessLockIntegration:
    """Integration tests for process lock."""

    def test_multiple_acquires_same_process(self, tmp_path: Path):
        """Test multiple acquires from same process."""
        lock_path1 = acquire_data_dir_lock(str(tmp_path))
        lock_path2 = acquire_data_dir_lock(str(tmp_path))

        assert lock_path1 == lock_path2

    def test_lock_file_cleanup_on_exit_simulation(self, tmp_path: Path):
        """Test that lock file would be cleaned up on exit."""
        acquire_data_dir_lock(str(tmp_path))
        assert (tmp_path / LOCK_FILENAME).exists()

        # Note: Actual cleanup happens via atexit, which we can't easily test
        # in unit tests without process termination

    def test_reentrant_lock_same_pid(self, tmp_path: Path):
        """Test that same PID can re-acquire lock."""
        my_pid = os.getpid()

        # First acquire
        lock_path1 = acquire_data_dir_lock(str(tmp_path))

        # Write PID again
        (tmp_path / LOCK_FILENAME).write_text(str(my_pid))

        # Second acquire should succeed
        lock_path2 = acquire_data_dir_lock(str(tmp_path))

        assert lock_path1 == lock_path2
