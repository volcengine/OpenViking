# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account wait and system health compatibility."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.service.debug_service import ComponentStatus, ObserverService
from openviking.service.resource_service import ResourceService
from openviking.storage.queuefs.named_queue import QueueStatus
from openviking_cli.exceptions import UnavailableError


@pytest.mark.asyncio
async def test_account_wait_dynamic_queries_and_legacy(monkeypatch):
    query = AsyncMock(side_effect=[False, True, True, False, RuntimeError("database offline")])
    qm = SimpleNamespace(
        check_status=AsyncMock(return_value={"Semantic": QueueStatus(processed=4)}),
        wait_complete=AsyncMock(return_value={}),
    )
    monkeypatch.setattr("openviking.service.resource_service.get_queue_manager", lambda: qm)
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: SimpleNamespace(has_work=query),
    )
    sleep = AsyncMock()
    monkeypatch.setattr("openviking.service.resource_service.asyncio.sleep", sleep)
    ctx = SimpleNamespace(account_id="A")
    await ResourceService.wait_processed(object(), ctx=ctx)
    sleep.assert_not_awaited()
    query.reset_mock()
    qm.check_status.reset_mock()
    result = await ResourceService.wait_processed(object(), ctx=ctx)
    assert result["Semantic"]["processed"] == 4
    assert all(
        call.kwargs == {"account_id": "A", "exclude_inline": True} for call in query.await_args_list
    )
    qm.check_status.assert_awaited_once_with(account_id="A")
    assert await ResourceService.wait_processed(object()) == {}
    qm.wait_complete.assert_awaited_once()
    with pytest.raises(UnavailableError):
        await ResourceService.wait_processed(object(), ctx=ctx)
    qm.wait_complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_account_health_excludes_filesystem():
    service = ObserverService()
    good = ComponentStatus("component", True, False, {})
    service.get_queue_status_async = AsyncMock(return_value=good)
    service.get_lock_status_async = AsyncMock(return_value=good)
    service.account_models = AsyncMock(return_value=good)
    service.account_vikingdb = AsyncMock(return_value=good)
    service.get_retrieval_status = lambda **_: good
    service.get_filesystem_status_async = AsyncMock(
        return_value=ComponentStatus("filesystem", False, True, {})
    )
    ctx = SimpleNamespace(account_id="A")
    account = await service.account_system(ctx, format="json", account_scope=True)
    assert account.is_healthy and account.errors == []
    assert "filesystem" in account.components
    runtime = await service.account_system(ctx, format="json")
    assert not runtime.is_healthy and runtime.errors == ["filesystem has errors"]
