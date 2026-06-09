# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("test-account", "test-user"), role=Role.USER)


@pytest.mark.asyncio
async def test_ls_missing_lazy_user_preset_directory_returns_empty_list():
    viking_fs = SimpleNamespace(
        ls=AsyncMock(side_effect=NotFoundError("viking://user/test-user/skills", "directory")),
        tree=AsyncMock(),
    )
    service = FSService(viking_fs=viking_fs)

    result = await service.ls("viking://user/skills/", ctx=_ctx())

    assert result == []
    viking_fs.ls.assert_awaited_once()


@pytest.mark.asyncio
async def test_ls_missing_unknown_directory_still_raises_not_found():
    viking_fs = SimpleNamespace(
        ls=AsyncMock(side_effect=NotFoundError("viking://user/not-a-preset", "directory")),
        tree=AsyncMock(),
    )
    service = FSService(viking_fs=viking_fs)

    with pytest.raises(NotFoundError):
        await service.ls("viking://user/not-a-preset", ctx=_ctx())
