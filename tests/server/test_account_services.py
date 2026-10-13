# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from openviking.server.api_keys import APIKeyManager
from openviking.server.api_keys.legacy import FileStore
from openviking.server.identity import Role
from openviking.server.store_assembly import build_api_key_manager
from openviking_cli.exceptions import (
    FailedPreconditionError,
    NotFoundError,
)
from tests.server.account_store_fakes import InMemoryAGFS


def _file_store() -> FileStore:
    return FileStore(SimpleNamespace(agfs=InMemoryAGFS()))


async def test_legacy_user_without_role_defaults_to_user_during_authentication():
    agfs = InMemoryAGFS()
    agfs.write(
        "/local/_system/accounts.json",
        json.dumps({"accounts": {"acme": {"created_at": "legacy"}}}).encode(),
    )
    agfs.write(
        "/local/acme/_system/users.json",
        json.dumps({"users": {"alice": {"key": "legacy-key"}}}).encode(),
    )
    manager = build_api_key_manager("root", SimpleNamespace(agfs=agfs))
    await manager.load()
    try:
        identity = await manager.resolve_identity("legacy-key")
        assert (identity.account_id, identity.user_id, identity.role) == (
            "acme",
            "alice",
            Role.USER,
        )
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


async def test_file_store_allows_only_one_active_credential_per_user():
    store = _file_store()
    await store.load()
    await store.create_account_with_api_key("acme", "alice", "initial-key")

    await store.replace_active_user_api_key("acme", "alice", "first-key")
    await store.replace_active_user_api_key("acme", "alice", "second-key")
    assert await store.verify_api_key("first-key") is None
    active = await store.verify_api_key("second-key")
    assert active == ("acme", "alice")


async def test_file_management_user_read_refreshes_only_explicitly():
    fs = SimpleNamespace(agfs=InMemoryAGFS())
    writer = build_api_key_manager("root", fs)
    reader = build_api_key_manager("root", fs)
    await writer.load()
    await reader.load()
    try:
        await writer.create_account("acme", "alice")

        assert not await reader.has_user("acme", "alice")
        assert await reader.get_user_for_management("acme", "alice") == {
            "user_id": "alice",
            "role": Role.ADMIN,
        }
        assert await reader.has_user("acme", "alice")
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


async def test_file_store_rejects_deleting_nonempty_group():
    store = _file_store()
    await store.load()
    await store.create_account_with_api_key("acme", "alice", "alice-key")
    await store.create_group("acme", "engineering")
    await store.add_group_member("acme", "engineering", "alice")

    with pytest.raises(FailedPreconditionError, match="Group must be empty"):
        await store.delete_group("acme", "engineering")
    assert await store.get_group_members("acme", "engineering") == ["alice"]
