# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Public account and credential contracts through APIKeyManager."""

import asyncio
import hashlib
import threading
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio

from openviking.pyagfs import AGFSNotFoundError
from openviking.server.api_keys import APIKeyManager
from openviking.server.identity import Role
from openviking.server.store_assembly import build_api_key_manager
from openviking_cli.exceptions import (
    AlreadyExistsError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from tests.server.account_store_fakes import InMemoryAGFS


def _uid() -> str:
    """Generate a unique account name to avoid cross-test collisions."""
    return f"acme_{uuid.uuid4().hex[:8]}"


def _seed_secret(user_id: str, seed: str) -> str:
    return hashlib.sha256(f"{user_id}\0{seed}".encode("utf-8")).hexdigest()


ROOT_KEY = "test-root-key-abcdef1234567890abcdef1234567890"


@pytest.fixture
def manager_service():
    return SimpleNamespace(viking_fs=SimpleNamespace(agfs=InMemoryAGFS()))


@pytest_asyncio.fixture(scope="function")
async def manager(manager_service):
    """Fresh APIKeyManager instance, loaded."""
    mgr = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await mgr.load()
    yield mgr
    await mgr.close()


async def _list_users(manager, account_id, **filters):
    page = await manager.list_users_page(account_id, **filters)
    return page["users"]


# ---- Root key tests ----


async def test_frozen_files_persist_merge_and_enforce_limit(manager, manager_service):
    """Peer writers merge under the registry guard; failed changes preserve rules."""
    from openviking_cli.exceptions import InvalidArgumentError

    await manager.create_account("frozen-files", "admin")
    await manager.register_user("frozen-files", "agent")
    peer = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await peer.load()
    try:
        await asyncio.gather(
            manager.set_frozen_file("frozen-files", "agent", "skills/a.md", True),
            peer.set_frozen_file("frozen-files", "agent", "skills/A.md", True),
        )
        assert await manager.get_frozen_files("frozen-files") == (
            "viking://user/agent/skills/A.md",
            "viking://user/agent/skills/a.md",
        )
        for i in range(8):
            await manager.set_frozen_file("frozen-files", "agent", f"memories/{i}.md", True)
        with pytest.raises(InvalidArgumentError):
            await manager.set_frozen_file("frozen-files", "agent", "skills/overflow.md", True)
        assert len(await peer.get_frozen_files("frozen-files")) == 10
    finally:
        await peer.close()


async def test_resolve_root_key(manager: APIKeyManager):
    """Root key should resolve to ROOT role."""
    identity = await manager.resolve_identity(ROOT_KEY)
    assert identity.role == Role.ROOT
    assert identity.account_id is None
    assert identity.user_id is None


@pytest.mark.parametrize("key", ["wrong-key", "", "YWNtZQ.dXNlcg.A"])
async def test_resolve_invalid_key_raises(manager: APIKeyManager, key):
    """Malformed keys should fail authentication with a format error."""
    with pytest.raises(UnauthenticatedError, match="^Invalid API key format$"):
        await manager.resolve_identity(key)


# ---- Account lifecycle tests ----


async def test_create_account(manager: APIKeyManager):
    """create_account should create workspace + first admin user."""
    from openviking.server.api_keys import parse_api_key

    assert {account["account_id"] for account in await manager.list_accounts()} == {"default"}
    acct = _uid()
    key = await manager.create_account(acct, "alice")
    account_id, user_id, secret = parse_api_key(key)
    assert (account_id, user_id) == (acct, "alice")
    assert secret

    identity = await manager.resolve_identity(key)
    assert identity.role == Role.ADMIN
    assert identity.account_id == acct
    assert identity.user_id == "alice"
    assert await manager.get_registered_user_role(acct, "alice") == Role.ADMIN


async def test_account_reads_and_mutations_cross_loops_after_waiter_cancellation(
    manager: APIKeyManager, manager_service, monkeypatch
):
    acct = _uid()
    await manager.create_account(acct, "admin")
    agfs = manager_service.viking_fs.agfs
    entered, release, readers_waiting = threading.Event(), threading.Event(), threading.Event()
    write = agfs.write

    def delayed_write(path, *args, **kwargs):
        if path == f"/local/{acct}/_system/users.json" and not entered.is_set():
            entered.set()
            if not release.wait(timeout=10):
                raise TimeoutError("account write was not released")
        return write(path, *args, **kwargs)

    async def worker_operations():
        tasks = [
            asyncio.create_task(manager.get_account(acct)),
            asyncio.create_task(manager.get_user_membership(acct, "alice")),
            asyncio.create_task(manager.register_user(acct, "bob")),
        ]
        await asyncio.sleep(0)
        readers_waiting.set()
        return await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    monkeypatch.setattr(agfs, "write", delayed_write)
    writer = asyncio.create_task(manager.register_user(acct, "alice"))
    worker = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        worker = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(worker_operations())))
        assert await asyncio.to_thread(readers_waiting.wait, 5)
        cancelled = asyncio.create_task(manager.get_account(acct))
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
    finally:
        release.set()
        results = await asyncio.gather(
            writer, *([worker] if worker is not None else []), return_exceptions=True
        )
    assert not any(isinstance(result, BaseException) for result in results)
    account, membership, bob_key = results[1]
    assert account["status"] == "active"
    assert membership == {"user_id": "alice", "role": Role.USER}
    assert (await manager.resolve_identity(bob_key)).user_id == "bob"
    assert {item["user_id"] for item in await _list_users(manager, acct)} == {
        "admin",
        "alice",
        "bob",
    }


async def test_ensure_trusted_identities_creates_missing_users_once(
    manager: APIKeyManager,
):
    """Trusted identity batches add only missing users and never mint keys."""
    acme = _uid()
    globex = _uid()

    result = await manager.ensure_trusted_identities({acme: {"alice", "bob"}, globex: {"eve"}})

    assert result == {"created_accounts": 2, "created_users": 3}
    assert await manager.get_registered_user_role(acme, "alice") == Role.USER
    assert await _list_users(manager, acme, expose_key=True) == [
        {"user_id": "alice", "role": "user"},
        {"user_id": "bob", "role": "user"},
    ]

    repeat = await manager.ensure_trusted_identities({acme: {"alice", "bob"}, globex: {"eve"}})

    assert repeat == {"created_accounts": 0, "created_users": 0}


async def test_management_writer_preserves_a_trusted_identity_from_another_instance(
    manager: APIKeyManager, manager_service
):
    """A stale manager write must merge, rather than replace, a trusted registry update."""
    acct = _uid()
    original_key = await manager.create_account(acct, "admin")
    other_account = f"{acct}_other"
    other_key = await manager.create_account(other_account, "admin")
    assert original_key[:8] == other_key[:8]

    replica = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await replica.load()

    await manager.ensure_trusted_identities({acct: {"trusted-user"}})
    rotated_key = await manager.regenerate_key(acct, "admin")
    remote_key = await manager.register_user(acct, "remote-user")
    assert await replica.has_user(acct, "trusted-user") is False

    await replica.register_user(acct, "managed-user")
    assert (await replica.resolve_identity(rotated_key)).user_id == "admin"
    assert (await replica.resolve_identity(remote_key)).user_id == "remote-user"
    with pytest.raises(UnauthenticatedError):
        await replica.resolve_identity(original_key)
    assert (await replica.resolve_identity(other_key)).account_id == other_account

    verifier = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await verifier.load()
    assert {item["user_id"] for item in await _list_users(verifier, acct)} == {
        "admin",
        "trusted-user",
        "remote-user",
        "managed-user",
    }
    await replica.delete_account(acct)
    assert (await replica.resolve_identity(other_key)).account_id == other_account


async def test_concurrent_management_registration_of_same_user_rejects_second_writer(
    manager: APIKeyManager, manager_service
):
    """The second stale instance must not replace the first user's key."""
    acct = _uid()
    await manager.create_account(acct, "admin")
    replica = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await replica.load()

    first_key = await manager.register_user(acct, "alice")

    with pytest.raises(AlreadyExistsError):
        await replica.register_user(acct, "alice")

    verifier = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await verifier.load()
    assert (await verifier.resolve_identity(first_key)).user_id == "alice"


async def test_management_registration_does_not_replace_a_trusted_user(
    manager: APIKeyManager, manager_service
):
    """A stale management writer must reject an identity created by trusted flush."""
    acct = _uid()
    await manager.create_account(acct, "admin")
    replica = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await replica.load()

    await manager.ensure_trusted_identities({acct: {"alice"}})

    with pytest.raises(AlreadyExistsError):
        await replica.register_user(acct, "alice")

    verifier = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await verifier.load()
    assert await _list_users(verifier, acct, expose_key=False) == [
        {"user_id": "admin", "role": "admin"},
        {"user_id": "alice", "role": "user"},
    ]


async def test_management_account_writer_preserves_a_trusted_account_from_another_instance(
    manager: APIKeyManager, manager_service
):
    """A stale account write must retain accounts registered by another instance."""
    replica = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await replica.load()
    trusted_account = _uid()
    managed_account = _uid()

    await manager.ensure_trusted_identities({trusted_account: {"trusted-user"}})
    await replica.create_account(managed_account, "managed-admin")

    verifier = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await verifier.load()
    assert {item["account_id"] for item in await verifier.list_accounts()} >= {
        trusted_account,
        managed_account,
    }


async def test_concurrent_management_creation_of_same_account_rejects_second_writer(
    manager: APIKeyManager, manager_service
):
    """A stale instance cannot create a second admin for an existing account."""
    replica = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await replica.load()
    account_id = _uid()

    first_key = await manager.create_account(account_id, "alice")

    with pytest.raises(AlreadyExistsError):
        await replica.create_account(account_id, "bob")

    verifier = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await verifier.load()
    assert (await verifier.resolve_identity(first_key)).user_id == "alice"
    assert await _list_users(verifier, account_id, expose_key=False) == [
        {"user_id": "alice", "role": "admin"}
    ]


async def test_delete_account(manager: APIKeyManager):
    """The durable account fence revokes keys until its owner finishes."""
    from openviking_cli.exceptions import FailedPreconditionError

    acct = _uid()
    key = await manager.create_account(acct, "alice")
    assert (await manager.resolve_identity(key)).account_id == acct
    deletion, created = await manager.begin_deletion(
        acct, None, task_id="delete-account-1", owner_account_id="_system", owner_user_id="root"
    )
    assert created
    with pytest.raises(UnauthenticatedError, match="Invalid API key"):
        await manager.resolve_identity(key)
    assert await manager.get_user_key_fingerprint(acct, "alice") is None
    with pytest.raises(AlreadyExistsError):
        await manager.create_account(acct, "bob")
    with pytest.raises(FailedPreconditionError):
        await manager.register_user(acct, "bob")
    with pytest.raises(FailedPreconditionError):
        await manager.regenerate_key(acct, "alice")
    await manager.ensure_trusted_identities({acct: {"trusted-user"}})
    assert not await manager.has_user(acct, "trusted-user")

    assert await manager.get_deletion(acct) == deletion
    assert await manager.finish_deletion(acct, None, "stale-task") is False
    replacement = await manager.replace_deletion_task(
        acct,
        None,
        expected_task_id="delete-account-1",
        task_id="delete-account-2",
        owner_account_id="_system",
        owner_user_id="root",
    )
    assert replacement["task_id"] == "delete-account-2"
    assert await manager.finish_deletion(acct, None, "delete-account-1") is False
    assert await manager.finish_deletion(acct, None, "delete-account-2") is True
    assert not await manager.has_user(acct, "alice")
    assert await manager.get_deletion(acct) is None


async def test_recreated_account_does_not_restore_deleted_users(manager: APIKeyManager):
    """A same-name account starts with only its newly created admin."""
    acct = _uid()
    old_key = await manager.create_account(acct, "old-admin")

    await manager.delete_account(acct)
    new_key = await manager.create_account(acct, "new-admin")

    with pytest.raises(UnauthenticatedError):
        await manager.resolve_identity(old_key)
    assert (await manager.resolve_identity(new_key)).user_id == "new-admin"
    assert await _list_users(manager, acct, expose_key=False) == [
        {"user_id": "new-admin", "role": "admin"}
    ]


async def test_delete_nonexistent_account_raises(manager: APIKeyManager):
    """Deleting nonexistent account should raise NotFoundError."""
    with pytest.raises(NotFoundError):
        await manager.delete_account("nonexistent")


@pytest.mark.parametrize("operation", ["create", "delete", "trusted"])
async def test_account_mutation_preserves_registry_when_baseline_is_missing(
    manager: APIKeyManager, manager_service, monkeypatch: pytest.MonkeyPatch, operation: str
):
    survivor = _uid()
    key = await manager.create_account(survivor, "alice")
    target = _uid()
    if operation == "delete":
        await manager.create_account(target, "bob")
    agfs = manager_service.viking_fs.agfs
    read = agfs.read

    def missing_registry(path, *args, **kwargs):
        if path == "/local/_system/accounts.json":
            raise AGFSNotFoundError("injected missing registry read")
        return read(path, *args, **kwargs)

    async def mutate():
        if operation == "create":
            return await manager.create_account(target, "bob")
        if operation == "delete":
            return await manager.delete_account(target)
        return await manager.ensure_trusted_identities({target: {"bob"}})

    with monkeypatch.context() as patch:
        patch.setattr(agfs, "read", missing_registry)
        with pytest.raises(FailedPreconditionError, match="Account registry"):
            await mutate()
    assert (await manager.resolve_identity(key)).account_id == survivor

    # Check durable state both after failure and after a successful retry.
    for retried in (False, True):
        if retried:
            await mutate()
        verifier = build_api_key_manager(ROOT_KEY, manager_service.viking_fs)
        await verifier.load()
        try:
            assert (await verifier.resolve_identity(key)).account_id == survivor
            target_exists = (operation == "delete") != retried
            assert await verifier.has_user(target, "bob") == target_exists
            assert (await verifier.get_account(target) is not None) == target_exists
        finally:
            await verifier.close()


# ---- User lifecycle tests ----


async def test_register_user_in_nonexistent_account_raises(manager: APIKeyManager):
    """Registering user in nonexistent account should raise NotFoundError."""
    with pytest.raises(NotFoundError):
        await manager.register_user("nonexistent", "bob", "user")


async def test_user_deletion_fence_revokes_key_and_rejects_stale_finish(
    manager: APIKeyManager,
):
    acct = _uid()
    await manager.create_account(acct, "alice")
    bob_key = await manager.register_user(acct, "bob", "user")

    deletion, created = await manager.begin_deletion(
        acct,
        "bob",
        task_id="delete-1",
        owner_account_id=acct,
        owner_user_id="alice",
    )

    assert created is True
    assert deletion["task_id"] == "delete-1"
    assert await manager.get_deletion(acct, "bob") == deletion
    with pytest.raises(UnauthenticatedError, match="Invalid API key"):
        await manager.resolve_identity(bob_key)
    with pytest.raises(AlreadyExistsError):
        await manager.register_user(acct, "bob", "user")
    assert await manager.finish_deletion(acct, "bob", "stale") is False
    assert await manager.has_user(acct, "bob")
    assert await manager.finish_deletion(acct, "bob", "delete-1") is True
    assert not await manager.has_user(acct, "bob")

    new_key = await manager.register_user(acct, "bob", "user")
    assert await manager.finish_deletion(acct, "bob", "delete-1") is False
    assert (await manager.resolve_identity(new_key)).user_id == "bob"


async def test_account_deletion_fence_overrides_user_deletion_fence(manager: APIKeyManager):
    acct = _uid()
    await manager.create_account(acct, "alice")
    await manager.register_user(acct, "bob")
    user_deletion, user_created = await manager.begin_deletion(
        acct,
        "bob",
        task_id="delete-user",
        owner_account_id=acct,
        owner_user_id="alice",
    )
    account_deletion, account_created = await manager.begin_deletion(
        acct,
        None,
        task_id="delete-account",
        owner_account_id=acct,
        owner_user_id="alice",
    )

    assert user_created
    assert account_created
    assert user_deletion["task_id"] == "delete-user"
    assert await manager.get_deletion(acct, "bob") == account_deletion


async def test_regenerate_key(manager: APIKeyManager):
    account = _uid()
    assert await manager.get_user_key_fingerprint(account, "bob") is None
    await manager.create_account(account, "alice")
    assert await manager.get_user_key_fingerprint(account, "missing") is None

    old_key = await manager.register_user(account, "bob", Role.USER)
    original_fingerprint = hashlib.sha256(old_key.encode()).hexdigest()
    assert await manager.get_user_key_fingerprint(account, "bob") == original_fingerprint

    new_key = await manager.regenerate_key(account, "bob")
    assert new_key != old_key
    assert (
        await manager.get_user_key_fingerprint(account, "bob")
        == hashlib.sha256(new_key.encode()).hexdigest()
    )
    with pytest.raises(UnauthenticatedError):
        await manager.resolve_identity(old_key)
    identity = await manager.resolve_identity(new_key)
    assert (identity.account_id, identity.user_id, identity.role) == (account, "bob", Role.USER)

    await manager.begin_deletion(
        account, "bob", task_id="delete-bob", owner_account_id=account, owner_user_id="alice"
    )
    assert await manager.get_user_key_fingerprint(account, "bob") is None


@pytest.mark.parametrize("hashing", [False, True])
@pytest.mark.parametrize("mutation", ["reset", "revoke"])
async def test_conditional_rotation_checks_persisted_key(manager_service, hashing, mutation):
    from openviking.server.api_keys import generate_api_key

    managers = [
        build_api_key_manager(
            root_key=ROOT_KEY,
            viking_fs=manager_service.viking_fs,
            api_key_hashing_enabled=hashing,
        )
        for _ in range(2)
    ]
    writer, stale = managers
    try:
        await writer.load()
        account = _uid()
        await writer.create_account(account, "owner")
        initial_key = await writer.register_user(account, "alice")
        old_key = await writer.regenerate_key(account, "alice", expected_api_key=initial_key)
        await stale.load()
        if mutation == "reset":
            reset_key = await writer.regenerate_key(account, "alice")
        else:
            await writer._store.revoke_active_api_keys(account, "alice")
        rejected_key = generate_api_key(account, "alice")
        # This store still has the old credential in memory. Its conditional
        # write must validate the persisted registry under the shared lease.
        with pytest.raises(UnauthenticatedError):
            await stale._store.replace_active_user_api_key(
                account, "alice", rejected_key, expected_api_key=old_key
            )
        await stale.load()
        for key in (old_key, rejected_key):
            with pytest.raises(UnauthenticatedError):
                await stale.resolve_identity(key)
        if mutation == "reset":
            assert (await stale.resolve_identity(reset_key)).user_id == "alice"
    finally:
        for manager in managers:
            await manager.close()


async def test_set_role(manager: APIKeyManager):
    """An issued key keeps its identity and sees the registered role change."""
    from openviking.server.api_keys import parse_api_key

    acct = _uid()
    await manager.create_account(acct, "alice")
    key = await manager.register_user(acct, "bob", "user")
    assert parse_api_key(key)[:2] == (acct, "bob")
    identity = await manager.resolve_identity(key)
    assert (identity.account_id, identity.user_id, identity.role) == (acct, "bob", Role.USER)
    assert await manager.get_registered_user_role(acct, "bob") == Role.USER

    await manager.set_role(acct, "bob", "admin")
    with pytest.raises(PermissionDeniedError, match="server.root_api_key"):
        await manager.set_role(acct, "bob", Role.ROOT)
    assert (await manager.resolve_identity(key)).role == Role.ADMIN
    assert await manager.get_registered_user_role(acct, "bob") == Role.ADMIN


async def test_get_users_pagination_and_ordering(manager: APIKeyManager):
    acct = _uid()
    await manager.create_account(acct, "alice")
    for user in ("dave", "alan", "carol"):
        await manager.register_user(acct, user, "user")

    users = await _list_users(manager, acct)
    assert [(u["user_id"], u["role"]) for u in users] == [
        ("alice", Role.ADMIN),
        ("dave", Role.USER),
        ("alan", Role.USER),
        ("carol", Role.USER),
    ]
    for page, expected in enumerate((["alice", "dave"], ["alan", "carol"], []), start=1):
        assert [
            u["user_id"] for u in await _list_users(manager, acct, limit=2, page=page)
        ] == expected
    for pattern, expected in (("al*", ["alice", "alan"]), ("alice", ["alice"]), ("zzz*", [])):
        assert [
            u["user_id"] for u in await _list_users(manager, acct, name_filter=pattern)
        ] == expected

    await manager.begin_deletion(
        acct,
        "alan",
        task_id="delete-alan",
        owner_account_id=acct,
        owner_user_id="alice",
    )
    assert [u["user_id"] for u in await _list_users(manager, acct, name_filter="al*")] == ["alice"]


async def test_get_accounts_pagination_and_ordering(manager: APIKeyManager):
    prefix = _uid()
    ids = [f"{prefix}_{suffix}" for suffix in ("delta", "alpha", "charlie", "bravo")]
    unrelated = _uid()
    for account_id in [*ids, unrelated]:
        await manager.create_account(account_id, "u")

    all_ids = [account["account_id"] for account in await manager.list_accounts()]
    assert all_ids == ["default", *ids, unrelated]
    assert [
        account["account_id"] for account in await manager.list_accounts(name_filter=ids[0])
    ] == ids[:1]
    assert [
        account["account_id"] for account in await manager.list_accounts(name_filter=f"{prefix}_*")
    ] == ids
    for page, expected in enumerate((ids[:2], ids[2:], []), start=1):
        accounts = await manager.list_accounts(name_filter=f"{prefix}_*", limit=2, page=page)
        assert [account["account_id"] for account in accounts] == expected


async def test_user_and_group_persistence_across_reload(manager_service):
    """Batch membership is atomic, idempotent, durable, and removed with the user."""
    mgr1 = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await mgr1.load()

    acct = _uid()
    key = await mgr1.create_account(acct, "alice")
    await mgr1.register_user(acct, "bob")
    created_group = await mgr1.create_group(acct, "engineering")
    assert created_group == {"group_id": "engineering", "member_count": 0}
    group_id = created_group["group_id"]
    assert await mgr1.add_group_members(acct, group_id, ["bob"]) is True

    await mgr1.create_account(_uid(), "outsider")
    with pytest.raises(NotFoundError):
        await mgr1.add_group_members(acct, group_id, ["alice", "outsider"])
    assert await mgr1.get_group_members(acct, group_id) == ["bob"]
    assert await mgr1.get_user_group_ids(acct, "alice") == ()

    members = ["alice", "bob", "bob"]
    assert await mgr1.add_group_members(acct, group_id, members) is True
    assert await mgr1.add_group_members(acct, group_id, members) is True
    assert await mgr1.get_group_members(acct, group_id) == ["alice", "bob"]
    assert await mgr1.get_groups(acct) == [{"group_id": group_id, "member_count": 2}]

    mgr2 = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await mgr2.load()

    identity = await mgr2.resolve_identity(key)
    assert identity.account_id == acct
    assert identity.user_id == "alice"
    assert identity.role == Role.ADMIN
    assert await mgr2.get_group_members(acct, group_id) == ["alice", "bob"]
    assert await mgr2.get_user_group_ids(acct, "alice") == (group_id,)
    assert await mgr2.get_user_group_ids(acct, "bob") == (group_id,)

    await mgr2.begin_deletion(
        acct,
        "bob",
        task_id="delete-bob",
        owner_account_id=acct,
        owner_user_id="alice",
    )
    assert await mgr2.remove_group_members(acct, group_id, ["alice", "missing", "alice"]) is True
    assert await mgr2.remove_group_members(acct, group_id, ["alice", "missing"]) is False
    with pytest.raises(FailedPreconditionError):
        await mgr2.add_group_members(acct, group_id, ["alice", "bob"])
    assert await mgr2.get_group_members(acct, group_id) == ["bob"]
    assert await mgr2.get_user_group_ids(acct, "alice") == ()
    await mgr2.finish_deletion(acct, "bob", "delete-bob")

    mgr3 = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    await mgr3.load()
    assert await mgr3.get_user_group_ids(acct, "bob") == ()
    assert await mgr3.get_group_members(acct, group_id) == []


# ---- API Key format tests ----


async def test_seeded_api_keys_are_predictable(manager: APIKeyManager):
    from openviking.server.api_keys import parse_api_key

    seed = "client-known-seed"
    acct1 = _uid()
    acct2 = _uid()

    key1 = await manager.create_account(acct1, "alice", seed=seed)
    key2 = await manager.create_account(acct2, "alice", seed=seed)

    account1, user1, secret1 = parse_api_key(key1)
    account2, user2, secret2 = parse_api_key(key2)

    assert account1 == acct1
    assert account2 == acct2
    assert user1 == user2 == "alice"
    assert secret1 == secret2 == _seed_secret("alice", seed)
    assert key1 != key2

    old_key = await manager.register_user(acct1, "bob", "user", seed=seed)
    assert parse_api_key(old_key) == (acct1, "bob", _seed_secret("bob", seed))
    new_key = await manager.regenerate_key(acct1, "bob", seed="rotated-seed")
    assert parse_api_key(new_key) == (acct1, "bob", _seed_secret("bob", "rotated-seed"))
    with pytest.raises(UnauthenticatedError):
        await manager.resolve_identity(old_key)
    identity = await manager.resolve_identity(new_key)
    assert (identity.account_id, identity.user_id, identity.role) == (acct1, "bob", Role.USER)


async def test_seed_must_not_be_empty(manager: APIKeyManager):
    acct = _uid()
    with pytest.raises(InvalidArgumentError):
        await manager.create_account(acct, "alice", seed="")


async def test_is_valid_format_key_validation():
    """Test is_valid_format_key correctly identifies key format."""
    from openviking.server.api_keys import generate_api_key, is_valid_format_key, parse_api_key

    # Valid API key format
    valid_key = generate_api_key("account", "user")
    assert is_valid_format_key(valid_key)

    # Bare single-segment keys are not accepted
    assert not is_valid_format_key("a" * 64)

    # Empty string
    assert not is_valid_format_key("")

    # Wrong number of segments
    assert not is_valid_format_key("onepart")
    assert not is_valid_format_key("two.parts")
    assert not is_valid_format_key("too.many.parts.here")
    with pytest.raises(ValueError, match="^Invalid API key format$"):
        parse_api_key("two.parts")


# ---- Registered user role ----


@pytest.mark.parametrize("delete_account", [False, True])
async def test_registered_identity_remains_readable_but_unusable_during_deletion(
    manager: APIKeyManager, delete_account: bool
):
    acct = _uid()
    await manager.create_account(acct, "alice")
    key = await manager.register_user(acct, "bob", Role.USER)
    assert (await manager.resolve_oauth_identity(acct, "bob", Role.USER)).role == Role.USER

    await manager.begin_deletion(
        acct,
        None if delete_account else "bob",
        task_id="delete-identity",
        owner_account_id=acct,
        owner_user_id="alice",
    )

    assert await manager.get_registered_user_role(acct, "bob") == Role.USER
    with pytest.raises(
        UnauthenticatedError, match="Account identity does not exist or is being deleted"
    ):
        await manager.resolve_oauth_identity(acct, "bob", Role.USER)
    with pytest.raises(UnauthenticatedError, match="Invalid API key"):
        await manager.resolve_identity(key)
    target = "Account" if delete_account else "User"
    with pytest.raises(FailedPreconditionError, match=f"{target} deletion is in progress") as exc:
        await manager.regenerate_key(acct, "bob")
    assert exc.value.details == {"task_id": "delete-identity"}


@pytest.mark.parametrize("missing_account", [False, True])
async def test_missing_identity_fails_authentication_and_management_separately(
    manager: APIKeyManager, missing_account: bool
):
    acct = _uid()
    if not missing_account:
        await manager.create_account(acct, "alice")

    assert await manager.get_registered_user_role(acct, "nobody") is None
    with pytest.raises(
        UnauthenticatedError, match="Account identity does not exist or is being deleted"
    ):
        await manager.resolve_oauth_identity(acct, "nobody", Role.USER)
    resource = "Account" if missing_account else "User"
    with pytest.raises(NotFoundError, match=f"{resource} not found"):
        await manager.regenerate_key(acct, "nobody")


# ---- Read-replica refresh contract ----


async def test_explicit_fresh_list_bypasses_watcher_interval(manager_service):
    writer = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    reader = build_api_key_manager(
        root_key=ROOT_KEY,
        viking_fs=manager_service.viking_fs,
        account_store_watch_enabled=True,
        account_store_watch_interval_seconds=300,
    )
    await writer.load()
    await reader.load()
    acct = _uid()
    try:
        await writer.create_account(acct, "alice")
        await writer.register_user(acct, "bob")

        page = await reader.list_users_page(acct, limit=None, fresh=True)

        assert {user["user_id"] for user in page["users"]} == {"alice", "bob"}
    finally:
        await reader.close()
        await writer.close()


async def test_watched_reader_observes_registration_and_deletion(manager_service, monkeypatch):
    writer = build_api_key_manager(root_key=ROOT_KEY, viking_fs=manager_service.viking_fs)
    reader = build_api_key_manager(
        root_key=ROOT_KEY,
        viking_fs=manager_service.viking_fs,
        account_store_watch_enabled=True,
        account_store_watch_interval_seconds=0.01,
    )
    account = _uid()
    observing = asyncio.Event()
    compute_signature = reader._store._compute_store_signature

    async def signature_after_startup():
        signature = await compute_signature()
        observing.set()
        return signature

    monkeypatch.setattr(reader._store, "_compute_store_signature", signature_after_startup)
    try:
        await writer.load()
        await writer.create_account(account, "alice")
        await reader.load()
        await asyncio.wait_for(observing.wait(), timeout=2)
        key = await writer.register_user(account, "bob")

        async def wait_for_identity(available):
            while True:
                try:
                    identity = await reader.resolve_identity(key)
                except UnauthenticatedError:
                    identity = None
                if (identity is not None) == available:
                    return identity
                await asyncio.sleep(0.01)

        identity = await asyncio.wait_for(wait_for_identity(True), timeout=2)
        assert (identity.account_id, identity.user_id) == (account, "bob")

        await writer.begin_deletion(
            account, "bob", task_id="delete-bob", owner_account_id=account, owner_user_id="alice"
        )
        assert await writer.finish_deletion(account, "bob", "delete-bob")
        await asyncio.wait_for(wait_for_identity(False), timeout=2)
    finally:
        await reader.close()
        await writer.close()
