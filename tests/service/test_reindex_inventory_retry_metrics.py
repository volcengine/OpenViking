# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service import reindex_executor as reindex_module
from openviking.service.reindex_executor import ReindexExecutor
from openviking.storage.errors import VikingDBException
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_reindex_inventory_retry_emits_backend_error_code(monkeypatch):
    emitted: list[dict] = []
    error = VikingDBException("rate limited", code="-55900461", retryable=True)
    vikingdb = SimpleNamespace(
        _backend_type="vikingdb",
        get_reindex_inventory_under_uri=AsyncMock(side_effect=[error, {}]),
    )
    monkeypatch.setattr(
        reindex_module.VikingDBEventDataSource,
        "record_retry",
        staticmethod(lambda **kwargs: emitted.append(kwargs)),
    )
    monkeypatch.setattr(reindex_module.asyncio, "sleep", AsyncMock())

    result = await ReindexExecutor()._read_reindex_inventory(
        vikingdb,
        uri="viking://resources/demo",
        ctx=RequestContext(user=UserIdentifier("test", "admin"), role=Role.ADMIN),
        recursive=True,
    )

    assert result == {}
    assert emitted == [{
        "backend": "vikingdb",
        "operation": "reindex_inventory",
        "error": error,
        "delay_seconds": 0.25,
    }]
