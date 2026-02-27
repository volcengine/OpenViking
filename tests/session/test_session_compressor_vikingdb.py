# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.compressor import SessionCompressor
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_delete_existing_memory_uses_vikingdb_manager():
    compressor = SessionCompressor.__new__(SessionCompressor)
    compressor.vikingdb = AsyncMock()
    viking_fs = AsyncMock()
    memory = SimpleNamespace(uri="viking://user/user1/memories/events/e1")
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)

    ok = await SessionCompressor._delete_existing_memory(compressor, memory, viking_fs, ctx)

    assert ok is True
    viking_fs.rm.assert_awaited_once_with(memory.uri, recursive=False, ctx=ctx)
    compressor.vikingdb.delete_uris.assert_awaited_once_with(ctx, [memory.uri])
