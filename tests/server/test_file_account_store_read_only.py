# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Readers observe writer identities without acquiring write leases or changing the registry."""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from openviking.server.identity import Role
from openviking.server.store_assembly import build_api_key_manager
from openviking_cli.exceptions import (
    FailedPreconditionError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from tests.server.account_store_fakes import InMemoryAGFS

ROOT_KEY = "test-root-key"
ACCOUNT = "read_test"


@pytest_asyncio.fixture
async def state():
    agfs = InMemoryAGFS()
    fs = SimpleNamespace(agfs=agfs)
    manager = build_api_key_manager(root_key=ROOT_KEY, viking_fs=fs)
    await manager.load()
    key = await manager.create_account(ACCOUNT, "admin")
    try:
        yield manager, agfs, fs, key
    finally:
        await manager.close()


async def test_read_only_replica_loads_and_observes_writer_without_filesystem_writes(
    state, monkeypatch
):
    writer, agfs, fs, key = state
    reader = build_api_key_manager(
        root_key=ROOT_KEY,
        viking_fs=fs,
        account_store_params={"read_only": True},
        account_store_watch_enabled=True,
        account_store_watch_interval_seconds=0.01,
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("reader attempted a filesystem mutation or file lock")

    for method in ("pathlock_acquire_exact", "write", "ensure_parent_dirs"):
        monkeypatch.setattr(reader._store._async_agfs, method, forbidden)
    before = dict(agfs._files)

    async def wait_for_key(api_key):
        for _ in range(100):
            try:
                return await reader.resolve_identity(api_key)
            except UnauthenticatedError:
                await asyncio.sleep(0.01)
        pytest.fail("read-only replica did not refresh identities from the writer")

    try:
        await reader.load()
        assert (await reader.resolve_identity(key)).role == Role.ADMIN
        assert agfs._files == before
        page = await reader.list_users_page(ACCOUNT, fresh=True)
        assert {u["user_id"] for u in page["users"]} == {"admin"}

        new_key = await writer.register_user(ACCOUNT, "writer-added")
        assert (await wait_for_key(new_key)).user_id == "writer-added"

        rotated_key = await writer.regenerate_key(ACCOUNT, "writer-added")
        assert (await wait_for_key(rotated_key)).user_id == "writer-added"
        with pytest.raises(UnauthenticatedError):
            await reader.resolve_identity(new_key)
    finally:
        await reader.close()


@pytest.mark.parametrize("operation", ["account", "user", "group", "key"])
async def test_read_only_replica_rejects_identity_mutations_on_writable_storage(state, operation):
    _, agfs, fs, _ = state
    reader = build_api_key_manager(
        root_key=ROOT_KEY, viking_fs=fs, account_store_params={"read_only": True}
    )
    await reader.load()
    before = dict(agfs._files)
    try:
        with pytest.raises(PermissionDeniedError, match="read-only"):
            if operation == "account":
                await reader.create_account("must_not_exist", "admin")
            elif operation == "user":
                await reader.register_user(ACCOUNT, "must_not_exist")
            elif operation == "group":
                await reader._store.create_group(ACCOUNT, "must_not_exist")
            else:
                await reader.regenerate_key(ACCOUNT, "admin")
        assert agfs._files == before
        assert not agfs._leases
    finally:
        await reader.close()


async def test_read_only_replica_never_bootstraps_missing_registry():
    agfs = InMemoryAGFS()
    reader = build_api_key_manager(
        root_key=ROOT_KEY,
        viking_fs=SimpleNamespace(agfs=agfs),
        account_store_params={"read_only": True},
    )
    with pytest.raises(FailedPreconditionError):
        await reader.load()
    assert agfs._files == {}
    assert not agfs._leases
