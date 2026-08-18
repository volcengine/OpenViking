# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource vector-count enrichment tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.storage.expr import PathScope
from openviking_cli.session.user_id import UserIdentifier


async def test_count_resource_vectors_uses_tenant_scoped_resource_tree():
    vikingdb = SimpleNamespace(count=AsyncMock(return_value=7))
    service = ResourceService(vikingdb=vikingdb)
    ctx = RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
        actor_peer_id="studio-visitor",
    )

    result = await service._count_resource_vectors(
        "viking://user/resources/upload",
        ctx=ctx,
    )

    assert result == 7
    vikingdb.count.assert_awaited_once_with(
        filter=PathScope(
            "uri",
            "viking://user/user-1/resources/upload",
            depth=-1,
        ),
        ctx=ctx,
    )


async def test_count_resource_vectors_is_unavailable_without_vector_store():
    service = ResourceService(vikingdb=None)
    ctx = RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )

    assert (
        await service._count_resource_vectors(
            "viking://resources/upload",
            ctx=ctx,
        )
        is None
    )
