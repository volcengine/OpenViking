# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account isolation and legacy behavior of wait and observers."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.retrieve.retrieval_stats import RetrievalStatsCollector
from openviking.server.routers import observer, system
from openviking.service.debug_service import ComponentStatus, ObserverService
from openviking.service.resource_service import ResourceService
from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue, QueueStatus
from openviking.storage.queuefs.process_result import ProcessResult
from openviking_cli.exceptions import UnavailableError


def test_retrieval_accounts_preserve_global_and_reset():
    collector = RetrievalStatsCollector()
    collector.record_query("memory", 2, [0.8, 0.6], account_id="A", latency_ms=30)
    collector.record_query("skill", 0, [], account_id="B", latency_ms=10)
    assert collector.snapshot("A").to_dict()["avg_score"] == 0.7
    assert collector.snapshot("A").zero_result_queries == 0
    assert collector.snapshot("B").zero_result_queries == 1
    assert collector.snapshot().total_queries == 2
    assert collector.snapshot("unknown").total_queries == 0
    collector.reset()
    assert collector.snapshot("A").total_queries == 0
    assert collector.snapshot().total_queries == 0


@pytest.mark.asyncio
async def test_local_queue_deliveries_isolate_accounts_and_release(monkeypatch):
    transport = AsyncMock()
    monkeypatch.setattr(
        "openviking.storage.queuefs.named_queue.AsyncAGFSClient", lambda _: transport
    )
    entered, finish = asyncio.Event(), asyncio.Event()

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            entered.set()
            await finish.wait()
            return ProcessResult.retry()

    queue = NamedQueue(object(), "/queue", "Semantic", Handler())
    a = {"id": "a", "data": json.dumps({"account_id": "A"})}
    b = {"id": "b", "data": json.dumps({"payload": {"context_data": {"account_id": "B"}}})}
    transport.read.side_effect = [json.dumps(a), json.dumps(b)]
    a = await queue.dequeue_raw()
    b = await queue.dequeue_raw()
    assert (await queue.get_status("A")).pending == 1
    assert (await queue.get_status("B")).pending == 1
    task = asyncio.create_task(queue.process_dequeued(a))
    await entered.wait()
    assert (await queue.get_status("A")).in_progress == 1
    assert (await queue.get_status("A")).pending == 0
    queue.reset_status()
    assert (await queue.get_status("A")).in_progress == 1
    finish.set()
    result = await task
    await queue.settle_dequeued(a, result)
    status = await queue.get_status("A")
    assert status.is_complete
    assert status.processed == status.requeue_count == 1
    queue._record_error("B only", b)
    assert (await queue.get_status("A")).errors == []
    assert (await queue.get_status("B")).errors[0].message == "B only"
    queue.release_local_delivery(b)
    assert (await queue.get_status("B")).is_complete
    # The two reads were deliveries; account observation never asks for backend totals.
    assert transport.read.await_count == 2


@pytest.mark.asyncio
async def test_queue_cancelled_execution_releases_local_ownership(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.named_queue.AsyncAGFSClient", lambda _: AsyncMock()
    )
    entered = asyncio.Event()

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            entered.set()
            await asyncio.Event().wait()

    queue = NamedQueue(object(), "/queue", "Semantic", Handler())
    delivery = {"id": "a", "data": {"account_id": "A"}}
    task = asyncio.create_task(queue.process_dequeued(delivery))
    await entered.wait()
    assert (await queue.get_status("A")).in_progress == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await queue.get_status("A")).is_complete
    assert (await queue.get_status("A")).processed == 0


@pytest.mark.asyncio
async def test_account_wait_dynamic_queries_and_legacy(monkeypatch):
    query = AsyncMock(side_effect=[2, 1, 0])
    qm = SimpleNamespace(
        check_status=AsyncMock(return_value={"Semantic": QueueStatus(processed=4)}),
        wait_complete=AsyncMock(return_value={}),
    )
    monkeypatch.setattr("openviking.service.resource_service.get_queue_manager", lambda: qm)
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: SimpleNamespace(_store=SimpleNamespace(count_open_account_work=query)),
    )
    sleep = AsyncMock()
    monkeypatch.setattr("openviking.service.resource_service.asyncio.sleep", sleep)
    result = await ResourceService.wait_processed(object(), ctx=SimpleNamespace(account_id="A"))
    assert result["Semantic"]["processed"] == 4
    assert query.await_count == 3
    assert all(call.kwargs == {"account_id": "A"} for call in query.await_args_list)
    assert [call.args for call in sleep.await_args_list] == [(5,), (5,)]
    qm.check_status.assert_awaited_once_with(account_id="A")
    assert await ResourceService.wait_processed(object()) == {}
    qm.wait_complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_account_wait_zero_and_sql_failure(monkeypatch):
    qm = SimpleNamespace(check_status=AsyncMock(return_value={}), wait_complete=AsyncMock())
    query = AsyncMock(return_value=0)
    monkeypatch.setattr("openviking.service.resource_service.get_queue_manager", lambda: qm)
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: SimpleNamespace(_store=SimpleNamespace(count_open_account_work=query)),
    )
    sleep = AsyncMock()
    monkeypatch.setattr("openviking.service.resource_service.asyncio.sleep", sleep)
    ctx = SimpleNamespace(account_id="A")
    await ResourceService.wait_processed(object(), ctx=ctx)
    sleep.assert_not_awaited()
    query.side_effect = RuntimeError("database offline")
    with pytest.raises(UnavailableError):
        await ResourceService.wait_processed(object(), ctx=ctx)
    qm.wait_complete.assert_not_awaited()


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
    service.get_queue_status_async.assert_awaited_once_with(format="json", account_id="A")
    runtime = await service.account_system(ctx, format="json")
    assert not runtime.is_healthy and runtime.errors == ["filesystem has errors"]


@pytest.mark.asyncio
async def test_routes_select_authenticated_account(monkeypatch):
    component = ComponentStatus("queue", True, False, {})
    debug = SimpleNamespace(
        get_queue_status_async=AsyncMock(return_value=component),
        get_lock_status_async=AsyncMock(return_value=component),
        get_retrieval_status=lambda **_: component,
    )
    resources = SimpleNamespace(wait_processed=AsyncMock(return_value={}))
    service = SimpleNamespace(debug=SimpleNamespace(observer=debug), resources=resources)
    monkeypatch.setattr(observer, "get_service", lambda: service)
    monkeypatch.setattr(system, "get_service", lambda: service)
    ctx = SimpleNamespace(account_id="A")
    await observer.observer_queue(ctx, "json", "account")
    debug.get_queue_status_async.assert_awaited_once_with(format="json", account_id="A")
    await system.wait_processed(system.WaitRequest(scope="account"), ctx)
    resources.wait_processed.assert_awaited_once_with(ctx=ctx)
    await system.wait_processed(system.WaitRequest(timeout=1, scope="runtime"), ctx)
    assert resources.wait_processed.await_args.kwargs == {}
    with pytest.raises(ValueError):
        system.WaitRequest(scope="other")
