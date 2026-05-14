# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for path lock with fencing tokens."""

import time
from unittest.mock import AsyncMock, MagicMock

from openviking.storage.transaction.lock_handle import LockHandle
from openviking.storage.transaction.path_lock import (
    EXACT_LOCK_FILE_PREFIX,
    LOCK_FILE_NAME,
    LOCK_TYPE_EXACT,
    LOCK_TYPE_TREE,
    PathLockEngine,
    _make_fencing_token,
    _parse_fencing_token,
)


def _single_lock_path(handle: LockHandle) -> str:
    assert len(handle.locks) == 1
    return handle.locks[0]


class TestFencingToken:
    def test_make_parse_roundtrip(self):
        token = _make_fencing_token("tx-123")
        tx_id, ts, lock_type = _parse_fencing_token(token)
        assert tx_id == "tx-123"
        assert ts > 0
        assert lock_type == LOCK_TYPE_EXACT

    def test_make_parse_tree_roundtrip(self):
        token = _make_fencing_token("tx-456", LOCK_TYPE_TREE)
        tx_id, ts, lock_type = _parse_fencing_token(token)
        assert tx_id == "tx-456"
        assert ts > 0
        assert lock_type == LOCK_TYPE_TREE

    def test_parse_previous_tree_tokens(self):
        for previous_type in ("P", "S"):
            tx_id, ts, lock_type = _parse_fencing_token(f"tx-old:123:{previous_type}")
            assert tx_id == "tx-old"
            assert ts == 123
            assert lock_type == LOCK_TYPE_TREE

    def test_parse_untyped_token_defaults_to_stale_exact(self):
        """Untyped tokens are treated as stale EXACT locks."""
        tx_id, ts, lock_type = _parse_fencing_token("tx-old:1234567890")
        assert tx_id == "tx-old:1234567890"
        assert ts == 0
        assert lock_type == LOCK_TYPE_EXACT

    def test_parse_plain_token_defaults_to_exact(self):
        """Plain tx_id (no colon) defaults to ts=0, lock_type=EXACT."""
        tx_id, ts, lock_type = _parse_fencing_token("tx-bare")
        assert tx_id == "tx-bare"
        assert ts == 0
        assert lock_type == LOCK_TYPE_EXACT

    def test_tokens_are_unique(self):
        t1 = _make_fencing_token("tx-1")
        time.sleep(0.001)
        t2 = _make_fencing_token("tx-1")
        assert t1 != t2


class TestPathLockStale:
    def test_is_lock_stale_no_file(self):
        agfs = MagicMock()
        agfs.read.side_effect = Exception("not found")
        lock = PathLockEngine(agfs)
        assert lock.is_lock_stale("/test/.path.ovlock") is True

    def test_is_lock_stale_for_plain_token(self):
        agfs = MagicMock()
        agfs.read.return_value = b"tx-old-format"
        lock = PathLockEngine(agfs)
        assert lock.is_lock_stale("/test/.path.ovlock") is True

    def test_is_lock_stale_recent_token(self):
        agfs = MagicMock()
        token = _make_fencing_token("tx-1")
        agfs.read.return_value = token.encode("utf-8")
        lock = PathLockEngine(agfs)
        assert lock.is_lock_stale("/test/.path.ovlock", expire_seconds=300.0) is False


class TestPathLockIsLocked:
    def _agfs_with_locks(self, locks: dict[str, bytes]) -> MagicMock:
        """Build an AGFS mock that only returns content for paths in *locks*."""
        agfs = MagicMock()

        def _read(p):
            if p in locks:
                return locks[p]
            raise Exception("not found")

        agfs.read.side_effect = _read
        return agfs

    def test_is_locked_no_lock(self):
        agfs = self._agfs_with_locks({})
        lock = PathLockEngine(agfs)
        assert lock.is_locked("/local/u/foo") is False

    def test_is_locked_self_exact_lock(self):
        token = _make_fencing_token("tx-1", LOCK_TYPE_EXACT)
        agfs = self._agfs_with_locks({"/local/u/foo/.path.ovlock": token.encode("utf-8")})
        lock = PathLockEngine(agfs)
        assert lock.is_locked("/local/u/foo") is True

    def test_is_locked_ancestor_tree_lock(self):
        token = _make_fencing_token("tx-1", LOCK_TYPE_TREE)
        agfs = self._agfs_with_locks({"/local/u/.path.ovlock": token.encode("utf-8")})
        lock = PathLockEngine(agfs)
        assert lock.is_locked("/local/u/foo/bar") is True

    def test_is_locked_ancestor_exact_lock_does_not_propagate(self):
        """An ancestor EXACT lock must not affect descendants -- EXACT locks only the path itself."""
        token = _make_fencing_token("tx-1", LOCK_TYPE_EXACT)
        agfs = self._agfs_with_locks({"/local/u/.path.ovlock": token.encode("utf-8")})
        lock = PathLockEngine(agfs)
        assert lock.is_locked("/local/u/foo/bar") is False

    def test_is_locked_ignores_stale_by_default(self):
        old_ts = time.time_ns() - int(600 * 1e9)  # 600s ago
        stale = f"tx-dead:{old_ts}:{LOCK_TYPE_EXACT}".encode("utf-8")
        agfs = self._agfs_with_locks({"/local/u/foo/.path.ovlock": stale})
        lock = PathLockEngine(agfs, lock_expire=300.0)
        assert lock.is_locked("/local/u/foo") is False

    def test_is_locked_can_include_stale(self):
        old_ts = time.time_ns() - int(600 * 1e9)
        stale = f"tx-dead:{old_ts}:{LOCK_TYPE_EXACT}".encode("utf-8")
        agfs = self._agfs_with_locks({"/local/u/foo/.path.ovlock": stale})
        lock = PathLockEngine(agfs, lock_expire=300.0)
        assert lock.is_locked("/local/u/foo", ignore_stale=False) is True


class TestPathLockOwnership:
    async def test_refresh_reports_refreshed_lost_and_failed_paths(self):
        owned_path = "/locks/owned/.path.ovlock"
        lost_path = "/locks/lost/.path.ovlock"
        missing_path = "/locks/missing/.path.ovlock"
        failed_path = "/locks/failed/.path.ovlock"

        tokens = {
            owned_path: _make_fencing_token("tx-1", LOCK_TYPE_EXACT),
            lost_path: _make_fencing_token("tx-2", LOCK_TYPE_TREE),
            failed_path: _make_fencing_token("tx-1", LOCK_TYPE_TREE),
        }
        agfs = MagicMock()

        def read_side_effect(lock_path):
            if lock_path == missing_path:
                raise FileNotFoundError(lock_path)
            return tokens[lock_path].encode("utf-8")

        def write_side_effect(lock_path, content):
            if lock_path == failed_path:
                raise OSError("write failed")
            tokens[lock_path] = content.decode("utf-8")

        agfs.read.side_effect = read_side_effect
        agfs.write.side_effect = write_side_effect

        lock = PathLockEngine(agfs)
        tx = LockHandle(id="tx-1")
        for lock_path in [owned_path, lost_path, missing_path, failed_path]:
            tx.add_lock(lock_path)

        result = await lock.refresh(tx)

        assert result.refreshed_paths == [owned_path]
        assert set(result.lost_paths) == {lost_path, missing_path}
        assert result.failed_paths == [failed_path]

    async def test_release_skips_locks_no_longer_owned(self):
        owned_path = "/locks/owned/.path.ovlock"
        replaced_path = "/locks/replaced/.path.ovlock"

        tokens = {
            owned_path: _make_fencing_token("tx-1", LOCK_TYPE_EXACT),
            replaced_path: _make_fencing_token("tx-2", LOCK_TYPE_EXACT),
        }
        agfs = MagicMock()
        agfs.read.side_effect = lambda lock_path: tokens[lock_path].encode("utf-8")

        lock = PathLockEngine(agfs)
        lock._remove_lock_file = AsyncMock(return_value=True)
        tx = LockHandle(id="tx-1")
        tx.add_lock(owned_path)
        tx.add_lock(replaced_path)

        await lock.release(tx)

        lock._remove_lock_file.assert_awaited_once_with(owned_path)
        assert tx.locks == []


class TestPathLockBehavior:
    """Behavioral tests using real AGFS backend."""

    async def test_acquire_exact_path_creates_lock_file(self, agfs_client, test_dir):
        lock = PathLockEngine(agfs_client)
        tx = LockHandle(id="tx-exact-1")

        ok = await lock.acquire_exact_path(test_dir, tx, timeout=3.0)
        assert ok is True

        lock_path = _single_lock_path(tx)
        assert lock_path == f"{test_dir}/{LOCK_FILE_NAME}"
        content = agfs_client.cat(lock_path)
        token = content.decode("utf-8") if isinstance(content, bytes) else content
        assert ":E" in token
        assert "tx-exact-1" in token

        await lock.release(tx)

    async def test_acquire_tree_creates_lock_file(self, agfs_client, test_dir):
        lock = PathLockEngine(agfs_client)
        tx = LockHandle(id="tx-tree-1")

        ok = await lock.acquire_tree(test_dir, tx, timeout=3.0)
        assert ok is True

        lock_path = f"{test_dir}/{LOCK_FILE_NAME}"
        content = agfs_client.cat(lock_path)
        token = content.decode("utf-8") if isinstance(content, bytes) else content
        assert ":T" in token
        assert "tx-tree-1" in token

        await lock.release(tx)

    async def test_acquire_tree_creates_missing_directory_after_conflict_check(
        self, agfs_client, test_dir
    ):
        lock = PathLockEngine(agfs_client)
        tx = LockHandle(id="tx-tree-missing")
        target = f"{test_dir}/new-resource"

        ok = await lock.acquire_tree(target, tx, timeout=3.0)
        assert ok is True
        assert _single_lock_path(tx) == f"{target}/{LOCK_FILE_NAME}"
        assert agfs_client.stat(target).get("isDir") is True

        await lock.release(tx)

    async def test_tree_blocked_by_ancestor_tree_does_not_create_missing_directory(
        self, agfs_client, test_dir
    ):
        lock = PathLockEngine(agfs_client)
        parent = LockHandle(id="tx-parent-tree")
        child = LockHandle(id="tx-child-tree")
        target = f"{test_dir}/blocked-resource"

        assert await lock.acquire_tree(test_dir, parent, timeout=3.0) is True
        assert await lock.acquire_tree(target, child, timeout=0.0) is False

        try:
            agfs_client.stat(target)
            raise AssertionError("missing TreeLock target should not be created on conflict")
        except AssertionError:
            raise
        except Exception:
            pass

        await lock.release(parent)

    async def test_acquire_exact_path_allows_missing_target(self, agfs_client):
        lock = PathLockEngine(agfs_client)
        tx = LockHandle(id="tx-no-dir")

        ok = await lock.acquire_exact_path("/local/nonexistent-path-xyz", tx, timeout=0.5)
        assert ok is True
        assert len(tx.locks) == 1
        assert tx.locks[0].rsplit("/", 1)[-1].startswith(EXACT_LOCK_FILE_PREFIX)

        await lock.release(tx)

    async def test_exact_blocked_by_ancestor_tree_does_not_create_missing_parent(
        self, agfs_client, test_dir
    ):
        lock = PathLockEngine(agfs_client)
        parent = LockHandle(id="tx-parent-tree")
        child = LockHandle(id="tx-child-exact")
        missing_parent = f"{test_dir}/blocked-dir"
        target = f"{missing_parent}/file.md"

        assert await lock.acquire_tree(test_dir, parent, timeout=3.0) is True
        assert await lock.acquire_exact_path(target, child, timeout=0.0) is False

        try:
            agfs_client.stat(missing_parent)
            raise AssertionError("missing exact-lock parent should not be created on conflict")
        except AssertionError:
            raise
        except Exception:
            pass

        await lock.release(parent)

    async def test_release_removes_lock_file(self, agfs_client, test_dir):
        lock = PathLockEngine(agfs_client)
        tx = LockHandle(id="tx-release-1")

        await lock.acquire_exact_path(test_dir, tx, timeout=3.0)
        lock_path = _single_lock_path(tx)

        await lock.release(tx)

        # Lock file should be gone (use stat, not cat — cat returns b'' for deleted files)
        try:
            agfs_client.stat(lock_path)
            raise AssertionError("Lock file should have been removed")
        except AssertionError:
            raise
        except Exception:
            pass  # Expected: file not found

    async def test_sequential_acquire_works(self, agfs_client, test_dir):
        lock = PathLockEngine(agfs_client)

        tx1 = LockHandle(id="tx-seq-1")
        ok1 = await lock.acquire_exact_path(test_dir, tx1, timeout=3.0)
        assert ok1 is True

        await lock.release(tx1)

        tx2 = LockHandle(id="tx-seq-2")
        ok2 = await lock.acquire_exact_path(test_dir, tx2, timeout=3.0)
        assert ok2 is True

        await lock.release(tx2)

    async def test_exact_blocked_by_ancestor_tree(self, agfs_client, test_dir):
        """EXACT on child blocked while ancestor holds TreeLock."""
        import uuid as _uuid

        child = f"{test_dir}/child-{_uuid.uuid4().hex}"
        agfs_client.mkdir(child)

        lock = PathLockEngine(agfs_client)
        tx_parent = LockHandle(id="tx-parent-tree")
        ok = await lock.acquire_tree(test_dir, tx_parent, timeout=3.0)
        assert ok is True

        tx_child = LockHandle(id="tx-child-exact")
        blocked = await lock.acquire_exact_path(child, tx_child, timeout=0.5)
        assert blocked is False

        await lock.release(tx_parent)

    async def test_tree_blocked_by_descendant_exact(self, agfs_client, test_dir):
        """TreeLock on parent blocked while descendant holds ExactPathLock."""
        import uuid as _uuid

        child = f"{test_dir}/child-{_uuid.uuid4().hex}"
        agfs_client.mkdir(child)

        lock = PathLockEngine(agfs_client)
        tx_child = LockHandle(id="tx-desc-exact")
        ok = await lock.acquire_exact_path(child, tx_child, timeout=3.0)
        assert ok is True

        tx_parent = LockHandle(id="tx-parent-tree")
        blocked = await lock.acquire_tree(test_dir, tx_parent, timeout=0.5)
        assert blocked is False

        await lock.release(tx_child)

    async def test_acquire_mv_creates_tree_and_exact_locks(self, agfs_client, test_dir):
        """Directory move locks the source tree and exact destination path."""
        import uuid as _uuid

        src = f"{test_dir}/src-{_uuid.uuid4().hex}"
        dst = f"{test_dir}/dst-{_uuid.uuid4().hex}"
        agfs_client.mkdir(src)
        agfs_client.mkdir(dst)

        lock = PathLockEngine(agfs_client)
        tx = LockHandle(id="tx-mv-1")
        ok = await lock.acquire_mv(src, dst, tx, timeout=3.0)
        assert ok is True

        src_token_bytes = agfs_client.cat(f"{src}/{LOCK_FILE_NAME}")
        src_token = (
            src_token_bytes.decode("utf-8")
            if isinstance(src_token_bytes, bytes)
            else src_token_bytes
        )
        assert ":T" in src_token

        dst_token_bytes = agfs_client.cat(f"{dst}/{LOCK_FILE_NAME}")
        dst_token = (
            dst_token_bytes.decode("utf-8")
            if isinstance(dst_token_bytes, bytes)
            else dst_token_bytes
        )
        assert ":E" in dst_token

        await lock.release(tx)

    async def test_exact_does_not_block_sibling_exact(self, agfs_client, test_dir):
        """EXACT locks on different directories do not conflict."""
        import uuid as _uuid

        dir_a = f"{test_dir}/sibling-a-{_uuid.uuid4().hex}"
        dir_b = f"{test_dir}/sibling-b-{_uuid.uuid4().hex}"
        agfs_client.mkdir(dir_a)
        agfs_client.mkdir(dir_b)

        lock = PathLockEngine(agfs_client)
        tx_a = LockHandle(id="tx-sib-a")
        tx_b = LockHandle(id="tx-sib-b")

        ok_a = await lock.acquire_exact_path(dir_a, tx_a, timeout=3.0)
        ok_b = await lock.acquire_exact_path(dir_b, tx_b, timeout=3.0)

        assert ok_a is True
        assert ok_b is True

        await lock.release(tx_a)
        await lock.release(tx_b)

    async def test_stale_lock_auto_removed_on_acquire(self, agfs_client, test_dir):
        """A stale lock (expired fencing token) is auto-removed, allowing a new acquire."""
        import uuid as _uuid

        target = f"{test_dir}/stale-{_uuid.uuid4().hex}"
        agfs_client.mkdir(target)

        lock = PathLockEngine(agfs_client, lock_expire=300.0)
        lock_path = lock._get_exact_lock_path(target)

        # Write a lock file with a very old timestamp (simulate crashed process)
        old_ts = time.time_ns() - int(600 * 1e9)  # 600 seconds ago
        stale_token = f"tx-dead:{old_ts}:{LOCK_TYPE_EXACT}"
        agfs_client.write(lock_path, stale_token.encode("utf-8"))

        # New transaction should succeed by auto-removing the stale lock
        tx = LockHandle(id="tx-new-owner")
        ok = await lock.acquire_exact_path(target, tx, timeout=2.0)
        assert ok is True

        # Verify new lock is owned by our transaction
        content = agfs_client.cat(lock_path)
        token = content.decode("utf-8") if isinstance(content, bytes) else content
        assert "tx-new-owner" in token

        await lock.release(tx)

    async def test_stale_tree_ancestor_auto_removed(self, agfs_client, test_dir):
        """A stale TreeLock on ancestor is auto-removed when child acquires ExactPathLock."""
        import uuid as _uuid

        child = f"{test_dir}/child-stale-{_uuid.uuid4().hex}"
        agfs_client.mkdir(child)

        # Write stale TreeLock on parent
        parent_lock = f"{test_dir}/{LOCK_FILE_NAME}"
        old_ts = time.time_ns() - int(600 * 1e9)
        stale_token = f"tx-dead-parent:{old_ts}:{LOCK_TYPE_TREE}"
        agfs_client.write(parent_lock, stale_token.encode("utf-8"))

        lock = PathLockEngine(agfs_client, lock_expire=300.0)
        tx = LockHandle(id="tx-child-new")
        ok = await lock.acquire_exact_path(child, tx, timeout=2.0)
        assert ok is True

        await lock.release(tx)
        # Clean up stale parent lock if still present
        try:
            agfs_client.rm(parent_lock)
        except Exception:
            pass

    async def test_exact_same_path_no_wait_fails_immediately(self, agfs_client, test_dir):
        """With timeout=0, a conflicting lock fails immediately."""
        import uuid as _uuid

        target = f"{test_dir}/nowait-{_uuid.uuid4().hex}"
        agfs_client.mkdir(target)

        lock = PathLockEngine(agfs_client)
        tx1 = LockHandle(id="tx-hold")
        ok1 = await lock.acquire_exact_path(target, tx1, timeout=3.0)
        assert ok1 is True

        # Second acquire with timeout=0 should fail immediately
        tx2 = LockHandle(id="tx-blocked")
        t0 = time.monotonic()
        ok2 = await lock.acquire_exact_path(target, tx2, timeout=0.0)
        elapsed = time.monotonic() - t0

        assert ok2 is False
        assert elapsed < 1.0  # Should not wait

        await lock.release(tx1)

    async def test_tree_same_path_mutual_exclusion(self, agfs_client, test_dir):
        """Two TreeLocks on the same path: second one blocked until first releases."""
        import uuid as _uuid

        target = f"{test_dir}/tree-excl-{_uuid.uuid4().hex}"
        agfs_client.mkdir(target)

        lock = PathLockEngine(agfs_client)
        tx1 = LockHandle(id="tx-tree1")
        ok1 = await lock.acquire_tree(target, tx1, timeout=3.0)
        assert ok1 is True

        tx2 = LockHandle(id="tx-tree2")
        ok2 = await lock.acquire_tree(target, tx2, timeout=0.5)
        assert ok2 is False

        await lock.release(tx1)

        # Now tx2 should succeed
        ok2_retry = await lock.acquire_tree(target, tx2, timeout=3.0)
        assert ok2_retry is True
        await lock.release(tx2)

    async def test_exact_reuses_same_owner_tree_lock_on_same_path(self, agfs_client, test_dir):
        lock = PathLockEngine(agfs_client)
        tx = LockHandle(id="tx-reentrant-same-path")

        ok = await lock.acquire_tree(test_dir, tx, timeout=3.0)
        assert ok is True

        lock_path = f"{test_dir}/{LOCK_FILE_NAME}"
        before = agfs_client.cat(lock_path)
        before_token = before.decode("utf-8") if isinstance(before, bytes) else before
        assert ":T" in before_token

        ok_reuse = await lock.acquire_exact_path(test_dir, tx, timeout=0.5)
        assert ok_reuse is True

        after = agfs_client.cat(lock_path)
        after_token = after.decode("utf-8") if isinstance(after, bytes) else after
        assert after_token == before_token
        assert ":T" in after_token

        await lock.release(tx)

    async def test_exact_under_same_owner_tree_does_not_create_child_lock(
        self, agfs_client, test_dir
    ):
        import uuid as _uuid

        child = f"{test_dir}/child-reentrant-{_uuid.uuid4().hex}"
        agfs_client.mkdir(child)

        lock = PathLockEngine(agfs_client)
        tx = LockHandle(id="tx-reentrant-child")

        ok = await lock.acquire_tree(test_dir, tx, timeout=3.0)
        assert ok is True

        ok_child = await lock.acquire_exact_path(child, tx, timeout=0.5)
        assert ok_child is True

        child_lock_path = lock._get_exact_lock_path(child)
        try:
            agfs_client.stat(child_lock_path)
            raise AssertionError("child lock should not be created when ancestor tree is owned")
        except AssertionError:
            raise
        except Exception:
            pass

        await lock.release(tx)
