# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.context_vector_gateway import ContextVectorGateway
from openviking.storage.vector_store.expr import And
from openviking_cli.session.user_id import UserIdentifier


def _make_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)


@pytest.mark.asyncio
async def test_search_in_tenant_uses_bound_collection_and_tenant_scope():
    storage = AsyncMock()
    storage.search.return_value = []
    gateway = ContextVectorGateway.from_storage(storage, collection_name="ctx_custom")

    await gateway.search_in_tenant(
        ctx=_make_ctx(),
        query_vector=[0.1],
        context_type="resource",
        target_directories=["viking://resources/foo"],
        limit=2,
    )

    call = storage.search.await_args.kwargs
    assert call["collection"] == "ctx_custom"
    assert isinstance(call["filter"], And)


@pytest.mark.asyncio
async def test_increment_active_count_updates_by_uri():
    storage = AsyncMock()
    storage.filter.return_value = [{"id": "r1", "active_count": 3}]
    storage.update.return_value = True
    gateway = ContextVectorGateway.from_storage(storage, collection_name="ctx_custom")

    updated = await gateway.increment_active_count(_make_ctx(), ["viking://resources/foo"])

    assert updated == 1
    update_call = storage.update.await_args
    assert update_call.args[0] == "ctx_custom"
    assert update_call.args[1] == "r1"
    assert update_call.args[2]["active_count"] == 4
