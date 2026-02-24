# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for APIKeyManager (openviking/server/api_keys.py)."""

import pytest
import pytest_asyncio

from openviking.server.api_keys import APIKeyManager
from openviking.server.identity import Role
from openviking.service.core import OpenVikingService
from openviking_cli.exceptions import AlreadyExistsError, NotFoundError, UnauthenticatedError
from openviking_cli.session.user_id import UserIdentifier

ROOT_KEY = "test-root-key-abcdef1234567890abcdef1234567890"


@pytest_asyncio.fixture(scope="function")
async def manager_service(temp_dir):
    """OpenVikingService for APIKeyManager tests."""
    svc = OpenVikingService(
        path=str(temp_dir / "mgr_data"), user=UserIdentifier.the_default_user("mgr_user")
    )
    await svc.initialize()
    yield svc
    await svc.close()


@pytest_asyncio.fixture(scope="function")
async def manager(manager_service):
    """Fresh APIKeyManager instance, loaded."""
    mgr = APIKeyManager(root_key=ROOT_KEY, agfs_url=manager_service._agfs_url)
    await mgr.load()
    return mgr


# ---- Root key tests ----


async def test_resolve_root_key(manager: APIKeyManager):
    """Root key should resolve to ROOT role."""
    identity = manager.resolve(ROOT_KEY)
    assert identity.role == Role.ROOT
    assert identity.account_id is None
    assert identity.user_id is None


async def test_resolve_wrong_key_raises(manager: APIKeyManager):
    """Invalid key should raise UnauthenticatedError."""
    with pytest.raises(UnauthenticatedError):
        manager.resolve("wrong-key")


async def test_resolve_empty_key_raises(manager: APIKeyManager):
    """Empty key should raise UnauthenticatedError."""
    with pytest.raises(UnauthenticatedError):
        manager.resolve("")


# ---- Account lifecycle tests ----


async def test_create_account(manager: APIKeyManager):
    """create_account should create workspace + first admin user."""
    key = await manager.create_account("acme", "alice")
    assert isinstance(key, str)
    assert len(key) == 64  # hex(32)

    identity = manager.resolve(key)
    assert identity.role == Role.ADMIN
    assert identity.account_id == "acme"
    assert identity.user_id == "alice"


async def test_create_duplicate_account_raises(manager: APIKeyManager):
    """Creating duplicate account should raise AlreadyExistsError."""
    await manager.create_account("acme", "alice")
    with pytest.raises(AlreadyExistsError):
        await manager.create_account("acme", "bob")


async def test_delete_account(manager: APIKeyManager):
    """Deleting account should invalidate all its user keys."""
    key = await manager.create_account("acme", "alice")
    identity = manager.resolve(key)
    assert identity.account_id == "acme"

    await manager.delete_account("acme")
    with pytest.raises(UnauthenticatedError):
        manager.resolve(key)


async def test_delete_nonexistent_account_raises(manager: APIKeyManager):
    """Deleting nonexistent account should raise NotFoundError."""
    with pytest.raises(NotFoundError):
        await manager.delete_account("nonexistent")


async def test_default_account_exists(manager: APIKeyManager):
    """Default account should be created on load."""
    accounts = manager.get_accounts()
    assert any(a["account_id"] == "default" for a in accounts)


# ---- User lifecycle tests ----


async def test_register_user(manager: APIKeyManager):
    """register_user should create a user with given role."""
    await manager.create_account("acme", "alice")
    key = await manager.register_user("acme", "bob", "user")

    identity = manager.resolve(key)
    assert identity.role == Role.USER
    assert identity.account_id == "acme"
    assert identity.user_id == "bob"


async def test_register_duplicate_user_raises(manager: APIKeyManager):
    """Registering duplicate user should raise AlreadyExistsError."""
    await manager.create_account("acme", "alice")
    with pytest.raises(AlreadyExistsError):
        await manager.register_user("acme", "alice", "user")


async def test_register_user_in_nonexistent_account_raises(manager: APIKeyManager):
    """Registering user in nonexistent account should raise NotFoundError."""
    with pytest.raises(NotFoundError):
        await manager.register_user("nonexistent", "bob", "user")


async def test_remove_user(manager: APIKeyManager):
    """Removing user should invalidate their key."""
    await manager.create_account("acme", "alice")
    bob_key = await manager.register_user("acme", "bob", "user")

    identity = manager.resolve(bob_key)
    assert identity.user_id == "bob"

    await manager.remove_user("acme", "bob")
    with pytest.raises(UnauthenticatedError):
        manager.resolve(bob_key)


async def test_regenerate_key(manager: APIKeyManager):
    """Regenerating key should invalidate old key and return new valid key."""
    await manager.create_account("acme", "alice")
    old_key = await manager.register_user("acme", "bob", "user")

    new_key = await manager.regenerate_key("acme", "bob")
    assert new_key != old_key

    # Old key invalid
    with pytest.raises(UnauthenticatedError):
        manager.resolve(old_key)

    # New key valid
    identity = manager.resolve(new_key)
    assert identity.user_id == "bob"
    assert identity.account_id == "acme"


async def test_set_role(manager: APIKeyManager):
    """set_role should update user's role in both storage and index."""
    await manager.create_account("acme", "alice")
    bob_key = await manager.register_user("acme", "bob", "user")

    assert manager.resolve(bob_key).role == Role.USER

    await manager.set_role("acme", "bob", "admin")
    assert manager.resolve(bob_key).role == Role.ADMIN


async def test_get_users(manager: APIKeyManager):
    """get_users should list all users in an account."""
    await manager.create_account("acme", "alice")
    await manager.register_user("acme", "bob", "user")

    users = manager.get_users("acme")
    user_ids = {u["user_id"] for u in users}
    assert user_ids == {"alice", "bob"}

    roles = {u["user_id"]: u["role"] for u in users}
    assert roles["alice"] == "admin"
    assert roles["bob"] == "user"


# ---- Persistence tests ----


async def test_persistence_across_reload(manager_service):
    """Keys should survive manager reload from AGFS."""
    mgr1 = APIKeyManager(root_key=ROOT_KEY, agfs_url=manager_service._agfs_url)
    await mgr1.load()

    key = await mgr1.create_account("acme", "alice")

    # Create new manager instance and reload
    mgr2 = APIKeyManager(root_key=ROOT_KEY, agfs_url=manager_service._agfs_url)
    await mgr2.load()

    identity = mgr2.resolve(key)
    assert identity.account_id == "acme"
    assert identity.user_id == "alice"
    assert identity.role == Role.ADMIN
