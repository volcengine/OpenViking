# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.content_write import ContentWriteCoordinator
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def ctx():
    return RequestContext(user=UserIdentifier("account-1", "user-1"), role=Role.USER)


class _FakeVikingFS:
    def __init__(self, stat_effect):
        self.stat = AsyncMock(side_effect=stat_effect)


@pytest.mark.asyncio
async def test_safe_stat_propagates_transient_storage_failure(ctx):
    fake_fs = _FakeVikingFS(TimeoutError("backend timeout"))
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)

    with pytest.raises(TimeoutError, match="backend timeout"):
        await coordinator._safe_stat("viking://resources/demo", ctx=ctx, allow_not_found=True)

    with pytest.raises(TimeoutError, match="backend timeout"):
        await coordinator._safe_stat("viking://resources/demo", ctx=ctx)


@pytest.mark.asyncio
async def test_safe_stat_keeps_not_found_semantics(ctx):
    fake_fs = _FakeVikingFS(NotFoundError("viking://resources/missing", "file"))
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)

    assert await coordinator._safe_stat(
        "viking://resources/missing", ctx=ctx, allow_not_found=True
    ) == {"not_found": True}

    with pytest.raises(NotFoundError):
        await coordinator._safe_stat("viking://resources/missing", ctx=ctx)
