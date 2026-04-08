"""Tests for stale RocksDB LOCK file cleanup."""

import os
from pathlib import Path

import openviking.storage.vectordb.utils.stale_lock as stale_lock_module


class TestStaleLockCleanup:
    """Tests for clean_stale_rocksdb_locks()."""

    def _create_lock_file(self, base_dir: Path, *path_parts: str) -> Path:
        """Helper to create a LOCK file at the given path under base_dir."""
        lock_dir = base_dir.joinpath(*path_parts[:-1])
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / path_parts[-1]
        lock_path.write_text("")
        return lock_path

    def _set_runtime(
        self,
        monkeypatch,
        *,
        platform: str,
        dockerenv: bool = False,
        containerenv: bool = False,
    ) -> None:
        """Patch runtime detection for platform/container-specific tests."""
        original_exists = os.path.exists

        def _fake_exists(path: str) -> bool:
            if path == "/.dockerenv":
                return dockerenv
            if path == "/run/.containerenv":
                return containerenv
            return original_exists(path)

        monkeypatch.setattr(stale_lock_module.sys, "platform", platform)
        monkeypatch.setattr(stale_lock_module.os.path, "exists", _fake_exists)

    def test_removes_stale_lock_in_standard_layout_on_windows(self, tmp_path: Path, monkeypatch):
        """Stale LOCK at vectordb/<collection>/store/LOCK is removed."""
        lock_path = self._create_lock_file(tmp_path, "vectordb", "context", "store", "LOCK")
        self._set_runtime(monkeypatch, platform="win32")

        removed = stale_lock_module.clean_stale_rocksdb_locks(str(tmp_path))

        assert removed == 1
        assert not lock_path.exists()

    def test_removes_multiple_collection_locks_on_windows(self, tmp_path: Path, monkeypatch):
        """Handles multiple collections with stale LOCKs."""
        lock1 = self._create_lock_file(tmp_path, "vectordb", "context", "store", "LOCK")
        lock2 = self._create_lock_file(tmp_path, "vectordb", "memories", "store", "LOCK")
        self._set_runtime(monkeypatch, platform="win32")

        removed = stale_lock_module.clean_stale_rocksdb_locks(str(tmp_path))

        assert removed == 2
        assert not lock1.exists()
        assert not lock2.exists()

    def test_no_error_on_empty_directory(self, tmp_path: Path, monkeypatch):
        """No crash when data_dir has no LOCK files."""
        self._set_runtime(monkeypatch, platform="win32")

        removed = stale_lock_module.clean_stale_rocksdb_locks(str(tmp_path))

        assert removed == 0

    def test_no_error_on_nonexistent_directory(self, monkeypatch):
        """No crash when data_dir does not exist."""
        self._set_runtime(monkeypatch, platform="win32")

        removed = stale_lock_module.clean_stale_rocksdb_locks("/tmp/does_not_exist_ov_test")

        assert removed == 0

    def test_noop_on_posix_without_container_marker(self, tmp_path: Path, monkeypatch):
        """POSIX without container markers should remain a no-op."""
        lock_path = self._create_lock_file(tmp_path, "vectordb", "context", "store", "LOCK")
        self._set_runtime(monkeypatch, platform="linux")

        removed = stale_lock_module.clean_stale_rocksdb_locks(str(tmp_path))

        assert removed == 0
        assert lock_path.exists()

    def test_container_dockerenv_enables_cleanup(self, tmp_path: Path, monkeypatch):
        """Containerized Linux should reclaim stale LOCKs when /.dockerenv exists."""
        lock_path = self._create_lock_file(tmp_path, "vectordb", "context", "store", "LOCK")
        self._set_runtime(monkeypatch, platform="linux", dockerenv=True)

        removed = stale_lock_module.clean_stale_rocksdb_locks(str(tmp_path))

        assert removed == 1
        assert not lock_path.exists()

    def test_containerenv_marker_enables_cleanup(self, tmp_path: Path, monkeypatch):
        """Containerized Linux should reclaim stale LOCKs when /run/.containerenv exists."""
        lock_path = self._create_lock_file(tmp_path, "vectordb", "context", "store", "LOCK")
        self._set_runtime(monkeypatch, platform="linux", containerenv=True)

        removed = stale_lock_module.clean_stale_rocksdb_locks(str(tmp_path))

        assert removed == 1
        assert not lock_path.exists()

    def test_container_cleanup_skips_when_probe_cannot_reclaim(self, tmp_path: Path, monkeypatch):
        """Container cleanup should skip LOCK files that fail the POSIX probe."""
        lock_path = self._create_lock_file(tmp_path, "vectordb", "context", "store", "LOCK")
        self._set_runtime(monkeypatch, platform="linux", dockerenv=True)
        monkeypatch.setattr(stale_lock_module, "_can_reclaim_posix_lock", lambda _path: False)

        removed = stale_lock_module.clean_stale_rocksdb_locks(str(tmp_path))

        assert removed == 0
        assert lock_path.exists()

    def test_permission_error_skips_live_lock_on_windows(self, tmp_path: Path, monkeypatch):
        """PermissionError should be treated as a live Windows LOCK."""
        lock_path = self._create_lock_file(tmp_path, "vectordb", "context", "store", "LOCK")
        self._set_runtime(monkeypatch, platform="win32")

        def _raise_permission_error(path: str) -> None:
            if path == str(lock_path):
                raise PermissionError("file is in use")
            raise AssertionError(f"Unexpected remove path: {path}")

        monkeypatch.setattr(stale_lock_module.os, "remove", _raise_permission_error)

        removed = stale_lock_module.clean_stale_rocksdb_locks(str(tmp_path))

        assert removed == 0
        assert lock_path.exists()

    def test_deduplicates_overlapping_patterns(self, tmp_path: Path, monkeypatch):
        """Same LOCK file matched by multiple glob patterns is only counted once."""
        self._create_lock_file(tmp_path, "vectordb", "context", "store", "LOCK")
        self._set_runtime(monkeypatch, platform="win32")

        removed = stale_lock_module.clean_stale_rocksdb_locks(str(tmp_path))

        assert removed == 1
