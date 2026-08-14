# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Contract tests for the AGFS test double.

These tests keep tests.utils.mock_agfs.MockLocalAGFS in sync with the
production AGFS interface the rest of the codebase dispatches through
AsyncAGFSClient. If the production interface grows or changes, these tests
fail until the test double catches up.
"""

import inspect
import re
from pathlib import Path

import pytest

from openviking.pyagfs import AsyncAGFSClient
from openviking.pyagfs.protocols import AGFSSyncClientProtocol
from tests.utils.mock_agfs import MockLocalAGFS


def _async_client_run_method_names() -> set[str]:
    """Collect every sync client method name AsyncAGFSClient dispatches via run()."""
    source = inspect.getsource(AsyncAGFSClient)
    return set(re.findall(r'self\.run\(\s*"([a-z_]+)"', source))


def _protocol_method_names() -> set[str]:
    """Collect every method name required by AGFSSyncClientProtocol."""
    names = set()
    for name, member in inspect.getmembers(AGFSSyncClientProtocol):
        if name.startswith("_"):
            continue
        if inspect.isfunction(member):
            names.add(name)
    return names


def _make_mock(tmp_path: Path) -> MockLocalAGFS:
    return MockLocalAGFS(root_path=tmp_path / "mock_agfs_root")


class TestMockAgfsSatisfiesProductionProtocol:
    """The test double must implement the documented sync client protocol."""

    def test_mock_is_agfs_sync_client_protocol(self, tmp_path):
        mock = _make_mock(tmp_path)
        assert isinstance(mock, AGFSSyncClientProtocol)

    def test_mock_implements_every_protocol_method(self, tmp_path):
        mock = _make_mock(tmp_path)
        missing = sorted(_protocol_method_names() - set(dir(mock)))
        assert not missing, f"MockLocalAGFS missing protocol methods: {missing}"

    def test_mock_implements_every_async_dispatch_method(self, tmp_path):
        mock = _make_mock(tmp_path)
        missing = sorted(_async_client_run_method_names() - set(dir(mock)))
        assert not missing, f"MockLocalAGFS missing dispatch methods: {missing}"


class TestMockAgfsPathlockContract:
    """Pathlock behavior must match the semantics production code relies on."""

    def test_acquire_returns_owned_lease(self, tmp_path):
        mock = _make_mock(tmp_path)
        lease = mock.pathlock_acquire_exact(None, "/local/test/a.txt")
        assert lease["owned"] is True
        assert lease["lease_ref"]
        assert lease["ownership_ref"]
        assert lease["owner_id"]

    def test_release_frees_lock(self, tmp_path):
        mock = _make_mock(tmp_path)
        lease = mock.pathlock_acquire_exact(None, "/local/test/a.txt")
        mock.pathlock_release(None, lease)
        assert mock.pathlock_is_locked(None, "/local/test/a.txt") is False

    def test_acquired_lock_is_observed(self, tmp_path):
        mock = _make_mock(tmp_path)
        mock.pathlock_acquire_exact(None, "/local/test/a.txt")
        assert mock.pathlock_is_locked(None, "/local/test/a.txt") is True
        snapshot = mock.pathlock_observe(None)
        assert snapshot["active_locks"] >= 1

    def test_acquire_batch_acquires_all(self, tmp_path):
        mock = _make_mock(tmp_path)
        lease = mock.pathlock_acquire_exact_batch(
            None,
            ["/local/test/a.txt", "/local/test/b.txt"],
        )
        assert lease["owned"] is True
        assert mock.pathlock_is_locked(None, "/local/test/a.txt") is True
        assert mock.pathlock_is_locked(None, "/local/test/b.txt") is True
        mock.pathlock_release(None, lease)
        assert mock.pathlock_is_locked(None, "/local/test/a.txt") is False
        assert mock.pathlock_is_locked(None, "/local/test/b.txt") is False

    def test_borrowed_lease_cannot_release(self, tmp_path):
        mock = _make_mock(tmp_path)
        owned = mock.pathlock_acquire_exact(None, "/local/test/a.txt")
        borrowed = mock.pathlock_as_borrowed(None, owned)
        assert borrowed["owned"] is False
        with pytest.raises(ValueError):
            mock.pathlock_release(None, borrowed)
        with pytest.raises((TypeError, ValueError)):
            mock.pathlock_release(None, borrowed["lease_ref"])

    def test_handoff_and_adopt_roundtrip(self, tmp_path):
        mock = _make_mock(tmp_path)
        owned = mock.pathlock_acquire_exact(None, "/local/test/a.txt")
        handoff = mock.pathlock_to_handoff(None, owned)
        assert handoff["lock_paths"]
        mock.pathlock_handoff(None, owned)
        adopted = mock.pathlock_adopt(None, handoff)
        assert adopted["owned"] is True
        mock.pathlock_release(None, adopted)

    def test_release_selected_keeps_others(self, tmp_path):
        mock = _make_mock(tmp_path)
        lease = mock.pathlock_acquire_exact_batch(
            None,
            ["/local/test/a.txt", "/local/test/b.txt"],
        )
        mock.pathlock_release_selected(None, lease, ["/local/test/a.txt"])
        assert mock.pathlock_is_locked(None, "/local/test/a.txt") is False
        assert mock.pathlock_is_locked(None, "/local/test/b.txt") is True
        mock.pathlock_release(None, lease)

    def test_acquire_batch_rejects_invalid_requests(self, tmp_path):
        mock = _make_mock(tmp_path)
        with pytest.raises(ValueError):
            mock.pathlock_acquire_batch(None, [])
        with pytest.raises(ValueError):
            mock.pathlock_acquire_batch(None, [{"kind": "exact"}])
        with pytest.raises(ValueError):
            mock.pathlock_acquire_batch(None, [{"path": "relative", "kind": "tree"}])
        with pytest.raises(ValueError):
            mock.pathlock_acquire_batch(None, [{"path": "/local/test/a", "kind": "other"}])

    def test_refresh_reports_state(self, tmp_path):
        mock = _make_mock(tmp_path)
        owned = mock.pathlock_acquire_exact(None, "/local/test/a.txt")
        assert mock.pathlock_refresh(None, owned) == "refreshed"
        mock.pathlock_release(None, owned)
        assert mock.pathlock_refresh(None, owned) == "lost"


class TestMockAgfsFileContract:
    """Filesystem methods must follow the production return shapes."""

    def test_mkdir_returns_dict(self, tmp_path):
        mock = _make_mock(tmp_path)
        assert isinstance(mock.mkdir("/local/test/dir"), dict)

    def test_ensure_parent_dirs_creates_parents(self, tmp_path):
        mock = _make_mock(tmp_path)
        mock.ensure_parent_dirs("/local/test/a/b/c.txt")
        assert mock.exists("/local/test/a/b")

    def test_write_read_roundtrip(self, tmp_path):
        mock = _make_mock(tmp_path)
        mock.write("/local/test/a.txt", b"hello")
        assert mock.read("/local/test/a.txt") == b"hello"
        assert mock.cat("/local/test/a.txt") == b"hello"

    def test_rm_force_and_recursive(self, tmp_path):
        mock = _make_mock(tmp_path)
        mock.write("/local/test/file.txt", b"x")
        assert isinstance(mock.rm("/local/test/file.txt"), dict)
        assert (
            mock.rm("/local/test/missing.txt", force=True)["removed"] == "/local/test/missing.txt"
        )
        with pytest.raises(FileNotFoundError):
            mock.rm("/local/test/missing.txt", force=False)

        mock.mkdir("/local/test/dir")
        mock.write("/local/test/dir/f.txt", b"x")
        mock.rm("/local/test/dir", recursive=True)
        assert not mock.exists("/local/test/dir")

    def test_copy_within_mount(self, tmp_path):
        mock = _make_mock(tmp_path)
        mock.write("/local/test/a.txt", b"copy me")
        result = mock.copy_within_mount("/local/test/a.txt", "/local/test/b.txt")
        assert result["performed"] is True
        assert mock.read("/local/test/b.txt") == b"copy me"

    def test_tree_directory_shape(self, tmp_path):
        mock = _make_mock(tmp_path)
        mock.write("/local/test/dir/nested/file.md", b"# t")
        entries = mock.tree_directory("/local/test/dir", level_limit=3)
        assert any(e["rel_path"] == "nested/file.md" for e in entries)
        for entry in entries:
            assert "path" in entry
            assert "info" in entry
            assert entry["info"]["name"]

    def test_grep_shape(self, tmp_path):
        mock = _make_mock(tmp_path)
        mock.write("/local/test/dir/a.md", b"needle here\nplain line\n")
        result = mock.grep(path="/local/test/dir", pattern="needle")
        assert result["count"] == 1
        assert result["matches"][0]["content"] == "needle here"
        assert "files_scanned" in result

    def test_system_sync_returns_dicts(self, tmp_path):
        mock = _make_mock(tmp_path)
        assert isinstance(mock.system_sync_status("/local/test/a.txt"), dict)
        assert isinstance(mock.system_sync_retry("/local/test/a.txt"), dict)


class TestMockAgfsQueueContract:
    """QueueFS virtual-path semantics must keep NamedQueue working."""

    def test_enqueue_dequeue_roundtrip(self, tmp_path):
        mock = _make_mock(tmp_path)
        queue_path = "/queue/TestQueue"
        msg_id = mock.writeto(f"{queue_path}/enqueue", b'{"id": "m1", "data": "hello"}')
        assert msg_id
        raw = mock.read_file(f"{queue_path}/dequeue")
        import json as json_mod

        message = json_mod.loads(raw)
        assert message["id"] == "m1"
        assert message["data"] == "hello"

    def test_queue_size_and_messages(self, tmp_path):
        mock = _make_mock(tmp_path)
        queue_path = "/queue/TestQueue"
        mock.writeto(f"{queue_path}/enqueue", b'{"id": "m1"}')
        mock.writeto(f"{queue_path}/enqueue", b'{"id": "m2"}')
        assert int(mock.read_file(f"{queue_path}/size")) == 2
        snapshot = mock.read_file(f"{queue_path}/messages")
        import json as json_mod

        messages = json_mod.loads(snapshot)
        assert {m["id"] for m in messages} == {"m1", "m2"}

    def test_ack_removes_processing_message(self, tmp_path):
        mock = _make_mock(tmp_path)
        queue_path = "/queue/TestQueue"
        mock.writeto(f"{queue_path}/enqueue", b'{"id": "m1"}')
        mock.read_file(f"{queue_path}/dequeue")
        mock.writeto(f"{queue_path}/ack", b"m1")
        snapshot = mock.read_file(f"{queue_path}/messages")
        import json as json_mod

        messages = json_mod.loads(snapshot)
        assert messages == []

    def test_clear_empties_queue(self, tmp_path):
        mock = _make_mock(tmp_path)
        queue_path = "/queue/TestQueue"
        mock.writeto(f"{queue_path}/enqueue", b'{"id": "m1"}')
        mock.writeto(f"{queue_path}/clear", b"")
        assert int(mock.read_file(f"{queue_path}/size")) == 0
