# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for system consistency URI validation."""

from unittest.mock import AsyncMock, patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.storage.index_consistency import IndexConsistencyReport
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


def _service_with_fs(stat_result: dict) -> tuple[OpenVikingService, AsyncMock]:
    service = OpenVikingService.__new__(OpenVikingService)
    service._initialized = True
    service._user = UserIdentifier.the_default_user()
    service._vikingdb_manager = AsyncMock()
    service._viking_fs = AsyncMock()
    service._viking_fs.stat.return_value = stat_result
    return service, service._viking_fs


@pytest.mark.asyncio
async def test_check_consistency_rejects_file_uri() -> None:
    service, viking_fs = _service_with_fs({"isDir": False})
    ctx = RequestContext(user=service.user, role=Role.ROOT)

    with pytest.raises(
        InvalidArgumentError,
        match="Consistency check only supports directory URIs",
    ):
        await service.check_consistency(
            uri="viking://resources/consistency-file.md",
            ctx=ctx,
        )

    viking_fs.tree.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_consistency_preserves_directory_behavior() -> None:
    service, viking_fs = _service_with_fs({"isDir": True})
    viking_fs.tree.return_value = []
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    report = IndexConsistencyReport(expected=(), missing_records=())

    with patch(
        "openviking.service.core.check_index_consistency",
        new=AsyncMock(return_value=report),
    ) as check:
        result = await service.check_consistency(uri="viking://resources", ctx=ctx)

    assert result == report.to_dict()
    viking_fs.stat.assert_awaited_once_with(
        "viking://resources",
        ctx=ctx,
        skip_count=True,
    )
    viking_fs.tree.assert_awaited_once()
    check.assert_awaited_once()
