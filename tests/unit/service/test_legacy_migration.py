# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock, call

import pytest

from openviking.core.namespace import canonical_session_uri
from openviking.server.identity import RequestContext, Role
from openviking.service.legacy_migration import LegacyDataMigration, MigrationPlan
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


class _PathMappingVikingFS:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RequestContext]] = []

    def _uri_to_path(self, uri: str, ctx: RequestContext) -> str:
        self.calls.append((uri, ctx))
        parts = uri.removeprefix("viking://").split("/")
        safe_parts = [VikingFS._shorten_component(part) for part in parts]
        return f"/local/{ctx.account_id}/{'/'.join(safe_parts)}"


def _migration(viking_fs: object) -> LegacyDataMigration:
    migration = LegacyDataMigration.__new__(LegacyDataMigration)
    migration._viking_fs = viking_fs
    return migration


@pytest.mark.asyncio
async def test_preflight_recognizes_registered_user_shortened_physical_directory():
    account_id = "acct"
    user_id = "user-" + ("u" * 300)
    physical_user_id = VikingFS._shorten_component(user_id)
    new_user_id = "new-user"
    viking_fs = _PathMappingVikingFS()
    migration = _migration(viking_fs)
    migration._registry_account_ids = AsyncMock(return_value={account_id})
    migration._physical_account_ids = AsyncMock(return_value={account_id})
    migration._registry_user_ids = AsyncMock(return_value={user_id})
    migration._physical_user_ids = AsyncMock(return_value={physical_user_id, new_user_id})
    migration._has_user = AsyncMock(side_effect=lambda _account, user: user == user_id)
    migration._exists = AsyncMock(return_value=True)
    migration._plan_sessions = AsyncMock()

    plan = await migration.preflight()

    assert plan.account_users == {account_id: {user_id, new_user_id}}
    assert plan.created_users == {(account_id, new_user_id)}
    assert plan.errors == []
    assert migration._has_user.await_args_list == [
        call(account_id, user_id),
        call(account_id, new_user_id),
    ]


@pytest.mark.asyncio
async def test_plan_session_uses_canonical_shortened_target_path():
    account_id = "acct"
    owner = "owner-" + ("o" * 300)
    session_id = "session-" + ("s" * 300)
    source_path = f"/local/{account_id}/session/legacy"
    viking_fs = _PathMappingVikingFS()
    migration = _migration(viking_fs)
    migration._session_owner = AsyncMock(return_value=owner)
    migration._ensure_plan_user = AsyncMock(return_value=True)
    plan = MigrationPlan(account_users={account_id: {owner}})
    ctx = RequestContext(
        user=UserIdentifier(account_id=account_id, user_id=owner),
        role=Role.USER,
    )
    target_uri = canonical_session_uri(ctx, session_id)
    expected_target_path = viking_fs._uri_to_path(target_uri, ctx=ctx)
    viking_fs.calls.clear()

    await migration._plan_one_session(
        account_id,
        session_id=session_id,
        source_path=source_path,
        owner_hint=None,
        plan=plan,
    )

    assert plan.errors == []
    assert len(plan.operations) == 1
    assert plan.operations[0].target_path == expected_target_path
    assert viking_fs.calls == [(target_uri, ctx)]
