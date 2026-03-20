# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for stale RocksDB LOCK detection.

Verifies the fix for https://github.com/volcengine/OpenViking/issues/650:
On Windows, crashed or exited processes leave behind stale RocksDB LOCK
files that block subsequent sessions.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from openviking.storage.vectordb.store.local_store import _clear_stale_rocksdb_locks


@pytest.fixture
def fake_vectordb(tmp_path):
    """Create a fake vectordb directory tree with LOCK files."""
    store_dir = tmp_path / "vectordb" / "default" / "store"
    store_dir.mkdir(parents=True)
    lock_file = store_dir / "LOCK"
    lock_file.touch()

    store_dir2 = tmp_path / "vectordb" / "account2" / "store"
    store_dir2.mkdir(parents=True)
    lock_file2 = store_dir2 / "LOCK"
    lock_file2.touch()

    return tmp_path


@patch("sys.platform", "win32")
def test_removes_stale_locks_on_windows(fake_vectordb):
    """Stale LOCK files should be removed on Windows."""
    lock1 = fake_vectordb / "vectordb" / "default" / "store" / "LOCK"
    lock2 = fake_vectordb / "vectordb" / "account2" / "store" / "LOCK"

    assert lock1.exists()
    assert lock2.exists()

    _clear_stale_rocksdb_locks(str(fake_vectordb))

    assert not lock1.exists(), "Stale LOCK should be removed"
    assert not lock2.exists(), "Stale LOCK should be removed"


@patch("sys.platform", "darwin")
def test_skips_on_non_windows(fake_vectordb):
    """LOCK cleanup should be skipped on non-Windows platforms."""
    lock1 = fake_vectordb / "vectordb" / "default" / "store" / "LOCK"
    assert lock1.exists()

    _clear_stale_rocksdb_locks(str(fake_vectordb))

    assert lock1.exists(), "LOCK should not be removed on non-Windows"


@patch("sys.platform", "win32")
def test_handles_permission_error_gracefully(fake_vectordb):
    """When a live process holds the LOCK, PermissionError should be caught."""
    lock1 = fake_vectordb / "vectordb" / "default" / "store" / "LOCK"

    with patch("os.remove", side_effect=PermissionError("locked by live process")):
        # Should not raise
        _clear_stale_rocksdb_locks(str(fake_vectordb))

    assert lock1.exists(), "LOCK held by live process should be kept"


@patch("sys.platform", "win32")
def test_handles_empty_directory(tmp_path):
    """No LOCK files should result in a no-op."""
    # Should not raise
    _clear_stale_rocksdb_locks(str(tmp_path))


@patch("sys.platform", "win32")
def test_handles_nonexistent_path():
    """Non-existent path should be handled gracefully."""
    _clear_stale_rocksdb_locks("/nonexistent/path/that/does/not/exist")
