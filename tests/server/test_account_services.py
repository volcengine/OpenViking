# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
import os
import threading
from datetime import datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import create_async_engine

from openviking.server.account_stores.file import FileStore
from openviking.server.account_stores.mysql import MySQLAccountStore
from openviking.server.account_stores.mysql_schema import (
    Account,
    Base,
    Credential,
    GroupMember,
    User,
)
from openviking.server.account_stores.redis_mysql import RedisMySQLAccountStore
from openviking.server.api_keys import APIKeyManager, generate_api_key
from openviking.server.identity import Role
from openviking.server.store_assembly import build_api_key_manager
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from tests.server.account_store_fakes import InMemoryAGFS


def _file_store() -> FileStore:
    return FileStore(SimpleNamespace(agfs=InMemoryAGFS()))


def test_mysql_account_schema_defines_management_list_indexes():
    indexes = {
        str(index.name): tuple(column.name for column in index.columns)
        for table_name in (Account.__tablename__, User.__tablename__)
        for index in Base.metadata.tables[table_name].indexes
    }

    assert indexes["ix_ov_accounts_account_created"] == (
        "service_name",
        "created_at",
        "account_id",
    )
    assert indexes["ix_ov_accounts_user_active_created"] == (
        "service_name",
        "account_pk",
        "deletion_task_id",
        "created_at",
        "user_pk",
    )
    assert indexes["ix_ov_accounts_user_role_active_created"] == (
        "service_name",
        "account_pk",
        "role",
        "deletion_task_id",
        "created_at",
        "user_pk",
    )


def test_mysql_account_summary_formats_naive_datetimes_as_utc():
    summary = MySQLAccountStore._account_summary(
        SimpleNamespace(
            account_id="default",
            created_at=datetime(2026, 10, 24, 1, 2, 3, 456789),
            deletion_task_id=None,
        ),
        0,
    )

    assert summary["created_at"] == "2026-10-24T01:02:03.456789+00:00"


@pytest.mark.parametrize(
    "account_id,user_id",
    [
        pytest.param("acme", "alice", id="normal"),
        pytest.param("a" * 1024, "alice", id="long-existing-account"),
        pytest.param("acme", "u" * 1024, id="long-existing-user"),
    ],
)
async def test_user_without_role_defaults_to_user_during_authentication(account_id, user_id):
    agfs = InMemoryAGFS()
    agfs.write(
        "/local/_system/accounts.json",
        json.dumps({"accounts": {account_id: {"created_at": "existing"}}}).encode(),
    )
    api_key = generate_api_key(account_id, user_id)
    agfs.write(
        f"/local/{account_id}/_system/users.json",
        json.dumps({"users": {user_id: {"key": api_key}}}).encode(),
    )
    manager = build_api_key_manager("root", SimpleNamespace(agfs=agfs))
    await manager.load()
    try:
        assert await manager.ensure_trusted_identities({account_id: {user_id}}) == {
            "created_accounts": 0,
            "created_users": 0,
        }
        identity = await manager.resolve_identity(api_key)
        assert (identity.account_id, identity.user_id, identity.role) == (
            account_id,
            user_id,
            Role.USER,
        )
    finally:
        await manager.close()


async def test_legacy_key_format_is_rejected_during_authentication():
    agfs = InMemoryAGFS()
    agfs.write(
        "/local/_system/accounts.json",
        json.dumps({"accounts": {"acme": {"created_at": "existing"}}}).encode(),
    )
    agfs.write(
        "/local/acme/_system/users.json",
        json.dumps({"users": {"alice": {"key": "legacy-key", "role": "user"}}}).encode(),
    )
    manager = build_api_key_manager("root", SimpleNamespace(agfs=agfs))
    await manager.load()
    try:
        with pytest.raises(UnauthenticatedError):
            await manager.resolve_identity("legacy-key")
    finally:
        await manager.close()


async def test_file_provider_keeps_existing_registry_paths():
    fs = SimpleNamespace(agfs=InMemoryAGFS())
    manager = build_api_key_manager("root", fs)
    await manager.load()
    try:
        key = await manager.create_account("acme", "alice")
        await manager.register_user("acme", "bob")
        assert await manager.resolve_identity(key)
        paths = set(fs.agfs._files)
        assert "/local/_system/accounts.json" in paths
        assert "/local/acme/_system/users.json" in paths
        assert "/local/acme/_system/groups.json" in paths
        assert not any("identities-v2" in path or "api-keys-v2" in path for path in paths)
    finally:
        await manager.close()


@pytest.mark.parametrize("use_outer_lease", [True, False], ids=["outer-tree", "own-exact"])
async def test_file_account_creation_reuses_optional_outer_lease(use_outer_lease):
    agfs = InMemoryAGFS()
    manager = build_api_key_manager("root", SimpleNamespace(agfs=agfs))
    await manager.load()
    acquired_paths = []
    released_leases = []
    store = manager._store
    original_acquire = store._async_agfs.pathlock_acquire_exact
    original_release = store._async_agfs.pathlock_release

    async def record_acquire(path, *args, **kwargs):
        acquired_paths.append(path)
        return await original_acquire(path, *args, **kwargs)

    async def record_release(lease):
        released_leases.append(lease)
        return await original_release(lease)

    store._async_agfs.pathlock_acquire_exact = record_acquire
    store._async_agfs.pathlock_release = record_release
    outer_lease = {"lease_ref": "account-tree"}
    try:
        await manager.create_account(
            "acme",
            "alice",
            lease_ref=outer_lease if use_outer_lease else None,
        )
    finally:
        await manager.close()

    assert acquired_paths[0] == "/local/_system/accounts.json"
    assert len(released_leases) == len(acquired_paths)
    if use_outer_lease:
        assert acquired_paths == ["/local/_system/accounts.json"]
    else:
        assert acquired_paths == [
            "/local/_system/accounts.json",
            "/local/acme/_system/users.json",
        ]


@pytest.mark.parametrize("issue_user_keys", [True, False], ids=["api-key", "trusted"])
@pytest.mark.parametrize("length", [255, 256, 1024])
async def test_file_account_creation_respects_path_component_limit(issue_user_keys, length):
    agfs = InMemoryAGFS()
    manager = build_api_key_manager(
        "root", SimpleNamespace(agfs=agfs), issue_user_keys=issue_user_keys
    )
    await manager.load()
    account_id = "a" * length
    admin_user_id = "admin"
    try:
        if length <= 255:
            key = await manager.create_account(account_id, admin_user_id)
            assert await manager.get_registered_user_role(account_id, admin_user_id) == Role.ADMIN
            if issue_user_keys:
                assert (await manager.resolve_identity(key)).account_id == account_id
            else:
                assert key is None
        else:
            before = dict(agfs._files)
            with pytest.raises(InvalidArgumentError, match="account_id.*255.*UTF-8 bytes"):
                await manager.create_account(account_id, admin_user_id)
            assert agfs._files == before
            assert await manager.get_account(account_id) is None
            assert await manager.get_user(account_id, admin_user_id) is None
    finally:
        await manager.close()


@pytest.mark.parametrize("issue_user_keys", [True, False], ids=["api-key", "trusted"])
@pytest.mark.parametrize("length", [255, 256, 1024])
async def test_file_account_creation_respects_admin_user_id_limit(
    issue_user_keys,
    length,
):
    agfs = InMemoryAGFS()
    manager = build_api_key_manager(
        "root", SimpleNamespace(agfs=agfs), issue_user_keys=issue_user_keys
    )
    await manager.load()
    account_id = "acme"
    admin_user_id = "u" * length
    try:
        if length <= 255:
            key = await manager.create_account(account_id, admin_user_id)
            assert await manager.get_registered_user_role(account_id, admin_user_id) == Role.ADMIN
            if issue_user_keys:
                assert (await manager.resolve_identity(key)).user_id == admin_user_id
        else:
            with pytest.raises(InvalidArgumentError, match="user_id.*255"):
                await manager.create_account(account_id, admin_user_id)
            assert await manager.get_account(account_id) is None
            assert await manager.get_user(account_id, admin_user_id) is None
    finally:
        await manager.close()


@pytest.mark.parametrize("issue_user_keys", [True, False], ids=["api-key", "trusted"])
@pytest.mark.parametrize("length", [255, 256, 1024])
async def test_file_user_creation_respects_user_id_limit(issue_user_keys, length):
    agfs = InMemoryAGFS()
    manager = build_api_key_manager(
        "root", SimpleNamespace(agfs=agfs), issue_user_keys=issue_user_keys
    )
    await manager.load()
    user_id = "u" * length
    try:
        await manager.create_account("acme", "alice")
        if length <= 255:
            key = await manager.register_user("acme", user_id)
            assert await manager.get_registered_user_role("acme", user_id) == Role.USER
            if issue_user_keys:
                assert (await manager.resolve_identity(key)).user_id == user_id
            else:
                assert key is None
        else:
            with pytest.raises(InvalidArgumentError, match="user_id.*255"):
                await manager.register_user("acme", user_id)
            assert await manager.get_user("acme", user_id) is None
    finally:
        await manager.close()


async def test_file_trusted_batch_rejects_oversized_account_before_any_write():
    agfs = InMemoryAGFS()
    manager = build_api_key_manager(
        "root",
        SimpleNamespace(agfs=agfs),
        issue_user_keys=False,
    )
    await manager.load()
    oversized_account_id = "a" * 256
    before = dict(agfs._files)
    try:
        with pytest.raises(InvalidArgumentError, match="account_id.*255.*UTF-8 bytes"):
            await manager.ensure_trusted_identities(
                {
                    "valid-account": {"alice"},
                    oversized_account_id: {"bob"},
                }
            )

        assert agfs._files == before
        assert await manager.get_account("valid-account") is None
        assert await manager.get_account(oversized_account_id) is None
    finally:
        await manager.close()


async def test_file_account_creation_is_atomic_when_credential_write_fails():
    class FailingAGFS(InMemoryAGFS):
        def write(self, path, content, ctx=None):
            if path == "/local/acme/_system/users.json":
                raise OSError("credential store unavailable")
            return super().write(path, content, ctx)

    store = FileStore(SimpleNamespace(agfs=FailingAGFS()))
    manager = APIKeyManager("root", store)
    await manager.load()

    with pytest.raises(OSError, match="credential store unavailable"):
        await manager.create_account("acme", "alice")
    assert await manager.get_account("acme") is None


async def test_file_user_creation_is_atomic_when_credential_write_fails():
    class FailingAGFS(InMemoryAGFS):
        fail_user_write = False

        def write(self, path, content, ctx=None):
            if self.fail_user_write and path == "/local/acme/_system/users.json":
                raise OSError("credential store unavailable")
            return super().write(path, content, ctx)

    agfs = FailingAGFS()
    store = FileStore(SimpleNamespace(agfs=agfs))
    manager = APIKeyManager("root", store)
    await manager.load()
    admin_key = await manager.create_account("acme", "alice")
    agfs.fail_user_write = True

    with pytest.raises(OSError, match="credential store unavailable"):
        await manager.register_user("acme", "bob")
    assert await manager.get_user("acme", "bob") is None
    assert (await manager.resolve_identity(admin_key)).user_id == "alice"


async def test_regenerate_key_preserves_management_not_found_errors():
    manager = build_api_key_manager("root", SimpleNamespace(agfs=InMemoryAGFS()))
    await manager.load()
    await manager.create_account("acme", "alice")

    with pytest.raises(NotFoundError, match="Account not found"):
        await manager.regenerate_key("missing", "alice")
    with pytest.raises(NotFoundError, match="User not found"):
        await manager.regenerate_key("acme", "missing")


async def test_mysql_load_creates_default_account():
    raw_mysql = os.environ.get("OV_TEST_MYSQL")
    if not raw_mysql:
        pytest.skip("Set OV_TEST_MYSQL to exercise MySQL account-store startup")

    service_name = f"test-{uuid4().hex}"
    manager = build_api_key_manager(
        "root",
        None,
        service_name=service_name,
        account_store_provider="mysql",
        account_store_params=json.loads(raw_mysql),
    )
    mysql = manager._store
    assert isinstance(mysql, MySQLAccountStore)
    engine = create_async_engine(mysql._url, **mysql._engine_options)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        await manager.load()

        accounts = await manager.list_accounts()
        assert [account["account_id"] for account in accounts] == ["default"]
        assert accounts[0]["user_count"] == 0
        await manager.close()
        await manager.load()

        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(Account)
                    .where(
                        Account.service_name == service_name,
                        Account.account_id == "default",
                    )
                )
                == 1
            )
    finally:
        await manager.close()
        async with engine.begin() as connection:
            await connection.execute(delete(Account).where(Account.service_name == service_name))
        await engine.dispose()


async def test_distributed_authentication_state_is_shared_and_service_scoped():
    """Key rotation and group deletion refresh caches within the owning service."""
    raw_mysql = os.environ.get("OV_TEST_MYSQL")
    redis_url = os.environ.get("OV_TEST_REDIS")
    if not raw_mysql or not redis_url:
        pytest.skip("Set OV_TEST_MYSQL and OV_TEST_REDIS for the distributed credential contract")

    service_name = f"test-{uuid4().hex}"
    isolated_service_name = f"test-{uuid4().hex}"
    key_prefix = f"ov:test:{uuid4().hex}"
    params = {
        "mysql": json.loads(raw_mysql),
        "cache": {
            "provider": "redis",
            "params": {
                "mode": "standalone",
                "endpoints": [redis_url],
                "key_prefix": key_prefix,
            },
        },
    }
    writer = build_api_key_manager(
        "root",
        None,
        service_name=service_name,
        account_store_provider="redis_mysql",
        account_store_params=params,
    )
    reader = build_api_key_manager(
        "root",
        None,
        service_name=service_name,
        account_store_provider="redis_mysql",
        account_store_params=params,
    )
    isolated = build_api_key_manager(
        "root",
        None,
        service_name=isolated_service_name,
        account_store_provider="redis_mysql",
        account_store_params=params,
    )
    mysql = writer._store._mysql
    engine = create_async_engine(mysql._url, **mysql._engine_options)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await writer.load()
        await reader.load()
        await isolated.load()

        old_key = await writer.create_account("acme", "alice")
        isolated_key = await isolated.create_account("acme", "alice")
        assert (await reader.resolve_identity(old_key)).role == Role.ADMIN
        assert (await isolated.resolve_identity(isolated_key)).role == Role.ADMIN
        with pytest.raises(UnauthenticatedError):
            await isolated.resolve_identity(old_key)
        with pytest.raises(UnauthenticatedError):
            await reader.resolve_identity(isolated_key)

        # Repeated rotation must bound history and invalidate an already cached key.
        for _ in range(12):
            active_key = await writer.regenerate_key("acme", "alice")
        with pytest.raises(UnauthenticatedError):
            await reader.resolve_identity(old_key)
        await writer.register_user("acme", "bob")
        await writer.create_group("acme", "engineering")
        assert await reader.get_user_group_ids("acme", "alice") == ()
        assert await reader.get_user_group_ids("acme", "bob") == ()
        with pytest.raises(NotFoundError):
            await writer.add_group_members("acme", "engineering", ["alice", "missing", "bob"])
        assert await writer.get_group_members("acme", "engineering") == []
        await writer.add_group_members("acme", "engineering", ["alice", "bob", "bob"])
        assert await reader.get_user_group_ids("acme", "alice") == ("engineering",)
        assert await reader.get_user_group_ids("acme", "bob") == ("engineering",)

        assert (
            await writer.remove_group_members(
                "acme", "engineering", ["alice", "bob", "alice", "missing"]
            )
            is True
        )
        assert await reader.get_group_members("acme", "engineering") == []
        assert await reader.get_user_group_ids("acme", "alice") == ()
        assert await reader.get_user_group_ids("acme", "bob") == ()
        assert await writer.remove_group_members("acme", "engineering", ["alice", "bob"]) is False
        await writer.add_group_members("acme", "engineering", ["alice", "bob"])

        await writer.delete_group("acme", "engineering")

        assert await reader.get_groups("acme") == []
        assert await reader.get_user_group_ids("acme", "alice") == ()
        assert await reader.get_user_group_ids("acme", "bob") == ()
        assert (await reader.resolve_identity(active_key)).user_id == "alice"
        assert (await isolated.resolve_identity(isolated_key)).role == Role.ADMIN
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(GroupMember)
                    .where(GroupMember.service_name == service_name)
                )
                == 0
            )
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(Credential)
                    .where(Credential.service_name == service_name)
                )
                == 12
            )
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(Credential)
                    .where(Credential.service_name == service_name, Credential.revoked.is_(False))
                )
                == 2
            )
    finally:
        try:

            async def clear_test_cache():
                redis_client = writer._store._redis
                if redis_client is None:
                    return
                keys = [key async for key in redis_client.scan_iter(match=f"{key_prefix}:*")]
                if keys:
                    await redis_client.delete(*keys)

            await writer._store._redis_runtime.run(clear_test_cache)
            async with engine.begin() as connection:
                await connection.execute(
                    delete(Account).where(
                        Account.service_name.in_([service_name, isolated_service_name])
                    )
                )
        finally:
            await isolated.close()
            await reader.close()
            await writer.close()
            await engine.dispose()


async def test_read_only_file_replica_refreshes_identities_without_writes(monkeypatch):
    fs = SimpleNamespace(agfs=InMemoryAGFS())
    writer = build_api_key_manager("root", fs)
    reader = build_api_key_manager("root", fs, account_store_params={"read_only": True})
    await writer.load()
    forbidden_write = AsyncMock(side_effect=AssertionError("read-only replica attempted a write"))
    for method in ("write", "ensure_parent_dirs", "pathlock_acquire_exact"):
        monkeypatch.setattr(reader._store._async_agfs, method, forbidden_write)
    await reader.load()
    try:
        key = await writer.create_account("acme", "alice")

        assert not await reader.has_user("acme", "alice")
        assert await reader.get_user_for_management("acme", "alice") == {
            "user_id": "alice",
            "role": Role.ADMIN,
        }
        assert await reader.has_user("acme", "alice")
        assert (await reader.resolve_identity(key)).role == Role.ADMIN
        rotated = await writer.regenerate_key("acme", "alice")
        await reader.get_user_for_management("acme", "alice")
        assert (await reader.resolve_identity(rotated)).user_id == "alice"
        with pytest.raises(UnauthenticatedError):
            await reader.resolve_identity(key)
        before = dict(fs.agfs._files)
        with pytest.raises(PermissionDeniedError, match="read-only"):
            await reader.register_user("acme", "bob")
        assert fs.agfs._files == before
        forbidden_write.assert_not_awaited()
    finally:
        await reader.close()
        await writer.close()


async def test_file_user_deletion_fence_survives_concurrent_role_update():
    class PausingAGFS(InMemoryAGFS):
        def __init__(self):
            super().__init__()
            self.pause_next_user_read = False
            self.user_read_started = threading.Event()
            self.allow_user_read = threading.Event()

        def read(self, path, ctx=None):
            if path == "/local/acme/_system/users.json" and self.pause_next_user_read:
                self.pause_next_user_read = False
                self.user_read_started.set()
                self.allow_user_read.wait()
            return super().read(path, ctx)

    agfs = PausingAGFS()
    store = FileStore(SimpleNamespace(agfs=agfs))
    await store.load()
    await store.create_account_with_api_key("acme", "alice", "alice-key")
    await store.create_user("acme", "bob", "user")

    agfs.pause_next_user_read = True
    set_role = asyncio.create_task(store.set_role("acme", "bob", "admin"))
    await asyncio.wait_for(asyncio.to_thread(agfs.user_read_started.wait), timeout=1)
    deleting = asyncio.create_task(
        store.begin_deletion(
            "acme",
            "bob",
            task_id="delete",
            owner_account_id="system",
            owner_user_id="system",
        )
    )
    await asyncio.sleep(0)
    agfs.allow_user_read.set()
    try:
        await set_role
        deletion, created = await deleting
    finally:
        agfs.allow_user_read.set()

    assert created
    assert deletion["task_id"] == "delete"
    assert (await store.get_deletion("acme", "bob"))["task_id"] == "delete"


async def test_file_store_keeps_global_role_immutable():
    """GLOBAL is creation-only: it can be neither a role-change source nor target."""
    store = _file_store()
    await store.load()
    await store.create_account_with_api_key("acme", "alice", "alice-key")
    await store.create_user("acme", "agent", "global")

    with pytest.raises(InvalidArgumentError, match="GLOBAL identities cannot change roles"):
        await store.set_role("acme", "agent", "user")
    with pytest.raises(InvalidArgumentError, match="user or admin"):
        await store.set_role("acme", "alice", "global")


async def test_file_store_persists_user_description_round_trip():
    """Descriptions persist to users.json and reload in legacy-compatible form."""
    agfs = InMemoryAGFS()
    store = FileStore(SimpleNamespace(agfs=agfs))
    await store.load()
    await store.create_account_with_api_key("acme", "alice", "alice-key", description="human admin")
    await store.create_user_with_api_key(
        "acme", "backup", "user", "backup-key", description="nightly backup cronjob"
    )
    # A row written without a description keeps the exact pre-feature shape.
    await store.create_user("acme", "probe", "user")

    persisted = json.loads(agfs.read("/local/acme/_system/users.json"))
    assert persisted["users"]["alice"]["description"] == "human admin"
    assert persisted["users"]["backup"]["description"] == "nightly backup cronjob"
    assert "description" not in persisted["users"]["probe"]

    peer = FileStore(SimpleNamespace(agfs=agfs))
    await peer.load()
    assert (await peer.get_user("acme", "backup"))["description"] == "nightly backup cronjob"
    assert "description" not in (await peer.get_user("acme", "probe"))
    rows = {row["user_id"]: row for row in (await peer.list_users_page("acme"))["users"]}
    assert rows["alice"]["description"] == "human admin"
    assert "description" not in rows["probe"]

    await peer.set_description("acme", "backup", "backup cronjob v2")
    assert (await peer.get_user("acme", "backup"))["description"] == "backup cronjob v2"
    # Clearing removes the key instead of persisting an explicit null.
    await peer.set_description("acme", "backup", None)
    assert "description" not in (await peer.get_user("acme", "backup"))
    persisted = json.loads(agfs.read("/local/acme/_system/users.json"))
    assert "description" not in persisted["users"]["backup"]


async def test_file_set_description_rejects_missing_and_deleting_users():
    store = _file_store()
    await store.load()
    await store.create_account_with_api_key("acme", "alice", "alice-key")
    await store.create_user("acme", "bob", "user")

    with pytest.raises(NotFoundError):
        await store.set_description("acme", "missing", "note")

    await store.begin_deletion(
        "acme",
        "bob",
        task_id="delete",
        owner_account_id="system",
        owner_user_id="system",
    )
    with pytest.raises(FailedPreconditionError, match="deletion is in progress"):
        await store.set_description("acme", "bob", "note")


def test_mysql_user_table_description_is_nullable():
    """Existing rows predate the column; it must stay optional in the schema."""
    column = User.__table__.columns["description"]
    assert column.nullable


async def test_group_deletion_preserves_users_and_persists_membership_removal():
    fs = SimpleNamespace(agfs=InMemoryAGFS())
    manager = build_api_key_manager("root", fs)
    reloaded = build_api_key_manager("root", fs)
    await manager.load()
    try:
        alice_key = await manager.create_account("acme", "alice")
        bob_key = await manager.register_user("acme", "bob")
        await manager.create_group("acme", "engineering")
        await manager.create_group("acme", "reviewers")
        await manager.add_group_members("acme", "engineering", ["alice", "bob"])
        await manager.add_group_members("acme", "reviewers", ["bob"])
        assert await manager.get_user_group_ids("acme", "bob") == (
            "engineering",
            "reviewers",
        )

        await manager.delete_group("acme", "engineering")
        await reloaded.load()

        for reader in (manager, reloaded):
            assert await reader.get_groups("acme") == [{"group_id": "reviewers", "member_count": 1}]
            assert await reader.get_user_group_ids("acme", "alice") == ()
            assert await reader.get_user_group_ids("acme", "bob") == ("reviewers",)
            assert (await reader.resolve_identity(alice_key)).user_id == "alice"
            assert (await reader.resolve_identity(bob_key)).user_id == "bob"
            with pytest.raises(NotFoundError, match="Group not found"):
                await reader.get_group_members("acme", "engineering")

        with pytest.raises(NotFoundError, match="Group not found"):
            await manager.delete_group("acme", "engineering")
    finally:
        await reloaded.close()
        await manager.close()


async def test_file_batch_group_removal_is_atomic_and_persists_indexes(monkeypatch):
    fs = SimpleNamespace(agfs=InMemoryAGFS())
    manager = build_api_key_manager("root", cast(VikingFS, fs))
    reloaded = build_api_key_manager("root", cast(VikingFS, fs))
    await manager.load()
    try:
        await manager.create_account("acme", "alice")
        await manager.register_user("acme", "bob")
        for group in ("engineering", "reviewers"):
            await manager.create_group("acme", group)
            await manager.add_group_members("acme", group, ["alice", "bob"])
        await manager.begin_deletion(
            "acme", "bob", task_id="delete-bob", owner_account_id="acme", owner_user_id="alice"
        )
        store = manager._store
        assert isinstance(store, FileStore)
        write = AsyncMock(wraps=store._write_groups_json, side_effect=OSError("store unavailable"))
        monkeypatch.setattr(store, "_write_groups_json", write)
        before = dict(fs.agfs._files)
        with pytest.raises(OSError, match="store unavailable"):
            await manager.remove_group_members("acme", "engineering", ["alice", "bob"])
        assert fs.agfs._files == before
        assert await manager.get_group_members("acme", "engineering") == ["alice", "bob"]
        for user_id in ("alice", "bob"):
            assert await manager.get_user_group_ids("acme", user_id) == ("engineering", "reviewers")

        write.reset_mock(side_effect=True)
        assert (
            await manager.remove_group_members(
                "acme", "engineering", ["bob", "alice", "missing", "bob"]
            )
            is True
        )
        assert await manager.remove_group_members("acme", "engineering", ["alice", "bob"]) is False
        write.assert_awaited_once()
        await reloaded.load()
        for reader in (manager, reloaded):
            assert await reader.get_group_members("acme", "engineering") == []
            assert await reader.get_group_members("acme", "reviewers") == ["alice", "bob"]
            for user_id in ("alice", "bob"):
                assert await reader.get_user_group_ids("acme", user_id) == ("reviewers",)
    finally:
        await reloaded.close()
        await manager.close()


async def test_file_concurrent_batch_group_removals_preserve_all_updates(monkeypatch):
    store = _file_store()
    await store.load()
    try:
        await store.create_account_with_api_key("acme", "alice", "alice-key")
        for user_id in ("bob", "carol"):
            await store.create_user("acme", user_id, "user")
        await store.create_group("acme", "engineering")
        await store.add_group_members("acme", "engineering", ["alice", "bob", "carol"])
        write = store._write_groups_json

        async def yielding_write(account_id, groups):
            await asyncio.sleep(0)
            await write(account_id, groups)

        monkeypatch.setattr(store, "_write_groups_json", yielding_write)
        assert await asyncio.gather(
            store.remove_group_members("acme", "engineering", ["alice"]),
            store.remove_group_members("acme", "engineering", ["bob", "missing"]),
        ) == [True, True]
        await store.load()
        assert await store.get_group_members("acme", "engineering") == ["carol"]
        assert await store.get_user_group_ids("acme", "alice") == ()
        assert await store.get_user_group_ids("acme", "bob") == ()
    finally:
        await store.close()


@pytest.mark.parametrize("fails", [False, True])
async def test_redis_batch_group_removal_invalidates_account_after_attempt(monkeypatch, fails):
    mysql = SimpleNamespace(
        _service_name="test-remove",
        _engine_options={},
        remove_group_members=AsyncMock(return_value=True),
    )
    store = RedisMySQLAccountStore(
        service_name="test-remove",
        mysql_store=cast(MySQLAccountStore, mysql),
        params={"cache": {"provider": "redis", "params": {}}},
    )

    bump = AsyncMock()
    monkeypatch.setattr(store, "_bump_generation", bump)
    if fails:
        mysql.remove_group_members.side_effect = NotFoundError("engineering", "group")
        with pytest.raises(NotFoundError):
            await store.remove_group_members("acme", "engineering", ["alice", "bob", "alice"])
    else:
        assert await store.remove_group_members("acme", "engineering", ["alice", "bob", "alice"])
    mysql.remove_group_members.assert_awaited_once_with("acme", "engineering", ["alice", "bob"])
    # A failed call may have committed before reporting failure; conservatively
    # invalidate the whole batch through the account generation in either case.
    bump.assert_awaited_once_with("acme", None)
