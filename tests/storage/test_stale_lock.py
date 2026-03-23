"""Tests for stale RocksDB LOCK file cleanup."""

import os
import sys
import tempfile

import pytest

from openviking.storage.vectordb.utils.stale_lock import clean_stale_rocksdb_locks


class TestStaleLockCleanup:
    """Tests for clean_stale_rocksdb_locks()."""

    def _create_lock_file(self, base_dir: str, *path_parts: str) -> str:
        """Helper to create a LOCK file at the given path under base_dir."""
        lock_dir = os.path.join(base_dir, *path_parts[:-1])
        os.makedirs(lock_dir, exist_ok=True)
        lock_path = os.path.join(lock_dir, path_parts[-1])
        with open(lock_path, "w") as f:
            f.write("")
        return lock_path

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific behavior")
    def test_removes_stale_lock_in_standard_layout(self):
        """Stale LOCK at vectordb/<collection>/store/LOCK is removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = self._create_lock_file(
                tmpdir, "vectordb", "context", "store", "LOCK"
            )
            assert os.path.exists(lock_path)

            removed = clean_stale_rocksdb_locks(tmpdir)

            assert removed == 1
            assert not os.path.exists(lock_path)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific behavior")
    def test_removes_multiple_collection_locks(self):
        """Handles multiple collections with stale LOCKs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lock1 = self._create_lock_file(
                tmpdir, "vectordb", "context", "store", "LOCK"
            )
            lock2 = self._create_lock_file(
                tmpdir, "vectordb", "memories", "store", "LOCK"
            )

            removed = clean_stale_rocksdb_locks(tmpdir)

            assert removed == 2
            assert not os.path.exists(lock1)
            assert not os.path.exists(lock2)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific behavior")
    def test_no_error_on_empty_directory(self):
        """No crash when data_dir has no LOCK files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            removed = clean_stale_rocksdb_locks(tmpdir)
            assert removed == 0

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific behavior")
    def test_no_error_on_nonexistent_directory(self):
        """No crash when data_dir does not exist."""
        removed = clean_stale_rocksdb_locks("/tmp/does_not_exist_ov_test")
        assert removed == 0

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only: no-op expected")
    def test_noop_on_posix(self):
        """On POSIX systems, the function is a no-op (flock handles cleanup)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_lock_file(
                tmpdir, "vectordb", "context", "store", "LOCK"
            )
            removed = clean_stale_rocksdb_locks(tmpdir)
            assert removed == 0

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific behavior")
    def test_deduplicates_overlapping_patterns(self):
        """Same LOCK file matched by multiple glob patterns is only counted once."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # This LOCK matches both **/store/LOCK and **/LOCK patterns
            self._create_lock_file(
                tmpdir, "vectordb", "context", "store", "LOCK"
            )
            removed = clean_stale_rocksdb_locks(tmpdir)
            assert removed == 1
