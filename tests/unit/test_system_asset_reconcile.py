import asyncio
import json

import pytest

from openviking.server.identity import Role
from openviking.storage.errors import ResourceBusyError
from openviking.system_assets.reconcile import SystemAssetReconciler


class _FakeAGFS:
    def __init__(self):
        self.marker = None
        self.lock = asyncio.Lock()

    async def read(self, path, fs_ctx=None):
        if self.marker is None:
            raise FileNotFoundError(path)
        return self.marker

    async def pathlock_acquire_exact(self, path, timeout_secs):
        await self.lock.acquire()
        return {"lease_ref": "manifest-lease"}

    async def pathlock_release(self, lease):
        self.lock.release()

    async def ensure_parent_dirs(self, path, fs_ctx=None):
        pass

    async def write(self, path, data, fs_ctx=None, auto_pathlock=True):
        assert self.lock.locked()
        self.marker = data


class _FakeAccountLister:
    def __init__(self, accounts):
        self._accounts = accounts
        self.calls = []

    async def list_accounts(self, *, limit, page):
        self.calls.append((limit, page))
        start = (page - 1) * limit
        return self._accounts[start : start + limit]


class _TrackingSynchronizer:
    def __init__(self, failures=(), busy_once=()):
        self._failures = set(failures)
        self._busy_once = set(busy_once)
        self.manifest_digest = "d" * 64
        self.manifest = json.dumps({"schema_version": 1, "sha256": self.manifest_digest}).encode()
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
            if account_id in self._busy_once:
                self._busy_once.remove(account_id)
                raise ResourceBusyError("busy")
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
    agfs = _FakeAGFS()
    synchronizer = _TrackingSynchronizer(busy_once={"a"})
    reconciler = SystemAssetReconciler(
        synchronizer,
        lister,
        agfs,
        user_id="system-user",
        page_size=2,
        max_concurrency=2,
    )

    assert await reconciler.sync_if_changed() == 4
    assert lister.calls == [(2, 1), (2, 2), (2, 3)]
    assert set(synchronizer.accounts) == {"a", "c", "d", "e"}
    assert synchronizer.max_active == 2
    assert synchronizer.accounts.count("a") == 2
    assert agfs.marker == synchronizer.manifest
    assert not agfs.lock.locked()

    lister.calls.clear()
    synchronizer.accounts.clear()
    assert await reconciler.sync_if_changed() == 0
    assert lister.calls == []
    assert synchronizer.accounts == []


@pytest.mark.asyncio
async def test_reconciler_skips_already_synced_account_without_ending_pagination():
    lister = _FakeAccountLister(
        [
            {"account_id": "default", "status": "active"},
            {"account_id": "a", "status": "active"},
            {"account_id": "b", "status": "active"},
        ]
    )
    agfs = _FakeAGFS()
    synchronizer = _TrackingSynchronizer()
    reconciler = SystemAssetReconciler(
        synchronizer,
        lister,
        agfs,
        user_id="system-user",
        skip_account_ids={"default"},
        page_size=2,
    )

    other_writer = SystemAssetReconciler(
        synchronizer,
        lister,
        agfs,
        user_id="system-user",
        skip_account_ids={"default"},
        page_size=2,
    )
    results = await asyncio.gather(reconciler.sync_if_changed(), other_writer.sync_if_changed())

    assert sorted(results) == [0, 2]
    assert lister.calls == [(2, 1), (2, 2)]
    assert sorted(synchronizer.accounts) == ["a", "b"]
    assert not agfs.lock.locked()


@pytest.mark.asyncio
async def test_reconciler_reports_all_failures_in_page():
    lister = _FakeAccountLister(
        [
            {"account_id": "a", "status": "active"},
            {"account_id": "b", "status": "active"},
        ]
    )
    agfs = _FakeAGFS()
    synchronizer = _TrackingSynchronizer(failures={"a", "b"})
    reconciler = SystemAssetReconciler(
        synchronizer,
        lister,
        agfs,
        user_id="system-user",
        page_size=2,
        max_concurrency=2,
    )

    with pytest.raises(RuntimeError, match="failed for 2 account") as exc_info:
        await reconciler.sync_if_changed()

    assert "a: OSError: failed a" in str(exc_info.value)
    assert "b: OSError: failed b" in str(exc_info.value)

    assert agfs.marker is None
    assert not agfs.lock.locked()

    synchronizer._failures.clear()
    assert await reconciler.sync_if_changed() == 2
    assert agfs.marker == synchronizer.manifest
