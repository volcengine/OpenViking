# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unreadable registries cannot become mutation baselines or authorization evidence."""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from openviking.pyagfs import AGFSNotFoundError
from openviking.server.account_stores import file
from openviking.server.identity import Role
from openviking.server.store_assembly import build_api_key_manager
from openviking_cli.exceptions import FailedPreconditionError
from tests.server.account_store_fakes import InMemoryAGFS

ROOT_KEY = "test-root-key"
ACCOUNT = "read_test"
USERS_PATH = file.USERS_PATH_TEMPLATE.format(account_id=ACCOUNT)


@pytest_asyncio.fixture
async def state(monkeypatch):
    monkeypatch.setattr(file, "_REGISTRY_READ_RETRY_DELAYS", (0, 0))
    agfs = InMemoryAGFS()
    fs = SimpleNamespace(agfs=agfs)
    manager = build_api_key_manager(root_key=ROOT_KEY, viking_fs=fs)
    await manager.load()
    key = await manager.create_account(ACCOUNT, "admin")
    try:
        yield manager, agfs, fs, key
    finally:
        await manager.close()


@pytest.mark.parametrize("misses", [1, 2])
async def test_transient_missing_read_preserves_members_and_uses_one_lease(
    state, monkeypatch, misses
):
    manager, agfs, fs, key = state
    read = agfs.read
    leases = []

    def flaky(path, ctx=None):
        if path == USERS_PATH and ctx and ctx.get("lease_ref"):
            leases.append(ctx["lease_ref"])
            if len(leases) <= misses:
                raise AGFSNotFoundError(path)
        return read(path, ctx=ctx)

    monkeypatch.setattr(agfs, "read", flaky)
    await manager.register_user(ACCOUNT, "new-user")
    assert len(leases) == misses + 1
    assert len(set(leases)) == 1
    assert not agfs._leases
    verifier = build_api_key_manager(root_key=ROOT_KEY, viking_fs=fs)
    await verifier.load()
    try:
        assert (await verifier.resolve_identity(key)).role == Role.ADMIN
        assert await verifier.get_user_membership(ACCOUNT, "new-user") is not None
    finally:
        await verifier.close()


@pytest.mark.parametrize("operation", ["mutation", "trusted", "reload", "refresh", "load"])
async def test_missing_existing_registry_refuses_writes_and_cached_auth(
    state, monkeypatch, operation
):
    manager, agfs, fs, key = state
    store = manager._store
    original = dict(agfs._files)
    read = agfs.read
    attempts = 0

    def missing(path, ctx=None):
        nonlocal attempts
        if path == USERS_PATH:
            attempts += 1
            raise AGFSNotFoundError(path)
        return read(path, ctx=ctx)

    monkeypatch.setattr(agfs, "read", missing)
    with pytest.raises(FailedPreconditionError, match="User registry is missing"):
        if operation == "mutation":
            await manager.register_user(ACCOUNT, "new-user")
        elif operation == "trusted":
            await manager.ensure_trusted_identities({ACCOUNT: {"new-user"}})
        elif operation == "reload":
            await store._reload()
        elif operation == "refresh":
            await store._refresh_account_users_from_store_unlocked(ACCOUNT)
        else:
            replica = build_api_key_manager(root_key=ROOT_KEY, viking_fs=fs)
            await replica.load()
    assert attempts == 3
    assert agfs._files == original
    assert store._accounts[ACCOUNT].users["admin"]["role"] == "admin"
    if operation != "load":
        assert store._cache_available is False
        with pytest.raises(FailedPreconditionError):
            await manager.resolve_identity(key)
        with pytest.raises(FailedPreconditionError):
            await manager.resolve_oauth_identity(ACCOUNT, "admin", Role.ADMIN)
    assert not agfs._leases
    monkeypatch.setattr(agfs, "read", read)
    assert (await manager.resolve_identity(key)).role == Role.ADMIN
    assert store._cache_available is True
    assert agfs._files == original


@pytest.mark.parametrize(
    "bad_content", [b"{}", b"[]", b'{"users": null}', b'{"users":', b'{"users":{"admin":null}}']
)
async def test_corrupt_registry_is_not_retried_or_overwritten(state, monkeypatch, bad_content):
    manager, agfs, _, key = state
    original = dict(agfs._files)
    read = agfs.read
    attempts = 0

    def corrupt(path, ctx=None):
        nonlocal attempts
        if path == USERS_PATH:
            attempts += 1
            return bad_content
        return read(path, ctx=ctx)

    monkeypatch.setattr(agfs, "read", corrupt)
    with pytest.raises((FailedPreconditionError, ValueError)):
        await manager.register_user(ACCOUNT, "new-user")
    assert attempts == 1
    assert agfs._files == original
    assert not agfs._leases
    monkeypatch.setattr(agfs, "read", read)
    assert (await manager.resolve_identity(key)).role == Role.ADMIN


async def test_permission_error_is_not_retried_or_overwritten(state, monkeypatch):
    manager, agfs, _, _ = state
    original = dict(agfs._files)
    read = agfs.read
    attempts = 0

    def denied(path, ctx=None):
        nonlocal attempts
        if path == USERS_PATH:
            attempts += 1
            raise PermissionError("injected access denied")
        return read(path, ctx=ctx)

    monkeypatch.setattr(agfs, "read", denied)
    with pytest.raises(PermissionError):
        await manager.register_user(ACCOUNT, "new-user")
    assert attempts == 1
    assert agfs._files == original
    assert not agfs._leases


async def test_cancelled_retry_releases_lease_and_leaves_registry_unchanged(state, monkeypatch):
    manager, agfs, _, key = state
    original = dict(agfs._files)
    read = agfs.read
    entered = asyncio.Event()
    monkeypatch.setattr(file, "_REGISTRY_READ_RETRY_DELAYS", (60,))

    def missing(path, ctx=None):
        if path == USERS_PATH and ctx and ctx.get("lease_ref"):
            entered.set()
            raise AGFSNotFoundError(path)
        return read(path, ctx=ctx)

    monkeypatch.setattr(agfs, "read", missing)
    task = asyncio.create_task(manager.register_user(ACCOUNT, "new-user"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not agfs._leases
    assert agfs._files == original
    monkeypatch.setattr(agfs, "read", read)
    assert (await manager.resolve_identity(key)).role == Role.ADMIN


async def test_watcher_metadata_failure_requires_full_recovery(state, monkeypatch):
    manager, agfs, _, key = state
    store = manager._store
    stat = agfs.stat
    read = agfs.read
    original = dict(agfs._files)

    def missing_stat(path, **kwargs):
        if path == USERS_PATH:
            raise AGFSNotFoundError(path)
        return stat(path, **kwargs)

    def missing_read(path, **kwargs):
        if path == USERS_PATH:
            raise AGFSNotFoundError(path)
        return read(path, **kwargs)

    monkeypatch.setattr(agfs, "stat", missing_stat)
    with pytest.raises(FailedPreconditionError):
        await store._compute_store_signature()
    assert store._cache_available is False
    monkeypatch.setattr(agfs, "read", missing_read)
    with pytest.raises(FailedPreconditionError):
        await manager.resolve_identity(key)
    assert store._accounts[ACCOUNT].users["admin"]["role"] == "admin"
    monkeypatch.setattr(agfs, "stat", stat)
    monkeypatch.setattr(agfs, "read", read)
    assert (await manager.resolve_identity(key)).role == Role.ADMIN
    assert store._cache_available is True
    assert agfs._files == original


async def test_new_reader_does_not_bootstrap_over_unreadable_accounts(state, monkeypatch):
    _, agfs, fs, _ = state
    original = dict(agfs._files)
    read = agfs.read

    def missing(path, **kwargs):
        if path == file.ACCOUNTS_PATH:
            raise AGFSNotFoundError(path)
        return read(path, **kwargs)

    monkeypatch.setattr(agfs, "read", missing)
    replica = build_api_key_manager(root_key=ROOT_KEY, viking_fs=fs)
    with pytest.raises(FailedPreconditionError, match="Account registry is missing"):
        await replica.load()
    assert agfs._files == original
    assert not agfs._leases


async def test_watched_reader_initial_load_is_read_only(state, monkeypatch):
    manager, agfs, fs, key = state
    original = dict(agfs._files)

    def fail_write_lock(*args, **kwargs):
        raise AssertionError("watched reader must not acquire a write lock during initial load")

    monkeypatch.setattr(manager._store._async_agfs, "pathlock_acquire_exact", fail_write_lock)
    reader = build_api_key_manager(
        root_key=ROOT_KEY,
        viking_fs=fs,
        account_store_watch_enabled=True,
        account_store_watch_interval_seconds=300,
    )
    await reader.load()
    try:
        assert (await reader.resolve_identity(key)).role == Role.ADMIN
        assert agfs._files == original
        assert not agfs._leases
    finally:
        await reader.close()
