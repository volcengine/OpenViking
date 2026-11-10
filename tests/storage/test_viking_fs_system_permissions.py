# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core.namespace_policy import (
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


@pytest.mark.parametrize("action", [AclAction.WRITE, AclAction.MANAGE])
@pytest.mark.parametrize("role", [Role.ROOT, Role.ADMIN])
@pytest.mark.asyncio
async def test_system_writes_are_limited_to_custom_assets_for_privileged_roles(action, role):
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
