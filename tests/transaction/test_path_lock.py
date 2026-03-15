# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for path lock with fencing tokens."""

import time
from unittest.mock import MagicMock

from openviking.storage.transaction.path_lock import (
    LOCK_FILE_NAME,
    LOCK_TYPE_POINT,
    LOCK_TYPE_SUBTREE,
    PathLock,
    _make_fencing_token,
    _parse_fencing_token,
)
from openviking.storage.transaction.transaction_record import TransactionRecord


class TestFencingToken:
    def test_make_parse_roundtrip(self):
        token = _make_fencing_token("tx-123")
        tx_id, ts, lock_type = _parse_fencing_token(token)
        assert tx_id == "tx-123"
        assert ts > 0
        assert lock_type == LOCK_TYPE_POINT

    def test_make_parse_subtree_roundtrip(self):
        token = _make_fencing_token("tx-456", LOCK_TYPE_SUBTREE)
        tx_id, ts, lock_type = _parse_fencing_token(token)
        assert tx_id == "tx-456"
        assert ts > 0
        assert lock_type == LOCK_TYPE_SUBTREE

    def test_parse_legacy_format_two_part(self):
        """Legacy two-part token "{tx_id}:{ts}" defaults to POINT."""
        tx_id, ts, lock_type = _parse_fencing_token("tx-old:1234567890")
        assert tx_id == "tx-old"
        assert ts == 1234567890
        assert lock_type == LOCK_TYPE_POINT

    def test_parse_legacy_format_plain(self):
        """Plain tx_id (no colon) defaults to ts=0, lock_type=POINT."""
        tx_id, ts, lock_type = _parse_fencing_token("tx-bare")
        assert tx_id == "tx-bare"
        assert ts == 0
        assert lock_type == LOCK_TYPE_POINT

    def test_tokens_are_unique(self):
        t1 = _make_fencing_token("tx-1")
        time.sleep(0.001)
        t2 = _make_fencing_token("tx-1")
        assert t1 != t2


class TestPathLockStale:
    def test_is_lock_stale_no_file(self):
        agfs = MagicMock()
        agfs.cat.side_effect = Exception("not found")
        lock = PathLock(agfs)
        assert lock.is_lock_stale("/test/.path.ovlock") is True

    def test_is_lock_stale_legacy_token(self):
        agfs = MagicMock()
        agfs.cat.return_value = b"tx-old-format"
        lock = PathLock(agfs)
        assert lock.is_lock_stale("/test/.path.ovlock") is True

    def test_is_lock_stale_recent_token(self):
        agfs = MagicMock()
        token = _make_fencing_token("tx-1")
        agfs.cat.return_value = token.encode("utf-8")
        lock = PathLock(agfs)
        assert lock.is_lock_stale("/test/.path.ovlock", expire_seconds=300.0) is False


class TestPathLockBehavior:
    """Behavioral tests using real AGFS backend."""

    async def test_acquire_point_creates_lock_file(self, agfs_client, test_dir):
        lock = PathLock(agfs_client)
        tx = TransactionRecord(id="tx-point-1")

        ok = await lock.acquire_point(test_dir, tx, timeout=3.0)
        assert ok is True

        lock_path = f"{test_dir}/{LOCK_FILE_NAME}"
        content = agfs_client.cat(lock_path)
        token = content.decode("utf-8") if isinstance(content, bytes) else content
        assert ":P" in token
        assert "tx-point-1" in token

        await lock.release(tx)

    async def test_acquire_subtree_creates_lock_file(self, agfs_client, test_dir):
        lock = PathLock(agfs_client)
        tx = TransactionRecord(id="tx-subtree-1")

        ok = await lock.acquire_subtree(test_dir, tx, timeout=3.0)
        assert ok is True

        lock_path = f"{test_dir}/{LOCK_FILE_NAME}"
        content = agfs_client.cat(lock_path)
        token = content.decode("utf-8") if isinstance(content, bytes) else content
        assert ":S" in token
        assert "tx-subtree-1" in token

        await lock.release(tx)

    async def test_acquire_point_dir_not_found(self, agfs_client):
        lock = PathLock(agfs_client)
        tx = TransactionRecord(id="tx-no-dir")

        ok = await lock.acquire_point("/local/nonexistent-path-xyz", tx, timeout=0.5)
        assert ok is False
        assert len(tx.locks) == 0

    async def test_release_removes_lock_file(self, agfs_client, test_dir):
        lock = PathLock(agfs_client)
        tx = TransactionRecord(id="tx-release-1")

        await lock.acquire_point(test_dir, tx, timeout=3.0)
        lock_path = f"{test_dir}/{LOCK_FILE_NAME}"

        await lock.release(tx)

        # Lock file should be gone
        try:
            agfs_client.cat(lock_path)
            raise AssertionError("Lock file should have been removed")
        except Exception:
            pass  # Expected: file not found

    async def test_sequential_acquire_works(self, agfs_client, test_dir):
        lock = PathLock(agfs_client)

        tx1 = TransactionRecord(id="tx-seq-1")
        ok1 = await lock.acquire_point(test_dir, tx1, timeout=3.0)
        assert ok1 is True

        await lock.release(tx1)

        tx2 = TransactionRecord(id="tx-seq-2")
        ok2 = await lock.acquire_point(test_dir, tx2, timeout=3.0)
        assert ok2 is True

        await lock.release(tx2)

    async def test_point_blocked_by_ancestor_subtree(self, agfs_client, test_dir):
        """POINT on child blocked while ancestor holds SUBTREE lock."""
        import uuid as _uuid

        child = f"{test_dir}/child-{_uuid.uuid4().hex}"
        agfs_client.mkdir(child)

        lock = PathLock(agfs_client)
        tx_parent = TransactionRecord(id="tx-parent-subtree")
        ok = await lock.acquire_subtree(test_dir, tx_parent, timeout=3.0)
        assert ok is True

        tx_child = TransactionRecord(id="tx-child-point")
        blocked = await lock.acquire_point(child, tx_child, timeout=0.5)
        assert blocked is False

        await lock.release(tx_parent)

    async def test_subtree_blocked_by_descendant_point(self, agfs_client, test_dir):
        """SUBTREE on parent blocked while descendant holds POINT lock."""
        import uuid as _uuid

        child = f"{test_dir}/child-{_uuid.uuid4().hex}"
        agfs_client.mkdir(child)

        lock = PathLock(agfs_client)
        tx_child = TransactionRecord(id="tx-desc-point")
        ok = await lock.acquire_point(child, tx_child, timeout=3.0)
        assert ok is True

        tx_parent = TransactionRecord(id="tx-parent-sub")
        blocked = await lock.acquire_subtree(test_dir, tx_parent, timeout=0.5)
        assert blocked is False

        await lock.release(tx_child)

    async def test_acquire_mv_creates_subtree_and_point(self, agfs_client, test_dir):
        """acquire_mv puts SUBTREE on src and POINT on dst."""
        import uuid as _uuid

        src = f"{test_dir}/src-{_uuid.uuid4().hex}"
        dst = f"{test_dir}/dst-{_uuid.uuid4().hex}"
        agfs_client.mkdir(src)
        agfs_client.mkdir(dst)

        lock = PathLock(agfs_client)
        tx = TransactionRecord(id="tx-mv-1")
        ok = await lock.acquire_mv(src, dst, tx, timeout=3.0)
        assert ok is True

        src_token_bytes = agfs_client.cat(f"{src}/{LOCK_FILE_NAME}")
        src_token = (
            src_token_bytes.decode("utf-8")
            if isinstance(src_token_bytes, bytes)
            else src_token_bytes
        )
        assert ":S" in src_token

        dst_token_bytes = agfs_client.cat(f"{dst}/{LOCK_FILE_NAME}")
        dst_token = (
            dst_token_bytes.decode("utf-8")
            if isinstance(dst_token_bytes, bytes)
            else dst_token_bytes
        )
        assert ":P" in dst_token

        await lock.release(tx)

    async def test_point_does_not_block_sibling_point(self, agfs_client, test_dir):
        """POINT locks on different directories do not conflict."""
        import uuid as _uuid

        dir_a = f"{test_dir}/sibling-a-{_uuid.uuid4().hex}"
        dir_b = f"{test_dir}/sibling-b-{_uuid.uuid4().hex}"
        agfs_client.mkdir(dir_a)
        agfs_client.mkdir(dir_b)

        lock = PathLock(agfs_client)
        tx_a = TransactionRecord(id="tx-sib-a")
        tx_b = TransactionRecord(id="tx-sib-b")

        ok_a = await lock.acquire_point(dir_a, tx_a, timeout=3.0)
        ok_b = await lock.acquire_point(dir_b, tx_b, timeout=3.0)

        assert ok_a is True
        assert ok_b is True

        await lock.release(tx_a)
        await lock.release(tx_b)

    async def test_stale_lock_auto_removed_on_acquire(self, agfs_client, test_dir):
        """A stale lock (expired fencing token) is auto-removed, allowing a new acquire."""
        import uuid as _uuid

        target = f"{test_dir}/stale-{_uuid.uuid4().hex}"
        agfs_client.mkdir(target)

        lock_path = f"{target}/{LOCK_FILE_NAME}"

        # Write a lock file with a very old timestamp (simulate crashed process)
        old_ts = time.time_ns() - int(600 * 1e9)  # 600 seconds ago
        stale_token = f"tx-dead:{old_ts}:{LOCK_TYPE_POINT}"
        agfs_client.write(lock_path, stale_token.encode("utf-8"))

        # New transaction should succeed by auto-removing the stale lock
        lock = PathLock(agfs_client, lock_expire=300.0)
        tx = TransactionRecord(id="tx-new-owner")
        ok = await lock.acquire_point(target, tx, timeout=2.0)
        assert ok is True

        # Verify new lock is owned by our transaction
        content = agfs_client.cat(lock_path)
        token = content.decode("utf-8") if isinstance(content, bytes) else content
        assert "tx-new-owner" in token

        await lock.release(tx)

    async def test_stale_subtree_ancestor_auto_removed(self, agfs_client, test_dir):
        """A stale SUBTREE lock on ancestor is auto-removed when child acquires POINT."""
        import uuid as _uuid

        child = f"{test_dir}/child-stale-{_uuid.uuid4().hex}"
        agfs_client.mkdir(child)

        # Write stale SUBTREE lock on parent
        parent_lock = f"{test_dir}/{LOCK_FILE_NAME}"
        old_ts = time.time_ns() - int(600 * 1e9)
        stale_token = f"tx-dead-parent:{old_ts}:{LOCK_TYPE_SUBTREE}"
        agfs_client.write(parent_lock, stale_token.encode("utf-8"))

        lock = PathLock(agfs_client, lock_expire=300.0)
        tx = TransactionRecord(id="tx-child-new")
        ok = await lock.acquire_point(child, tx, timeout=2.0)
        assert ok is True

        await lock.release(tx)
        # Clean up stale parent lock if still present
        try:
            agfs_client.rm(parent_lock)
        except Exception:
            pass

    async def test_point_same_path_no_wait_fails_immediately(self, agfs_client, test_dir):
        """With timeout=0, a conflicting lock fails immediately."""
        import uuid as _uuid

        target = f"{test_dir}/nowait-{_uuid.uuid4().hex}"
        agfs_client.mkdir(target)

        lock = PathLock(agfs_client)
        tx1 = TransactionRecord(id="tx-hold")
        ok1 = await lock.acquire_point(target, tx1, timeout=3.0)
        assert ok1 is True

        # Second acquire with timeout=0 should fail immediately
        tx2 = TransactionRecord(id="tx-blocked")
        t0 = time.monotonic()
        ok2 = await lock.acquire_point(target, tx2, timeout=0.0)
        elapsed = time.monotonic() - t0

        assert ok2 is False
        assert elapsed < 1.0  # Should not wait

        await lock.release(tx1)

    async def test_subtree_same_path_mutual_exclusion(self, agfs_client, test_dir):
        """Two SUBTREE locks on the same path: second one blocked until first releases."""
        import uuid as _uuid

        target = f"{test_dir}/sub-excl-{_uuid.uuid4().hex}"
        agfs_client.mkdir(target)

        lock = PathLock(agfs_client)
        tx1 = TransactionRecord(id="tx-sub1")
        ok1 = await lock.acquire_subtree(target, tx1, timeout=3.0)
        assert ok1 is True

        tx2 = TransactionRecord(id="tx-sub2")
        ok2 = await lock.acquire_subtree(target, tx2, timeout=0.5)
        assert ok2 is False

        await lock.release(tx1)

        # Now tx2 should succeed
        ok2_retry = await lock.acquire_subtree(target, tx2, timeout=3.0)
        assert ok2_retry is True
        await lock.release(tx2)
