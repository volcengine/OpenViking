# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Lightweight unit tests for phase-1 ACL permission profiles."""

import pytest

from openviking.server.api_keys import APIKeyManager
from openviking.server.identity import (
    READ_ONLY_PERMISSION_PROFILE_ID,
    EffectivePermissions,
    RequestContext,
    Role,
)
from openviking.server.permissions import require_data_read, require_data_write
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


class _FakeAGFS:
    def __init__(self):
        self.storage: dict[str, bytes] = {}

    def read(self, path: str) -> bytes:
        if path not in self.storage:
            raise FileNotFoundError(path)
        return self.storage[path]

    def write(self, path: str, content: bytes) -> None:
        self.storage[path] = content

    def mkdir(self, path: str) -> None:
        return None


class _FakeVikingFS:
    def __init__(self):
        self.agfs = _FakeAGFS()

    async def encrypt_bytes(self, account_id: str, content: bytes) -> bytes:
        return content

    async def decrypt_bytes(self, account_id: str, raw: bytes) -> bytes:
        return raw


ROOT_KEY = "phase1-root-key"


@pytest.mark.asyncio
async def test_permission_profile_persistence_and_resolution():
    viking_fs = _FakeVikingFS()
    manager = APIKeyManager(root_key=ROOT_KEY, viking_fs=viking_fs)
    await manager.load()

    await manager.create_account("acme", "alice")
    await manager.upsert_permission_profile(
        "acme",
        "readonly_docs",
        permissions=EffectivePermissions(data_read=True, data_write=False),
    )
    user_key = await manager.register_user(
        "acme",
        "bob",
        permission_profile="readonly_docs",
    )

    resolved = manager.resolve(user_key)
    assert resolved.permission_profile == "readonly_docs"
    assert resolved.effective_permissions == EffectivePermissions(
        data_read=True,
        data_write=False,
    )

    reloaded = APIKeyManager(root_key=ROOT_KEY, viking_fs=viking_fs)
    await reloaded.load()
    assert {item["profile_id"] for item in reloaded.get_permission_profiles("acme")} >= {
        "data_rw",
        "data_readonly",
        "no_data_access",
        "readonly_docs",
    }


@pytest.mark.asyncio
async def test_admin_profile_assignment_keeps_admin_semantics():
    viking_fs = _FakeVikingFS()
    manager = APIKeyManager(root_key=ROOT_KEY, viking_fs=viking_fs)
    await manager.load()

    admin_key = await manager.create_account("acme", "alice")
    await manager.set_user_permission_profile("acme", "alice", READ_ONLY_PERMISSION_PROFILE_ID)

    resolved = manager.resolve(admin_key)
    assert resolved.role == Role.ADMIN
    assert resolved.permission_profile == READ_ONLY_PERMISSION_PROFILE_ID
    assert resolved.effective_permissions == EffectivePermissions.full_access()


def test_permission_denied_error_is_structured_for_read_and_write():
    ctx = RequestContext(
        user=UserIdentifier("acme", "alice", "default"),
        role=Role.USER,
        permission_profile="readonly_docs",
        effective_permissions=EffectivePermissions(data_read=True, data_write=False),
    )

    require_data_read(ctx, operation="search.find", resource="viking://resources")

    with pytest.raises(PermissionDeniedError) as exc_info:
        require_data_write(ctx, operation="filesystem.mkdir", resource="viking://resources/demo")

    assert exc_info.value.details == {
        "resource": "viking://resources/demo",
        "operation": "filesystem.mkdir",
        "required_permission": "data.write",
        "permission_profile": "readonly_docs",
        "role": "user",
        "effective_permissions": {"data_read": True, "data_write": False},
    }
