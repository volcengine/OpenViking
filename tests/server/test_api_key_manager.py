# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for APIKeyManager (openviking/server/api_keys.py)."""

import uuid

import pytest
import pytest_asyncio

from openviking.server.api_keys import APIKeyManager
from openviking.server.identity import AccountNamespacePolicy, Role
from openviking.service.core import OpenVikingService
from openviking_cli.exceptions import AlreadyExistsError, NotFoundError, UnauthenticatedError
from openviking_cli.session.user_id import UserIdentifier


def _uid() -> str:
    """Generate a unique account name to avoid cross-test collisions."""
    return f"acme_{uuid.uuid4().hex[:8]}"


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
    mgr = APIKeyManager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
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
    acct = _uid()
    key = await manager.create_account(acct, "alice")
    assert isinstance(key, str)
    assert len(key) == 64  # hex(32)

    identity = manager.resolve(key)
    assert identity.role == Role.ADMIN
    assert identity.account_id == acct
    assert identity.user_id == "alice"


async def test_create_duplicate_account_raises(manager: APIKeyManager):
    """Creating duplicate account should raise AlreadyExistsError."""
    acct = _uid()
    await manager.create_account(acct, "alice")
    with pytest.raises(AlreadyExistsError):
        await manager.create_account(acct, "bob")


async def test_delete_account(manager: APIKeyManager):
    """Deleting account should invalidate all its user keys."""
    acct = _uid()
    key = await manager.create_account(acct, "alice")
    identity = manager.resolve(key)
    assert identity.account_id == acct

    await manager.delete_account(acct)
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
    assert manager.get_account_policy("default") == AccountNamespacePolicy()


# ---- User lifecycle tests ----


async def test_register_user(manager: APIKeyManager):
    """register_user should create a user with given role."""
    acct = _uid()
    await manager.create_account(acct, "alice")
    key = await manager.register_user(acct, "bob", "user")

    identity = manager.resolve(key)
    assert identity.role == Role.USER
    assert identity.account_id == acct
    assert identity.user_id == "bob"


async def test_register_duplicate_user_raises(manager: APIKeyManager):
    """Registering duplicate user should raise AlreadyExistsError."""
    acct = _uid()
    await manager.create_account(acct, "alice")
    with pytest.raises(AlreadyExistsError):
        await manager.register_user(acct, "alice", "user")


async def test_register_user_in_nonexistent_account_raises(manager: APIKeyManager):
    """Registering user in nonexistent account should raise NotFoundError."""
    with pytest.raises(NotFoundError):
        await manager.register_user("nonexistent", "bob", "user")


async def test_remove_user(manager: APIKeyManager):
    """Removing user should invalidate their key."""
    acct = _uid()
    await manager.create_account(acct, "alice")
    bob_key = await manager.register_user(acct, "bob", "user")

    identity = manager.resolve(bob_key)
    assert identity.user_id == "bob"

    await manager.remove_user(acct, "bob")
    with pytest.raises(UnauthenticatedError):
        manager.resolve(bob_key)


async def test_regenerate_key(manager: APIKeyManager):
    """Regenerating key should invalidate old key and return new valid key."""
    acct = _uid()
    await manager.create_account(acct, "alice")
    old_key = await manager.register_user(acct, "bob", "user")

    new_key = await manager.regenerate_key(acct, "bob")
    assert new_key != old_key

    # Old key invalid
    with pytest.raises(UnauthenticatedError):
        manager.resolve(old_key)

    # New key valid
    identity = manager.resolve(new_key)
    assert identity.user_id == "bob"
    assert identity.account_id == acct


async def test_set_role(manager: APIKeyManager):
    """set_role should update user's role in both storage and index."""
    acct = _uid()
    await manager.create_account(acct, "alice")
    bob_key = await manager.register_user(acct, "bob", "user")

    assert manager.resolve(bob_key).role == Role.USER

    await manager.set_role(acct, "bob", "admin")
    assert manager.resolve(bob_key).role == Role.ADMIN


async def test_get_users(manager: APIKeyManager):
    """get_users should list all users in an account."""
    acct = _uid()
    await manager.create_account(acct, "alice")
    await manager.register_user(acct, "bob", "user")

    users = manager.get_users(acct)
    user_ids = {u["user_id"] for u in users}
    assert user_ids == {"alice", "bob"}

    roles = {u["user_id"]: u["role"] for u in users}
    assert roles["alice"] == "admin"
    assert roles["bob"] == "user"


# ---- Persistence tests ----


async def test_persistence_across_reload(manager_service):
    """Keys should survive manager reload from AGFS."""
    mgr1 = APIKeyManager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await mgr1.load()

    acct = _uid()
    key = await mgr1.create_account(acct, "alice")

    # Create new manager instance and reload
    mgr2 = APIKeyManager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await mgr2.load()

    identity = mgr2.resolve(key)
    assert identity.account_id == acct
    assert identity.user_id == "alice"
    assert identity.role == Role.ADMIN


async def test_legacy_account_without_settings_infers_user_and_agent_policy(manager_service):
    """Legacy accounts default to user-shared + agent-shared and persist the inferred policy."""
    acct = _uid()
    created_at = "2026-04-16T00:00:00+00:00"

    seed_mgr = APIKeyManager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await seed_mgr._write_json(
        "/local/_system/accounts.json", {"accounts": {acct: {"created_at": created_at}}}
    )
    await seed_mgr._write_json(
        f"/local/{acct}/_system/users.json",
        {"users": {"alice": {"role": "admin", "key": "legacy-key-alice"}}},
    )

    mgr = APIKeyManager(
        root_key=ROOT_KEY,
        viking_fs=manager_service.viking_fs,
    )
    await mgr.load()

    assert mgr.get_account_policy(acct) == AccountNamespacePolicy(
        isolate_user_scope_by_agent=False,
        isolate_agent_scope_by_user=False,
    )

    settings = await mgr._read_json(f"/local/{acct}/_system/setting.json")
    assert settings == {
        "namespace": {
            "isolate_user_scope_by_agent": False,
            "isolate_agent_scope_by_user": False,
        }
    }


# ---- Encryption tests ----


async def test_create_account_with_encryption_enabled(manager_service):
    """create_account with encryption_enabled=True should create hashed keys."""
    acct = _uid()
    mgr = APIKeyManager(
        root_key=ROOT_KEY, viking_fs=manager_service.viking_fs, encryption_enabled=True
    )
    await mgr.load()

    key = await mgr.create_account(acct, "alice")
    stored_hash = _get_stored_hash(mgr, acct, "alice")

    _print_api_key_info("创建账号", acct, "alice", key, stored_hash)

    assert isinstance(key, str)
    assert len(key) == 64  # hex(32)
    _assert_argon2_hash(stored_hash)

    identity = mgr.resolve(key)
    assert identity.role == Role.ADMIN
    assert identity.account_id == acct
    assert identity.user_id == "alice"


async def test_register_user_with_encryption_enabled(manager_service):
    """register_user with encryption_enabled=True should create hashed keys."""
    acct = _uid()
    mgr = APIKeyManager(
        root_key=ROOT_KEY, viking_fs=manager_service.viking_fs, encryption_enabled=True
    )
    await mgr.load()

    await mgr.create_account(acct, "alice")
    key = await mgr.register_user(acct, "bob", "user")
    stored_hash = _get_stored_hash(mgr, acct, "bob")

    _print_api_key_info("注册用户", acct, "bob", key, stored_hash, role="user")
    _assert_argon2_hash(stored_hash)

    identity = mgr.resolve(key)
    assert identity.role == Role.USER
    assert identity.account_id == acct
    assert identity.user_id == "bob"


async def test_regenerate_key_with_encryption_enabled(manager_service):
    """regenerate_key with encryption_enabled=True should create new hashed key."""
    acct = _uid()
    mgr = APIKeyManager(
        root_key=ROOT_KEY, viking_fs=manager_service.viking_fs, encryption_enabled=True
    )
    await mgr.load()

    await mgr.create_account(acct, "alice")
    old_key = await mgr.register_user(acct, "bob", "user")
    old_stored_hash = _get_stored_hash(mgr, acct, "bob")

    new_key = await mgr.regenerate_key(acct, "bob")
    new_stored_hash = _get_stored_hash(mgr, acct, "bob")

    _print_key_regeneration_info(
        "重新生成密钥", acct, "bob", old_key, old_stored_hash, new_key, new_stored_hash
    )

    assert new_key != old_key
    assert new_stored_hash != old_stored_hash
    _assert_argon2_hash(new_stored_hash)

    # Old key invalid
    with pytest.raises(UnauthenticatedError):
        mgr.resolve(old_key)

    # New key valid
    identity = mgr.resolve(new_key)
    assert identity.user_id == "bob"
    assert identity.account_id == acct


async def test_migrate_plaintext_keys_to_encrypted(manager_service):
    """Keys created with encryption disabled should be migrated when encryption is enabled."""
    acct = _uid()

    # First, create a key with encryption disabled
    mgr1 = APIKeyManager(
        root_key=ROOT_KEY, viking_fs=manager_service.viking_fs, encryption_enabled=False
    )
    await mgr1.load()
    key = await mgr1.create_account(acct, "alice")

    # Now, reload with encryption enabled - should migrate the key
    mgr2 = APIKeyManager(
        root_key=ROOT_KEY, viking_fs=manager_service.viking_fs, encryption_enabled=True
    )
    await mgr2.load()

    # Key should still work
    identity = mgr2.resolve(key)
    assert identity.account_id == acct
    assert identity.user_id == "alice"


async def test_persistence_with_encryption_enabled(manager_service):
    """Hashed keys should survive manager reload from AGFS."""
    mgr1 = APIKeyManager(
        root_key=ROOT_KEY, viking_fs=manager_service.viking_fs, encryption_enabled=True
    )
    await mgr1.load()

    acct = _uid()
    key = await mgr1.create_account(acct, "alice")
    stored_hash1 = _get_stored_hash(mgr1, acct, "alice")

    _print_api_key_info("持久化验证", acct, "alice", key, stored_hash1)
    print("正在重新加载管理器...\n")

    # Create new manager instance and reload
    mgr2 = APIKeyManager(
        root_key=ROOT_KEY, viking_fs=manager_service.viking_fs, encryption_enabled=True
    )
    await mgr2.load()

    stored_hash2 = _get_stored_hash(mgr2, acct, "alice")

    print(f"\n{'=' * 80}")
    print("[持久化验证 - 重新加载后]")
    print(f"重新加载后存储的 Argon2id 哈希值: {stored_hash2}")
    print(f"哈希值一致: {stored_hash1 == stored_hash2}")
    print(f"{'=' * 80}\n")

    assert stored_hash1 == stored_hash2
    _assert_argon2_hash(stored_hash2)

    identity = mgr2.resolve(key)
    assert identity.account_id == acct
    assert identity.user_id == "alice"
    assert identity.role == Role.ADMIN


def _print_api_key_info(
    test_name: str,
    account_id: str,
    user_id: str,
    original_key: str,
    stored_hash: str,
    role: str = None,
) -> None:
    """打印 API Key 相关信息的辅助函数。"""
    print(f"\n{'=' * 80}")
    print(f"[加密测试 - {test_name}]")
    print(f"账号ID: {account_id}")
    print(f"用户名: {user_id}")
    if role:
        print(f"角色: {role}")
    print(f"原始 API Key (返回给用户): {original_key}")
    print(f"存储的 Argon2id 哈希值: {stored_hash}")
    print(f"原始 Key 长度: {len(original_key)}")
    print(f"哈希值长度: {len(stored_hash)}")
    print(f"{'=' * 80}\n")


def _print_key_regeneration_info(
    test_name: str,
    account_id: str,
    user_id: str,
    old_key: str,
    old_hash: str,
    new_key: str,
    new_hash: str,
) -> None:
    """打印密钥重新生成信息的辅助函数。"""
    print(f"\n{'=' * 80}")
    print(f"[加密测试 - {test_name}]")
    print(f"账号ID: {account_id}")
    print(f"用户名: {user_id}")
    print(f"旧原始 API Key: {old_key}")
    print(f"旧存储的 Argon2id 哈希值: {old_hash}")
    print(f"新原始 API Key: {new_key}")
    print(f"新存储的 Argon2id 哈希值: {new_hash}")
    print(f"密钥已更换: {new_key != old_key}")
    print(f"哈希已更换: {new_hash != old_hash}")
    print(f"{'=' * 80}\n")


def _assert_argon2_hash(stored_hash: str) -> None:
    """验证存储的哈希值是有效的 Argon2id 格式。"""
    assert stored_hash.startswith("$argon2"), "哈希值必须是 Argon2id 格式"


def _get_stored_hash(mgr: APIKeyManager, account_id: str, user_id: str) -> str:
    """从管理器中获取用户存储的哈希值。"""
    account_info = mgr._accounts.get(account_id)
    assert account_info is not None, f"账号 {account_id} 不存在"
    assert user_id in account_info.users, f"用户 {user_id} 不存在"
    return account_info.users[user_id]["key"]
