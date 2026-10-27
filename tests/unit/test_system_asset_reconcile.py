import asyncio
import json

import pytest

from openviking.server.identity import Role
from openviking.system_assets.reconcile import SystemAssetReconciler


class _FakeAGFS:
    def __init__(self, marker=None, marker_on_acquire=None):
        self.marker = marker
        self.marker_on_acquire = marker_on_acquire
        self.read_calls = 0
        self.acquire_calls = 0
        self.release_calls = []
        self.write_calls = []

    async def read(self, path, fs_ctx=None):
        self.read_calls += 1
        if self.marker is None:
            raise FileNotFoundError(path)
        return self.marker

    async def pathlock_acquire_exact(self, path, timeout_secs):
        self.acquire_calls += 1
        if self.marker_on_acquire is not None:
            self.marker = self.marker_on_acquire
        return {"lease_ref": "manifest-lease"}

    async def pathlock_release(self, lease):
        self.release_calls.append(lease)

    async def ensure_parent_dirs(self, path, fs_ctx=None):
        return None

    async def write(self, path, data, fs_ctx=None, auto_pathlock=True):
        self.marker = data
        self.write_calls.append((path, data, fs_ctx, auto_pathlock))


class _FakeAccountLister:
    def __init__(self, accounts):
        self._accounts = accounts
        self.calls = []

    async def list_accounts(self, *, limit, page):
        self.calls.append((limit, page))
        start = (page - 1) * limit
        return self._accounts[start : start + limit]


class _TrackingSynchronizer:
    def __init__(self, failures=(), manifest_digest="digest"):
        self._failures = set(failures)
        self.manifest_digest = manifest_digest
        self.accounts = []
        self.active = 0
        self.max_active = 0

    async def sync(self, ctx):
        account_id = ctx.user.account_id
        assert ctx.role == Role.ROOT
        assert ctx.user.user_id == "system-user"
        self.accounts.append(account_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            if account_id in self._failures:
                raise OSError(f"failed {account_id}")
            return 0
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_reconciler_pages_active_accounts_with_bounded_concurrency():
    lister = _FakeAccountLister(
        [
            {"account_id": "a", "status": "active"},
            {"account_id": "b", "status": "deleting"},
            {"account_id": "c", "status": "active"},
            {"account_id": "d", "status": "active"},
            {"account_id": "e", "status": "active"},
        ]
    )
    synchronizer = _TrackingSynchronizer()
    reconciler = SystemAssetReconciler(
        synchronizer,
        lister,
        _FakeAGFS(),
        user_id="system-user",
        page_size=2,
        max_concurrency=2,
    )

    assert await reconciler.sync_all() == 4
    assert lister.calls == [(2, 1), (2, 2), (2, 3)]
    assert set(synchronizer.accounts) == {"a", "c", "d", "e"}
    assert synchronizer.max_active == 2


@pytest.mark.asyncio
async def test_reconciler_skips_already_synced_account_without_ending_pagination():
    lister = _FakeAccountLister(
        [
            {"account_id": "default", "status": "active"},
            {"account_id": "a", "status": "active"},
            {"account_id": "b", "status": "active"},
        ]
    )
    synchronizer = _TrackingSynchronizer()
    reconciler = SystemAssetReconciler(
        synchronizer,
        lister,
        _FakeAGFS(),
        user_id="system-user",
        skip_account_ids={"default"},
        page_size=2,
    )

    assert await reconciler.sync_all() == 2
    assert lister.calls == [(2, 1), (2, 2)]
    assert set(synchronizer.accounts) == {"a", "b"}


@pytest.mark.asyncio
async def test_reconciler_reports_all_failures_in_page():
    lister = _FakeAccountLister(
        [
            {"account_id": "a", "status": "active"},
            {"account_id": "b", "status": "active"},
        ]
    )
    synchronizer = _TrackingSynchronizer(failures={"a", "b"})
    reconciler = SystemAssetReconciler(
        synchronizer,
        lister,
        _FakeAGFS(),
        user_id="system-user",
        page_size=2,
        max_concurrency=2,
    )

    with pytest.raises(RuntimeError, match="failed for 2 account") as exc_info:
        await reconciler.sync_all()

    assert "a: OSError: failed a" in str(exc_info.value)
    assert "b: OSError: failed b" in str(exc_info.value)


@pytest.mark.asyncio
async def test_reconciler_skips_account_listing_when_manifest_matches():
    marker = json.dumps({"sha256": "digest"}).encode()
    agfs = _FakeAGFS(marker=marker)
    lister = _FakeAccountLister([{"account_id": "a", "status": "active"}])
    reconciler = SystemAssetReconciler(
        _TrackingSynchronizer(),
        lister,
        agfs,
        user_id="system-user",
    )

    assert await reconciler.sync_if_changed() == 0
    assert lister.calls == []
    assert agfs.acquire_calls == 0


@pytest.mark.asyncio
async def test_reconciler_syncs_and_writes_manifest_under_lock():
    agfs = _FakeAGFS()
    lister = _FakeAccountLister([{"account_id": "a", "status": "active"}])
    synchronizer = _TrackingSynchronizer()
    reconciler = SystemAssetReconciler(
        synchronizer,
        lister,
        agfs,
        user_id="system-user",
    )

    assert await reconciler.sync_if_changed() == 1
    assert synchronizer.accounts == ["a"]
    assert json.loads(agfs.marker) == {"sha256": "digest"}
    assert agfs.write_calls[0][2]["lease_ref"] == "manifest-lease"
    assert agfs.write_calls[0][3] is False
    assert agfs.release_calls == [{"lease_ref": "manifest-lease"}]


@pytest.mark.asyncio
async def test_reconciler_rechecks_manifest_after_acquiring_lock():
    marker = json.dumps({"sha256": "digest"}).encode()
    agfs = _FakeAGFS(marker_on_acquire=marker)
    lister = _FakeAccountLister([{"account_id": "a", "status": "active"}])
    reconciler = SystemAssetReconciler(
        _TrackingSynchronizer(),
        lister,
        agfs,
        user_id="system-user",
    )

    assert await reconciler.sync_if_changed() == 0
    assert lister.calls == []
    assert agfs.write_calls == []
    assert agfs.release_calls == [{"lease_ref": "manifest-lease"}]


@pytest.mark.asyncio
async def test_reconciler_does_not_write_manifest_when_sync_fails():
    agfs = _FakeAGFS()
    lister = _FakeAccountLister([{"account_id": "a", "status": "active"}])
    reconciler = SystemAssetReconciler(
        _TrackingSynchronizer(failures={"a"}),
        lister,
        agfs,
        user_id="system-user",
    )

    with pytest.raises(RuntimeError, match="failed for 1 account"):
        await reconciler.sync_if_changed()

    assert agfs.write_calls == []
    assert agfs.release_calls == [{"lease_ref": "manifest-lease"}]
