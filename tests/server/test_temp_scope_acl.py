# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for temp-scope access control."""

import pytest

from openviking.pyagfs import get_binding_client
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking.utils.agfs_utils import RagfsBindingConfig, mount_agfs_backend
from openviking_cli.exceptions import NotFoundError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.agfs_config import AGFSConfig


@pytest.fixture
def viking_fs(tmp_path):
    client_type, _ = get_binding_client()
    assert client_type is not None
    config = RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
    client = client_type(None, config=config.to_binding_dict())
    try:
        mount_agfs_backend(client, config)
        yield VikingFS(agfs=client)
    finally:
        client.close()


@pytest.mark.asyncio
async def test_temp_scope_isolated_between_users_in_same_account(viking_fs):
    owner_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice"),
        role=Role.USER,
    )
    other_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="bob"),
        role=Role.USER,
    )

    temp_uri = viking_fs.create_temp_uri(ctx=owner_ctx)
    secret_uri = f"{temp_uri}/secret.txt"

    await viking_fs.mkdir(temp_uri, exist_ok=True, ctx=owner_ctx)
    await viking_fs.write(secret_uri, "owner secret", ctx=owner_ctx)

    assert (await viking_fs.read(secret_uri, ctx=owner_ctx)).decode("utf-8") == "owner secret"

    with pytest.raises(PermissionDeniedError):
        await viking_fs.read(secret_uri, ctx=other_ctx)

    with pytest.raises(PermissionDeniedError):
        await viking_fs.write(secret_uri, "tampered", ctx=other_ctx)


@pytest.mark.asyncio
async def test_temp_scope_user_id_matching_legacy_pattern_stays_isolated(viking_fs):
    owner_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="04011234_abcdef"),
        role=Role.USER,
    )
    other_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="bob"),
        role=Role.USER,
    )

    temp_uri = viking_fs.create_temp_uri(ctx=owner_ctx)
    secret_uri = f"{temp_uri}/secret.txt"

    await viking_fs.mkdir(temp_uri, exist_ok=True, ctx=owner_ctx)
    await viking_fs.write(secret_uri, "owner secret", ctx=owner_ctx)

    assert temp_uri.startswith("viking://temp/04011234_abcdef/")
    with pytest.raises(PermissionDeniedError):
        await viking_fs.read(secret_uri, ctx=other_ctx)


@pytest.mark.asyncio
async def test_temp_root_listing_only_shows_callers_own_entries(viking_fs):
    alice_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice"),
        role=Role.USER,
    )
    bob_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="bob"),
        role=Role.USER,
    )

    alice_temp_uri = viking_fs.create_temp_uri(ctx=alice_ctx)
    bob_temp_uri = viking_fs.create_temp_uri(ctx=bob_ctx)

    await viking_fs.mkdir(alice_temp_uri, exist_ok=True, ctx=alice_ctx)
    await viking_fs.write(f"{alice_temp_uri}/alice.txt", "alice", ctx=alice_ctx)

    await viking_fs.mkdir(bob_temp_uri, exist_ok=True, ctx=bob_ctx)
    await viking_fs.write(f"{bob_temp_uri}/bob.txt", "bob", ctx=bob_ctx)

    alice_entries = await viking_fs.tree("viking://temp", output="original", ctx=alice_ctx)
    bob_entries = await viking_fs.tree("viking://temp", output="original", ctx=bob_ctx)

    alice_uris = {entry["uri"] for entry in alice_entries}
    bob_uris = {entry["uri"] for entry in bob_entries}

    assert any(uri.startswith(alice_temp_uri) for uri in alice_uris)
    assert not any(uri.startswith(bob_temp_uri) for uri in alice_uris)

    assert any(uri.startswith(bob_temp_uri) for uri in bob_uris)
    assert not any(uri.startswith(alice_temp_uri) for uri in bob_uris)


@pytest.mark.asyncio
async def test_temp_root_destructive_operations_are_blocked_for_non_root_users(viking_fs):
    alice_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice"),
        role=Role.USER,
    )

    with pytest.raises(PermissionDeniedError):
        await viking_fs.rm("viking://temp", recursive=True, ctx=alice_ctx)

    with pytest.raises(PermissionDeniedError):
        await viking_fs.delete_temp("viking://temp", ctx=alice_ctx)


@pytest.mark.asyncio
async def test_delete_temp_removes_repository_tasks_directory(viking_fs):
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice"),
        role=Role.ROOT,
    )
    temp_uri = viking_fs.create_temp_uri(ctx=ctx)
    tasks_file_uri = f"{temp_uri}/repository/src/tasks/task-registry.ts"

    await viking_fs.mkdir(f"{temp_uri}/repository/src/tasks", exist_ok=True, ctx=ctx)
    await viking_fs.write(tasks_file_uri, "task registry", ctx=ctx)

    await viking_fs.delete_temp(temp_uri, ctx=ctx)

    with pytest.raises(NotFoundError):
        await viking_fs.read(tasks_file_uri, ctx=ctx)


@pytest.mark.asyncio
async def test_legacy_temp_trees_remain_accessible_for_same_account_users(viking_fs):
    alice_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice"),
        role=Role.USER,
    )
    bob_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="bob"),
        role=Role.USER,
    )

    legacy_temp_uri = "viking://temp/04011234_abcdef"
    legacy_secret_uri = f"{legacy_temp_uri}/legacy.txt"

    await viking_fs.mkdir(legacy_temp_uri, exist_ok=True, ctx=alice_ctx)
    await viking_fs.write(legacy_secret_uri, "legacy", ctx=alice_ctx)

    assert (await viking_fs.read(legacy_secret_uri, ctx=bob_ctx)).decode("utf-8") == "legacy"

    bob_entries = await viking_fs.tree("viking://temp", output="original", ctx=bob_ctx)
    bob_uris = {entry["uri"] for entry in bob_entries}

    assert legacy_temp_uri in bob_uris


def test_create_temp_uri_uses_user_scope_segment(viking_fs):
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice"),
        role=Role.USER,
    )

    temp_uri = viking_fs.create_temp_uri(ctx=ctx)

    assert temp_uri.startswith(f"viking://temp/{ctx.user.user_space_name()}/")


def test_create_temp_uri_without_context_preserves_legacy_shape(viking_fs):
    temp_uri = viking_fs.create_temp_uri()

    assert temp_uri.startswith("viking://temp/")
    assert temp_uri.count("/") == 3


@pytest.mark.asyncio
async def test_legacy_temp_uri_remains_accessible_to_same_account_users(viking_fs):
    alice_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice"),
        role=Role.USER,
    )
    bob_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="bob"),
        role=Role.USER,
    )

    legacy_temp_uri = viking_fs.create_temp_uri()
    secret_uri = f"{legacy_temp_uri}/shared.txt"

    await viking_fs.mkdir(legacy_temp_uri, exist_ok=True, ctx=alice_ctx)
    await viking_fs.write(secret_uri, "legacy temp", ctx=alice_ctx)

    assert (await viking_fs.read(secret_uri, ctx=bob_ctx)).decode("utf-8") == "legacy temp"


@pytest.mark.asyncio
async def test_non_root_cannot_delete_temp_root_recursively(viking_fs):
    alice_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice"),
        role=Role.USER,
    )
    bob_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="bob"),
        role=Role.USER,
    )

    alice_temp_uri = viking_fs.create_temp_uri(ctx=alice_ctx)
    bob_temp_uri = viking_fs.create_temp_uri(ctx=bob_ctx)
    bob_secret_uri = f"{bob_temp_uri}/bob.txt"

    await viking_fs.mkdir(alice_temp_uri, exist_ok=True, ctx=alice_ctx)
    await viking_fs.mkdir(bob_temp_uri, exist_ok=True, ctx=bob_ctx)
    await viking_fs.write(bob_secret_uri, "bob secret", ctx=bob_ctx)

    with pytest.raises(PermissionDeniedError):
        await viking_fs.rm("viking://temp", recursive=True, ctx=alice_ctx)

    assert (await viking_fs.read(bob_secret_uri, ctx=bob_ctx)).decode("utf-8") == "bob secret"
