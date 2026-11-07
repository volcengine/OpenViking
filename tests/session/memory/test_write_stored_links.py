# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for write_stored_links lock contention handling (#5648).

Covers three defects reported in the issue:
- endpoint pathlocks are never pre-acquired, so every write races an
  auto-pathlock with a 0ms default timeout and fails immediately;
- per-endpoint failures are swallowed with one ERROR log each, with no
  aggregated, countable signal;
- callers drop the return value, so failed endpoints cannot be excluded from
  edit reporting.
"""

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.memory.memory_updater import (
    MemoryUpdateResult,
    write_stored_links,
)
from openviking.session.memory.utils import MemoryFileUtils
from openviking.storage.errors import LockAcquisitionError
from openviking_cli.session.user_id import UserIdentifier

LOCK_ERROR_MESSAGE = "lock acquire timed out after 0ms"

URI_A = "viking://user/alice/memories/cases/a.md"
URI_B = "viking://user/alice/memories/events/b.md"


class _FakeAGFS:
    """Records pathlock acquisition and release calls."""

    def __init__(self, fail_uris=()):
        self.fail_uris = set(fail_uris)
        self.acquired = []
        self.released = []

    async def pathlock_acquire_exact_batch(
        self, paths, timeout_secs=0.0, owner_lease_ref=None, **kwargs
    ):
        lease = {"id": f"lease-{len(self.acquired) + 1}", "paths": list(paths)}
        self.acquired.append({"lease": lease, "timeout_secs": timeout_secs})
        return lease

    async def pathlock_release(self, lease, **kwargs):
        self.released.append(lease["id"])


class _FakeVikingFS:
    """Minimal viking_fs double: readable memory files, controllable writes."""

    def __init__(self, fail_uris=()):
        self._async_agfs = _FakeAGFS(fail_uris)
        self.written = []
        self.read_uris = []

    def _uri_to_path(self, uri, ctx=None):
        del ctx
        return f"/local/acme/user/alice/{uri.split('/user/alice/', 1)[-1]}"

    async def read_file(self, uri, ctx=None, **kwargs):
        del ctx, kwargs
        self.read_uris.append(uri)
        return MemoryFileUtils.write(MemoryFile(uri=uri, content=f"content of {uri}"))

    async def write_file(self, uri, content, ctx=None, lease_ref=None, **kwargs):
        del content, ctx, kwargs
        if uri in self._async_agfs.fail_uris:
            raise LockAcquisitionError(LOCK_ERROR_MESSAGE)
        self.written.append({"uri": uri, "lease_ref": lease_ref})


def _ctx():
    return RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)


def _link(from_uri=URI_A, to_uri=URI_B):
    return StoredLink(from_uri=from_uri, to_uri=to_uri)


@pytest.mark.asyncio
async def test_write_stored_links_returns_successful_subset_under_lock_contention():
    """Baseline: lock contention on one endpoint must not raise or abort the batch."""
    viking_fs = _FakeVikingFS(fail_uris={URI_B})

    updated_uris = await write_stored_links([_link()], _ctx(), viking_fs)

    assert updated_uris == [URI_A]
    assert URI_B not in updated_uris


@pytest.mark.asyncio
async def test_write_stored_links_aggregates_failures_into_one_warning(monkeypatch):
    """Failed endpoints are aggregated into a single countable warning (#5648 Ask 2)."""
    from openviking.session.memory import memory_updater

    viking_fs = _FakeVikingFS(fail_uris={URI_B})
    warnings = []
    errors = []

    def fake_warning(message, *args, **kwargs):
        warnings.append(message % args if args else message)

    def fake_error(message, *args, **kwargs):
        errors.append(message % args if args else message)

    def fake_debug(message, *args, **kwargs):
        pass

    monkeypatch.setattr(memory_updater.logger, "warning", fake_warning)
    monkeypatch.setattr(memory_updater.logger, "error", fake_error)
    monkeypatch.setattr(memory_updater.logger, "debug", fake_debug)

    updated_uris = await write_stored_links([_link()], _ctx(), viking_fs)

    assert updated_uris == [URI_A]
    assert len(warnings) == 1
    assert "1/2" in warnings[0]
    assert URI_B in warnings[0]
    assert errors == []


@pytest.mark.asyncio
async def test_apply_links_to_existing_files_prelocks_endpoints_and_releases():
    """Endpoint locks are pre-acquired with the streaming timeout and always released."""
    from openviking.session.memory.memory_updater import MemoryUpdater
    from openviking.session.memory.streaming_memory_updater import (
        _MEMORY_APPLY_LOCK_TIMEOUT_SECONDS,
    )

    viking_fs = _FakeVikingFS()
    updater = MemoryUpdater()
    updater._viking_fs = viking_fs
    result = MemoryUpdateResult()
    ctx = _ctx()

    await updater._apply_links_to_existing_files([_link()], result, ctx)

    assert len(viking_fs._async_agfs.acquired) == 1
    acquisition = viking_fs._async_agfs.acquired[0]
    assert acquisition["timeout_secs"] == _MEMORY_APPLY_LOCK_TIMEOUT_SECONDS
    acquired_paths = acquisition["lease"]["paths"]
    assert len(acquired_paths) == 2
    assert viking_fs._async_agfs.released == [acquisition["lease"]["id"]]
    # Both endpoint writes ran while holding the batch lease.
    assert {entry["uri"] for entry in viking_fs.written} == {URI_A, URI_B}
    write_leases = {entry["lease_ref"]["id"] for entry in viking_fs.written}
    assert write_leases == {acquisition["lease"]["id"]}


@pytest.mark.asyncio
async def test_apply_links_to_existing_files_skips_skipped_uris_when_locking():
    """URIs the caller handles elsewhere are neither locked nor written."""
    from openviking.session.memory.memory_updater import MemoryUpdater

    viking_fs = _FakeVikingFS()
    updater = MemoryUpdater()
    updater._viking_fs = viking_fs
    result = MemoryUpdateResult()
    result.add_written(URI_B)

    await updater._apply_links_to_existing_files([_link()], result, _ctx(), deleted_uris=set())

    acquired_paths = viking_fs._async_agfs.acquired[0]["lease"]["paths"]
    assert len(acquired_paths) == 1
    assert {entry["uri"] for entry in viking_fs.written} == {URI_A}


@pytest.mark.asyncio
async def test_apply_links_to_existing_files_reports_only_successful_link_edits():
    """The return-value contract: failed endpoints are not reported as edits."""
    from openviking.session.memory.memory_updater import MemoryUpdater

    viking_fs = _FakeVikingFS(fail_uris={URI_B})
    updater = MemoryUpdater()
    updater._viking_fs = viking_fs
    result = MemoryUpdateResult()

    await updater._apply_links_to_existing_files([_link()], result, _ctx())

    assert result.edited_uris == [URI_A]
    assert URI_B not in result.edited_uris


@pytest.mark.asyncio
async def test_apply_links_to_existing_files_releases_lock_when_write_fails():
    """The batch lease is released even when endpoint writes raise."""
    from openviking.session.memory.memory_updater import MemoryUpdater

    viking_fs = _FakeVikingFS(fail_uris={URI_A, URI_B})
    updater = MemoryUpdater()
    updater._viking_fs = viking_fs
    result = MemoryUpdateResult()

    await updater._apply_links_to_existing_files([_link()], result, _ctx())

    assert result.edited_uris == []
    assert len(viking_fs._async_agfs.acquired) == 1
    lease_id = viking_fs._async_agfs.acquired[0]["lease"]["id"]
    assert viking_fs._async_agfs.released == [lease_id]


@pytest.mark.asyncio
async def test_apply_links_to_existing_files_keeps_caller_lease_without_reacquiring():
    """A caller-supplied transaction lease already covers endpoints: no second lease.

    Acquiring a second lease on paths held by the caller's transaction lease would
    self-deadlock until the timeout, so the transaction handle stays authoritative.
    """
    from openviking.session.memory.memory_updater import MemoryUpdater

    viking_fs = _FakeVikingFS()
    updater = MemoryUpdater()
    updater._viking_fs = viking_fs
    result = MemoryUpdateResult()
    transaction_handle = {"lease_ref": "txn-1"}

    await updater._apply_links_to_existing_files(
        [_link()], result, _ctx(), lease_ref=transaction_handle
    )

    assert viking_fs._async_agfs.acquired == []
    assert viking_fs._async_agfs.released == []
    assert [entry["lease_ref"] for entry in viking_fs.written] == [
        transaction_handle,
        transaction_handle,
    ]
