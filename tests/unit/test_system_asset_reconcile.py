import asyncio

import pytest

from openviking.server.identity import Role
from openviking.system_assets.reconcile import SystemAssetReconciler


class _FakeAccountLister:
    def __init__(self, accounts):
        self._accounts = accounts
        self.calls = []

    async def list_accounts(self, *, limit, page):
        self.calls.append((limit, page))
        start = (page - 1) * limit
        return self._accounts[start : start + limit]


class _TrackingSynchronizer:
    def __init__(self, failures=()):
        self._failures = set(failures)
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
        user_id="system-user",
        page_size=2,
        max_concurrency=2,
    )

    with pytest.raises(RuntimeError, match="failed for 2 account") as exc_info:
        await reconciler.sync_all()

    assert "a: OSError: failed a" in str(exc_info.value)
    assert "b: OSError: failed b" in str(exc_info.value)
