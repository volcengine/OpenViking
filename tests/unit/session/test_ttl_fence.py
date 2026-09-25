# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Concurrency fences for delayed writes derived from TTL sessions."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import (
    AGFSConnectionError,
    AGFSHTTPError,
    AGFSNetworkError,
    AGFSNotFoundError,
    AGFSTimeoutError,
)
from openviking.server.identity import RequestContext, Role
from openviking.session.ttl_fence import (
    StaleSessionGenerationError,
    session_generation_fence,
)
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


def _fs(metadata):
    agfs = SimpleNamespace(
        pathlock_acquire_tree=AsyncMock(return_value="tree-lease"),
        pathlock_acquire_batch=AsyncMock(return_value="batch-lease"),
        pathlock_release=AsyncMock(),
    )
    read_file = (
        AsyncMock(side_effect=metadata)
        if isinstance(metadata, Exception)
        else AsyncMock(return_value=json.dumps(metadata))
    )
    return SimpleNamespace(
        _async_agfs=agfs,
        _uri_to_path=lambda uri, ctx=None: f"/local/acct/{uri.removeprefix('viking://')}",
        read_file=read_file,
    )


@pytest.mark.asyncio
async def test_disabled_fence_preserves_legacy_lock_behavior():
    fs = _fs({})
    fence = session_generation_fence(fs, object())

    assert fence.enabled is False
    assert fence.key == ("", "")
    assert fence.lock_path() == ""
    assert await fence.is_current() is True
    async with fence.lock() as lease:
        assert lease is None
    fs.read_file.assert_not_awaited()
    fs._async_agfs.pathlock_acquire_tree.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"ttl_generation": "g1", "expires_at": "2999-01-01T00:00:00.000Z"}, True),
        ({"ttl_generation": "g2", "expires_at": "2999-01-01T00:00:00.000Z"}, False),
        ({"ttl_generation": "g1", "expires_at": "2000-01-01T00:00:00.000Z"}, False),
        (FileNotFoundError("session metadata"), False),
        (NotFoundError("session metadata", "file"), False),
        (AGFSNotFoundError("session metadata"), False),
        (AGFSHTTPError("session metadata", status_code=404), False),
    ],
)
async def test_is_current_requires_same_live_generation(metadata, expected):
    fence = session_generation_fence(
        _fs(metadata), object(), session_uri="viking://user/u1/sessions/s1/", generation="g1"
    )

    assert fence.key == ("viking://user/u1/sessions/s1", "g1")
    assert await fence.is_current() is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("AGFS timed out"),
        ConnectionError("AGFS offline"),
        ConnectionError("DNS name not found"),
        AGFSNetworkError("endpoint not found"),
        AGFSTimeoutError("backend not found before timeout"),
        AGFSConnectionError("host does not exist"),
        AGFSHTTPError("backend not found", status_code=503),
        RuntimeError("backend not found"),
    ],
)
async def test_storage_failure_is_not_a_stale_generation_and_releases_lock(error):
    fs = _fs(error)
    fence = session_generation_fence(
        fs, object(), session_uri="viking://user/u1/sessions/s1", generation="g1"
    )

    with pytest.raises(type(error), match=str(error)):
        async with fence.lock():
            pytest.fail("unverified work must not enter the write section")
    fs._async_agfs.pathlock_release.assert_awaited_once_with("tree-lease")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stat", "read"])
async def test_fence_rejects_a_vikingfs_not_found_wrapper_around_network_failure(operation):
    fs = VikingFS(agfs=SimpleNamespace())
    ctx = RequestContext(user=UserIdentifier("acct", "u1"), role=Role.ROOT)
    error = AGFSNetworkError("endpoint not found")
    fs._async_agfs.stat = AsyncMock(return_value={"isDir": False})
    fs._async_agfs.read = AsyncMock()
    getattr(fs._async_agfs, operation).side_effect = error
    fence = session_generation_fence(
        fs, ctx, session_uri="viking://user/u1/sessions/s1", generation="g1"
    )

    # The legacy VikingFS read API translates the message into NotFoundError.
    # The generation fence must inspect its cause rather than skip extraction.
    with pytest.raises(NotFoundError) as raised:
        await fence.require_current()
    assert raised.value.__cause__ is error


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stat", "read"])
@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("metadata missing"),
        AGFSNotFoundError("metadata missing"),
        AGFSHTTPError("metadata missing", status_code=404),
    ],
)
async def test_fence_accepts_a_vikingfs_wrapper_for_missing_metadata(operation, error):
    fs = VikingFS(agfs=SimpleNamespace())
    ctx = RequestContext(user=UserIdentifier("acct", "u1"), role=Role.ROOT)
    fs._async_agfs.stat = AsyncMock(return_value={"isDir": False})
    fs._async_agfs.read = AsyncMock()
    getattr(fs._async_agfs, operation).side_effect = error
    fence = session_generation_fence(
        fs, ctx, session_uri="viking://user/u1/sessions/s1", generation="g1"
    )

    assert await fence.is_current() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["not json", "[]", "null"])
async def test_corrupt_metadata_is_not_a_stale_generation(raw):
    fs = _fs({})
    fs.read_file.return_value = raw
    fence = session_generation_fence(
        fs, object(), session_uri="viking://user/u1/sessions/s1", generation="g1"
    )

    with pytest.raises(ValueError):
        await fence.require_current()


@pytest.mark.asyncio
async def test_allow_expired_only_relaxes_deadline_not_generation():
    expired = _fs(
        {"ttl_generation": "g1", "expires_at": "2000-01-01T00:00:00.000Z"}
    )
    fence = session_generation_fence(
        expired,
        object(),
        session_uri="viking://user/u1/sessions/s1",
        generation="g1",
    )

    assert await fence.is_current(allow_expired=True) is True

    expired.read_file.return_value = json.dumps(
        {"ttl_generation": "g2", "expires_at": "2000-01-01T00:00:00.000Z"}
    )
    assert await fence.is_current(allow_expired=True) is False


@pytest.mark.asyncio
async def test_tree_lock_rechecks_generation_and_always_releases():
    fs = _fs({"ttl_generation": "g1", "expires_at": "2999-01-01T00:00:00.000Z"})
    fence = session_generation_fence(
        fs, object(), session_uri="viking://user/u1/sessions/s1", generation="g1"
    )

    async with fence.lock() as lease:
        assert lease == "tree-lease"
        fs._async_agfs.pathlock_release.assert_not_awaited()

    fs._async_agfs.pathlock_acquire_tree.assert_awaited_once_with(
        "/local/acct/user/u1/sessions/s1", timeout_secs=300.0
    )
    fs._async_agfs.pathlock_release.assert_awaited_once_with("tree-lease")

    fs.read_file.return_value = json.dumps(
        {"ttl_generation": "g2", "expires_at": "2999-01-01T00:00:00.000Z"}
    )
    with pytest.raises(StaleSessionGenerationError, match="stale TTL session generation"):
        async with fence.lock():
            raise AssertionError("stale work must not enter the write section")
    assert fs._async_agfs.pathlock_release.await_count == 2


@pytest.mark.asyncio
async def test_policy_lock_batches_policy_and_session_before_recheck():
    fs = _fs({"ttl_generation": "g1", "expires_at": "2999-01-01T00:00:00.000Z"})
    ctx = object()
    fence = session_generation_fence(
        fs, ctx, session_uri="viking://user/u1/sessions/s1", generation="g1"
    )

    class _PolicySet:
        viking_fs = fs
        root_uri = "viking://user/u1/memories/preferences"
        request_context = ctx

        @asynccontextmanager
        async def lock(self):
            raise AssertionError("enabled TTL fence must use the batch lock")
            yield

    async with fence.lock_policy_set(_PolicySet()) as lease:
        assert lease == "batch-lease"

    fs._async_agfs.pathlock_acquire_batch.assert_awaited_once_with(
        [
            {"path": "/local/acct/user/u1/memories/preferences", "kind": "tree"},
            {"path": "/local/acct/user/u1/sessions/s1", "kind": "tree"},
        ],
        timeout_secs=300.0,
    )
    fs._async_agfs.pathlock_release.assert_awaited_once_with("batch-lease")

    other_fs = _fs({})
    policy_set = _PolicySet()
    policy_set.viking_fs = other_fs
    with pytest.raises(RuntimeError, match="same VikingFS"):
        async with fence.lock_policy_set(policy_set):
            pass
