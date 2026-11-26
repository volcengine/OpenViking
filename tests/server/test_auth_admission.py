# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Database-independent admission boundaries and cancellation ownership contracts."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking.server.account_stores import sql as sql_store
from openviking.server.account_stores.mysql import MySQLAccountStore
from openviking.server.account_stores.postgres import PostgresAccountStore
from openviking_cli.exceptions import InvalidArgumentError, UnavailableError


@pytest.fixture
def store():
    return PostgresAccountStore(
        service_name="admission-unit",
        params={"user": "ov", "database": "ov", "maxsize": 2, "auth_max_waiters": 2},
    )


async def wait_until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), timeout=5)


async def cancel_tasks(tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("store_type", [MySQLAccountStore, PostgresAccountStore])
@pytest.mark.parametrize("limit", [-1, True, False, None, "2", 2.0, float("inf"), float("nan")])
def test_queue_limit_requires_non_negative_integer(store_type, limit):
    with pytest.raises(InvalidArgumentError, match="auth_max_waiters"):
        store_type(
            service_name="test",
            params={"user": "ov", "password": "", "database": "ov", "auth_max_waiters": limit},
        )


@pytest.mark.parametrize("store_type", [MySQLAccountStore, PostgresAccountStore])
@pytest.mark.parametrize("limit", [None, 0, 3])
def test_queue_limit_default_and_override(store_type, limit):
    params = {"user": "ov", "password": "", "database": "ov", "maxsize": 2}
    if limit is not None:
        params["auth_max_waiters"] = limit
    store = store_type(service_name="test", params=params)
    assert store._auth_max_waiters == (40 if limit is None else limit)


def test_unlimited_mysql_pool_requires_finite_pool_for_explicit_queue_limit():
    params = {"user": "ov", "password": "", "database": "ov", "maxsize": 0}
    assert MySQLAccountStore(service_name="test", params=params)._auth_max_waiters == 0
    for limit in (0, 3):
        with pytest.raises(InvalidArgumentError, match="positive maxsize"):
            MySQLAccountStore(service_name="test", params={**params, "auth_max_waiters": limit})


@pytest.mark.parametrize("limit", [0, 2])
async def test_simultaneous_arrivals_reserve_capacity_and_reject_full_queue(
    store, limit, monkeypatch
):
    store._auth_max_waiters = limit
    release = asyncio.Event()
    entered = []
    query = AsyncMock()
    monkeypatch.setattr(store, "_get_auth_snapshot", query)

    async def occupy(index):
        async with store._auth_read_slot():
            entered.append(index)
            await release.wait()

    tasks = [asyncio.create_task(occupy(index)) for index in range(2 + limit)]
    try:
        await wait_until(lambda: len(entered) == 2 and store._auth_admission.waiting == limit)
        # Invoke the actual auth body without dispatching to a different loop.
        # It must reject before reaching query/session creation.
        with pytest.raises(UnavailableError, match="auth admission queue full"):
            await asyncio.wait_for(store.get_auth_snapshot.__wrapped__(store, "key"), timeout=1)
        query.assert_not_awaited()
        assert entered == [0, 1]
        assert store._auth_admission.waiting == limit
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        assert entered == list(range(2 + limit))
        assert store._auth_admission.waiting == 0
        assert store._auth_admission.semaphore._value == 2
    finally:
        release.set()
        await cancel_tasks(tasks)


@pytest.mark.parametrize("failure", ["cancel", "timeout"])
async def test_waiter_failure_returns_queue_space_and_allows_recovery(store, failure):
    store._engine_options["pool_size"] = 1
    store._auth_max_waiters = 1
    holder = store._auth_read_slot()
    await holder.__aenter__()
    state = store._auth_admission
    tasks = []
    holder_open = True

    async def enter():
        async with store._auth_read_slot():
            return "admitted"

    try:
        if failure == "timeout":
            store._auth_wait_timeout = 0.05
        waiter = asyncio.create_task(enter())
        tasks.append(waiter)
        await wait_until(lambda: state.waiting == 1)
        if failure == "cancel":
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        else:
            with pytest.raises(UnavailableError, match="wait budget exceeded"):
                await asyncio.wait_for(waiter, timeout=5)
        assert state.waiting == 0
        assert state.semaphore.locked()
        store._auth_wait_timeout = 5
        replacement = asyncio.create_task(enter())
        tasks.append(replacement)
        await wait_until(lambda: state.waiting == 1)
        await holder.__aexit__(None, None, None)
        holder_open = False
        assert await asyncio.wait_for(replacement, timeout=5) == "admitted"
        assert state.waiting == 0
        assert state.semaphore._value == 1
    finally:
        if holder_open:
            await holder.__aexit__(None, None, None)
        await cancel_tasks(tasks)


@pytest.mark.parametrize("failure", ["cancel", "timeout"])
async def test_failure_after_acquisition_returns_owned_permit(store, failure, monkeypatch):
    store._engine_options["pool_size"] = 1
    holder = store._auth_read_slot()
    await holder.__aenter__()
    state = store._auth_admission
    original_wait = asyncio.wait
    raced = asyncio.Event()
    holder_open = True
    tasks = []

    async def fail_after_completion(futures, *, timeout):
        done, pending = await original_wait(futures, timeout=timeout)
        assert done and not pending
        raced.set()
        if failure == "cancel":
            # Cancel the owner after its acquisition child has succeeded.
            asyncio.current_task().cancel()
            await asyncio.sleep(0)
        # Force timeout selection to win while the child owns the permit.
        return set(), set(futures)

    monkeypatch.setattr(sql_store.asyncio, "wait", fail_after_completion)

    async def enter():
        async with store._auth_read_slot():
            pytest.fail("a cancelled/expired request must not enter")

    try:
        waiter = asyncio.create_task(enter())
        tasks.append(waiter)
        await wait_until(lambda: state.waiting == 1)
        await holder.__aexit__(None, None, None)
        holder_open = False
        expected = asyncio.CancelledError if failure == "cancel" else UnavailableError
        with pytest.raises(expected):
            await asyncio.wait_for(waiter, timeout=5)
        assert raced.is_set()
        assert state.waiting == 0
        async with store._auth_read_slot():
            assert state.semaphore._value == 0
        assert state.semaphore._value == 1
    finally:
        if holder_open:
            await holder.__aexit__(None, None, None)
        await cancel_tasks(tasks)


async def test_repeated_cancellation_waits_for_acquisition_cleanup(store, monkeypatch):
    store._engine_options["pool_size"] = 1
    holder = store._auth_read_slot()
    await holder.__aenter__()
    state = store._auth_admission
    original_acquire = state.semaphore.acquire
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    tasks = []

    async def delayed_cancel_cleanup():
        try:
            return await original_acquire()
        except asyncio.CancelledError:
            cleanup_started.set()
            await finish_cleanup.wait()
            raise

    monkeypatch.setattr(state.semaphore, "acquire", delayed_cancel_cleanup)

    async def enter():
        async with store._auth_read_slot():
            pytest.fail("a cancelled request must not enter")

    try:
        waiter = asyncio.create_task(enter())
        tasks.append(waiter)
        await wait_until(lambda: state.waiting == 1)
        waiter.cancel()
        await asyncio.wait_for(cleanup_started.wait(), timeout=5)
        waiter.cancel()
        await asyncio.sleep(0)
        assert not waiter.done()
        assert state.waiting == 1
        finish_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiter, timeout=5)
        assert state.waiting == 0
    finally:
        finish_cleanup.set()
        await cancel_tasks(tasks)
        await holder.__aexit__(None, None, None)
    assert state.semaphore._value == 1


async def test_new_arrival_does_not_overtake_existing_waiters(store):
    store._engine_options["pool_size"] = 1
    store._auth_max_waiters = 3
    holder = store._auth_read_slot()
    await holder.__aenter__()
    state = store._auth_admission
    order = []
    release = [asyncio.Event() for _ in range(3)]
    tasks = []
    holder_open = True

    async def enter(index):
        async with store._auth_read_slot():
            order.append(index)
            await release[index].wait()

    try:
        for index in range(2):
            tasks.append(asyncio.create_task(enter(index)))
            await wait_until(lambda index=index: state.waiting == index + 1)
        await holder.__aexit__(None, None, None)
        holder_open = False
        # Submit a newcomer before the awakened owner has resumed.
        tasks.append(asyncio.create_task(enter(2)))
        for index in range(3):
            await wait_until(lambda index=index: len(order) == index + 1)
            assert order == list(range(index + 1))
            release[index].set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        assert state.waiting == 0
        assert state.semaphore._value == 1
    finally:
        for event in release:
            event.set()
        if holder_open:
            await holder.__aexit__(None, None, None)
        await cancel_tasks(tasks)


async def test_new_arrival_does_not_overtake_acquisition_task_not_yet_started(store):
    store._engine_options["pool_size"] = 1
    holder = store._auth_read_slot()
    await holder.__aenter__()
    state = store._auth_admission
    order = []
    holder_open = True

    async def older_request():
        async with store._auth_read_slot():
            order.append("older")

    older = asyncio.create_task(older_request())
    try:
        # Run the parent waiter once; its acquisition child is queued behind us.
        await asyncio.sleep(0)
        assert state.waiting == 1
        assert not state.semaphore._waiters
        await holder.__aexit__(None, None, None)
        holder_open = False
        async with store._auth_read_slot():
            order.append("newer")
        await asyncio.wait_for(older, timeout=5)
        assert order == ["older", "newer"]
        assert state.waiting == 0
        assert state.semaphore._value == 1
    finally:
        if holder_open:
            await holder.__aexit__(None, None, None)
        await cancel_tasks([older])
