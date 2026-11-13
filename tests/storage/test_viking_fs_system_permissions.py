# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core.namespace_policy import (
    SYSTEM_COMPILE_GLOBAL_ROOT,
    SYSTEM_COMPILE_ROOT,
    SYSTEM_EXAMPLE_SKILLS_ROOT,
    SYSTEM_MEMORY_BUILTIN_ROOT,
    SYSTEM_MEMORY_CUSTOM_ROOT,
    SYSTEM_ROOT_URI,
)
from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import AclAction
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_agfs import MockLocalAGFS

CUSTOM_ASSET_URI = f"{SYSTEM_MEMORY_CUSTOM_ROOT}/customer_service.yaml"


def _ctx(role: Role, *, bypass_acl: bool = False) -> RequestContext:
    return RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=role,
        bypass_acl=bypass_acl,
    )


def _fs(*, acl_manager=None) -> VikingFS:
    return VikingFS(agfs=SimpleNamespace(), acl_manager=acl_manager)


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN, Role.ROOT])
@pytest.mark.parametrize(
    "uri",
    [
        SYSTEM_ROOT_URI,
        f"{SYSTEM_EXAMPLE_SKILLS_ROOT}/example.md",
        f"{SYSTEM_MEMORY_BUILTIN_ROOT}/default.yaml",
        SYSTEM_MEMORY_CUSTOM_ROOT,
        CUSTOM_ASSET_URI,
    ],
)
def test_system_namespace_is_readable_by_every_account_role(role, uri):
    assert _fs()._system_permission_allows(uri, _ctx(role), AclAction.READ)


@pytest.mark.parametrize(
    "role,allowed_actions",
    [
        (Role.USER, {AclAction.READ}),
        (Role.ADMIN, {AclAction.READ, AclAction.WRITE, AclAction.MANAGE}),
        (Role.ROOT, {AclAction.READ, AclAction.WRITE, AclAction.MANAGE}),
        (Role.GLOBAL, {AclAction.READ}),
    ],
)
@pytest.mark.parametrize("action", [AclAction.READ, AclAction.WRITE, AclAction.MANAGE])
@pytest.mark.parametrize(
    "uri",
    [
        SYSTEM_COMPILE_GLOBAL_ROOT,
        f"{SYSTEM_COMPILE_GLOBAL_ROOT}/",
        f"{SYSTEM_COMPILE_GLOBAL_ROOT}/nested/file.md",
    ],
)
@pytest.mark.asyncio
async def test_global_subtree_role_permissions(role, allowed_actions, action, uri):
    fs = _fs()
    ctx = _ctx(role)
    if action in allowed_actions:
        await fs._ensure_access(uri, ctx, action=action)
    else:
        with pytest.raises(PermissionDeniedError):
            await fs._ensure_access(uri, ctx, action=action)


@pytest.mark.parametrize(
    "role,allowed_actions",
    [
        (Role.USER, {AclAction.READ}),
        (Role.ADMIN, {AclAction.READ, AclAction.WRITE, AclAction.MANAGE}),
        (Role.ROOT, {AclAction.READ, AclAction.WRITE, AclAction.MANAGE}),
        (Role.GLOBAL, {AclAction.READ}),
    ],
)
@pytest.mark.parametrize("action", [AclAction.READ, AclAction.WRITE, AclAction.MANAGE])
@pytest.mark.asyncio
async def test_global_subtree_policy_does_not_consult_acl_or_honor_bypass(
    role, allowed_actions, action
):
    acl_manager = SimpleNamespace(
        is_enabled=AsyncMock(side_effect=AssertionError("system must bypass ACL")),
        resolve_many=AsyncMock(side_effect=AssertionError("system must bypass ACL")),
    )
    access = await _fs(acl_manager=acl_manager)._can_access_many(
        [SYSTEM_COMPILE_GLOBAL_ROOT],
        _ctx(role, bypass_acl=True),
        action=action,
    )
    assert access[SYSTEM_COMPILE_GLOBAL_ROOT] is (action in allowed_actions)


@pytest.mark.parametrize("uri", [SYSTEM_COMPILE_ROOT, f"{SYSTEM_COMPILE_ROOT}/global-other"])
@pytest.mark.asyncio
async def test_global_management_does_not_extend_to_parent_or_sibling(uri):
    fs = _fs()
    for role in (Role.USER, Role.ADMIN, Role.ROOT, Role.GLOBAL):
        await fs._ensure_access(uri, _ctx(role), action=AclAction.READ)
    for action in (AclAction.WRITE, AclAction.MANAGE):
        with pytest.raises(PermissionDeniedError):
            await fs._ensure_access(uri, _ctx(Role.GLOBAL), action=action)


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN, Role.ROOT, Role.GLOBAL])
@pytest.mark.parametrize("acl_enabled", [False, True])
@pytest.mark.asyncio
async def test_global_subtree_parent_listings_follow_read_permissions(role, acl_enabled, tmp_path):
    agfs = MockLocalAGFS(root_path=tmp_path)
    fs = VikingFS(
        agfs=agfs,
        acl_manager=SimpleNamespace(is_enabled=AsyncMock(return_value=acl_enabled)),
    )
    global_file = f"{SYSTEM_COMPILE_GLOBAL_ROOT}/nested/file.md"
    sibling_file = f"{SYSTEM_COMPILE_ROOT}/global-other/file.md"
    for uri in (global_file, sibling_file):
        agfs.write(fs._uri_to_path(uri, ctx=_ctx(Role.GLOBAL)), b"content")

    result = await fs.glob("**/*.md", SYSTEM_COMPILE_ROOT, ctx=_ctx(role), force_fs=True)
    assert set(result["matches"]) == {global_file, sibling_file}
    assert await fs.exists(global_file, _ctx(role))

    entries = await fs.ls(SYSTEM_COMPILE_ROOT, ctx=_ctx(role), include_abstract=False)
    assert {entry["name"] for entry in entries} == {"global", "global-other"}
    permissions = {entry["name"]: entry["access"] for entry in entries}
    assert permissions == {
        "global": "manage" if role in {Role.ADMIN, Role.ROOT} else "read",
        "global-other": "manage" if role == Role.ROOT else "read",
    }
    for uri, name in ((global_file, "global"), (sibling_file, "global-other")):
        assert (await fs.stat(uri, ctx=_ctx(role)))["access"] == permissions[name]
    parent_path = fs._uri_to_path(SYSTEM_COMPILE_ROOT, ctx=_ctx(role))
    parent = agfs._resolve(parent_path)
    fs._async_agfs.tree_directory = AsyncMock(
        return_value=[
            {
                "path": f"{parent_path}/{item.relative_to(parent).as_posix()}",
                "rel_path": item.relative_to(parent).as_posix(),
                "info": {
                    "name": item.name,
                    "isDir": item.is_dir(),
                    "size": item.stat().st_size,
                    "mode": item.stat().st_mode,
                    "modTime": item.stat().st_mtime,
                },
            }
            for item in sorted(parent.rglob("*"))
        ]
    )
    entries = await fs.tree(SYSTEM_COMPILE_ROOT, ctx=_ctx(role), include_abstract=False)
    assert {entry["uri"] for entry in entries if not entry["isDir"]} == {global_file, sibling_file}
    assert {entry["uri"]: entry["access"] for entry in entries if not entry["isDir"]} == {
        global_file: permissions["global"],
        sibling_file: permissions["global-other"],
    }
    result = await fs.grep(SYSTEM_COMPILE_ROOT, "content", ctx=_ctx(role), force_fs=True)
    assert {match["uri"] for match in result["matches"]} == {global_file, sibling_file}


@pytest.mark.parametrize("role", [Role.ADMIN, Role.ROOT])
@pytest.mark.asyncio
async def test_admin_and_root_can_manage_global_directory_with_account_isolation(role, tmp_path):
    fs = VikingFS(agfs=MockLocalAGFS(root_path=tmp_path))
    ctx = _ctx(role)
    uri = f"{SYSTEM_COMPILE_GLOBAL_ROOT}/file.md"
    other_account = RequestContext(user=UserIdentifier("other", "alice"), role=role)
    assert not await fs.exists(SYSTEM_COMPILE_GLOBAL_ROOT, ctx)
    await fs.mkdir(SYSTEM_COMPILE_GLOBAL_ROOT, ctx=ctx)
    await fs.write(uri, "first", ctx)
    await fs.write(uri, "updated", ctx)
    assert await fs.read_file(uri, ctx=ctx) == "updated"
    assert not await fs.exists(uri, other_account)
    assert await fs.read_file(uri, ctx=_ctx(Role.USER)) == "updated"
    assert await fs.read_file(uri, ctx=_ctx(Role.GLOBAL)) == "updated"
    assert await fs.read_file(uri, ctx=_ctx(Role.ROOT)) == "updated"
    for denied_role in (Role.USER, Role.GLOBAL):
        with pytest.raises(PermissionDeniedError):
            await fs.mkdir(f"{SYSTEM_COMPILE_GLOBAL_ROOT}/denied", ctx=_ctx(denied_role))
        with pytest.raises(PermissionDeniedError):
            await fs.write(uri, "denied", _ctx(denied_role))
        with pytest.raises(PermissionDeniedError):
            await fs.rm(
                SYSTEM_COMPILE_GLOBAL_ROOT, recursive=True, ctx=_ctx(denied_role), no_vector=True
            )
    await fs.rm(SYSTEM_COMPILE_GLOBAL_ROOT, recursive=True, ctx=ctx, no_vector=True)
    assert not await fs.exists(SYSTEM_COMPILE_GLOBAL_ROOT, ctx)


@pytest.mark.parametrize("action", [AclAction.WRITE, AclAction.MANAGE])
@pytest.mark.parametrize("role", [Role.ROOT, Role.ADMIN])
@pytest.mark.asyncio
async def test_system_writes_allow_root_and_limit_admin_to_custom_assets(action, role):
    fs = _fs()
    ctx = _ctx(role, bypass_acl=True)
    for uri in (
        SYSTEM_ROOT_URI,
        "viking://system/local-notes.md",
        "viking://system/memory/memory_templates",
        SYSTEM_MEMORY_BUILTIN_ROOT,
        f"{SYSTEM_MEMORY_BUILTIN_ROOT}/profile.yaml",
        SYSTEM_EXAMPLE_SKILLS_ROOT,
        f"{SYSTEM_EXAMPLE_SKILLS_ROOT}/daily-report/SKILL.md",
        SYSTEM_MEMORY_CUSTOM_ROOT,
    ):
        if role == Role.ROOT:
            await fs._ensure_access(uri, ctx, action=action)
        else:
            with pytest.raises(PermissionDeniedError):
                await fs._ensure_access(uri, ctx, action=action)
        await fs._ensure_access(uri, ctx, action=AclAction.READ)
    await fs._ensure_access(CUSTOM_ASSET_URI, ctx, action=action)


@pytest.mark.parametrize("action", [AclAction.WRITE, AclAction.MANAGE])
def test_admin_can_manage_only_concrete_custom_assets(action):
    fs = _fs()
    admin = _ctx(Role.ADMIN)

    assert fs._system_permission_allows(CUSTOM_ASSET_URI, admin, action)
    assert not fs._system_permission_allows(SYSTEM_MEMORY_CUSTOM_ROOT, admin, action)
    assert not fs._system_permission_allows(SYSTEM_MEMORY_BUILTIN_ROOT, admin, action)
    assert not fs._system_permission_allows(SYSTEM_EXAMPLE_SKILLS_ROOT, admin, action)


@pytest.mark.parametrize("action", [AclAction.WRITE, AclAction.MANAGE])
def test_user_cannot_modify_system_assets(action):
    fs = _fs()

    assert not fs._system_permission_allows(CUSTOM_ASSET_URI, _ctx(Role.USER), action)


@pytest.mark.asyncio
async def test_system_permissions_do_not_consult_acl_or_honor_bypass():
    acl_manager = SimpleNamespace(
        is_enabled=AsyncMock(side_effect=AssertionError("system must bypass ACL")),
        resolve_many=AsyncMock(side_effect=AssertionError("system must bypass ACL")),
    )
    fs = _fs(acl_manager=acl_manager)

    access = await fs._can_access_many(
        [SYSTEM_MEMORY_BUILTIN_ROOT, CUSTOM_ASSET_URI],
        _ctx(Role.USER, bypass_acl=True),
        action=AclAction.WRITE,
    )

    assert access == {
        SYSTEM_MEMORY_BUILTIN_ROOT: False,
        CUSTOM_ASSET_URI: False,
    }
    acl_manager.is_enabled.assert_not_awaited()
    acl_manager.resolve_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_permissions_are_partitioned_before_acl_disabled_fallback(monkeypatch):
    acl_manager = SimpleNamespace(is_enabled=AsyncMock(return_value=False))
    fs = _fs(acl_manager=acl_manager)
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, _ctx: uri == "viking://agent")

    access = await fs._can_access_many(
        [CUSTOM_ASSET_URI, "viking://agent"],
        _ctx(Role.USER),
        action=AclAction.WRITE,
    )

    assert access == {
        CUSTOM_ASSET_URI: False,
        "viking://agent": True,
    }


@pytest.mark.asyncio
async def test_ensure_access_enforces_system_path_boundaries():
    fs = _fs()

    await fs._ensure_access(CUSTOM_ASSET_URI, _ctx(Role.ADMIN), action=AclAction.WRITE)
    await fs._ensure_access(SYSTEM_MEMORY_BUILTIN_ROOT, _ctx(Role.USER), action=AclAction.READ)

    with pytest.raises(PermissionDeniedError, match="write permission required"):
        await fs._ensure_access(
            SYSTEM_MEMORY_CUSTOM_ROOT,
            _ctx(Role.ADMIN),
            action=AclAction.WRITE,
        )


@pytest.mark.asyncio
async def test_system_namespace_does_not_support_acl_management():
    fs = _fs(acl_manager=SimpleNamespace())

    with pytest.raises(InvalidArgumentError, match="ACL is only supported"):
        await fs.get_acl(SYSTEM_ROOT_URI, ctx=_ctx(Role.ROOT))
