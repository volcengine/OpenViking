# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for process-local URI mutation coordination."""

import asyncio

import pytest

from openviking.resource.uri_mutation_coordinator import (
    UriMutationCoordinator,
    uri_matches_prefix,
)


async def _hold_mutation(
    coordinator: UriMutationCoordinator,
    account_id: str,
    uris: list[str],
    entered: asyncio.Event,
    release: asyncio.Event,
) -> None:
    async with coordinator.mutation(account_id, uris):
        entered.set()
        await release.wait()


async def _enter_access(
    coordinator: UriMutationCoordinator,
    account_id: str,
    uris: list[str],
    entered: asyncio.Event,
) -> None:
    async with coordinator.access(account_id, uris):
        entered.set()


async def _enter_mutation(
    coordinator: UriMutationCoordinator,
    account_id: str,
    uris: list[str],
    entered: asyncio.Event,
) -> None:
    async with coordinator.mutation(account_id, uris):
        entered.set()


def test_uri_matches_prefix_respects_segment_boundary():
    assert uri_matches_prefix("viking://resources/wiki/page", "viking://resources/wiki")
    assert uri_matches_prefix("viking://resources/wiki", "viking://resources/wiki/")
    assert not uri_matches_prefix("viking://resources/wiki-old", "viking://resources/wiki")
    assert not uri_matches_prefix(None, "viking://resources/wiki")


@pytest.mark.asyncio
async def test_unrelated_access_continues_during_mutation():
    coordinator = UriMutationCoordinator()
    mutation_entered = asyncio.Event()
    release_mutation = asyncio.Event()
    mutation_task = asyncio.create_task(
        _hold_mutation(
            coordinator,
            "account-a",
            ["viking://resources/source", "viking://resources/target"],
            mutation_entered,
            release_mutation,
        )
    )
    await mutation_entered.wait()

    access_entered = asyncio.Event()
    access_task = asyncio.create_task(
        _enter_access(
            coordinator,
            "account-a",
            ["viking://resources/unrelated"],
            access_entered,
        )
    )
    await asyncio.wait_for(access_entered.wait(), timeout=1.0)

    release_mutation.set()
    await asyncio.gather(mutation_task, access_task)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri",
    [
        "viking://resources/source",
        "viking://resources/source/child",
        "viking://resources/target",
        "viking://resources/target/child",
        "viking://resources",
    ],
)
async def test_overlapping_access_waits_for_mutation(uri: str):
    coordinator = UriMutationCoordinator()
    mutation_entered = asyncio.Event()
    release_mutation = asyncio.Event()
    mutation_task = asyncio.create_task(
        _hold_mutation(
            coordinator,
            "account-a",
            ["viking://resources/source", "viking://resources/target"],
            mutation_entered,
            release_mutation,
        )
    )
    await mutation_entered.wait()

    access_entered = asyncio.Event()
    access_task = asyncio.create_task(
        _enter_access(coordinator, "account-a", [uri], access_entered)
    )
    await asyncio.sleep(0)
    assert not access_entered.is_set()

    release_mutation.set()
    await asyncio.gather(mutation_task, access_task)
    assert access_entered.is_set()


@pytest.mark.asyncio
async def test_overlapping_access_leases_can_nest():
    coordinator = UriMutationCoordinator()

    async with coordinator.access("account-a", ["viking://resources/source"]):
        async with coordinator.access("account-a", ["viking://resources/source/child"]):
            pass


@pytest.mark.asyncio
async def test_overlapping_mutation_waits_for_access():
    coordinator = UriMutationCoordinator()
    release_access = asyncio.Event()
    access_entered = asyncio.Event()

    async def hold_access() -> None:
        async with coordinator.access("account-a", ["viking://resources/source"]):
            access_entered.set()
            await release_access.wait()

    access_task = asyncio.create_task(hold_access())
    await access_entered.wait()

    mutation_entered = asyncio.Event()
    mutation_task = asyncio.create_task(
        _enter_mutation(
            coordinator,
            "account-a",
            ["viking://resources/source/child"],
            mutation_entered,
        )
    )
    await asyncio.sleep(0)
    assert not mutation_entered.is_set()

    release_access.set()
    await asyncio.gather(access_task, mutation_task)
    assert mutation_entered.is_set()


@pytest.mark.asyncio
async def test_overlapping_mutations_are_serialized():
    coordinator = UriMutationCoordinator()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    first = asyncio.create_task(
        _hold_mutation(
            coordinator,
            "account-a",
            ["viking://resources/source"],
            first_entered,
            release_first,
        )
    )
    await first_entered.wait()

    second_entered = asyncio.Event()
    second = asyncio.create_task(
        _enter_mutation(
            coordinator,
            "account-a",
            ["viking://resources/source/child"],
            second_entered,
        )
    )
    await asyncio.sleep(0)
    assert not second_entered.is_set()

    release_first.set()
    await asyncio.gather(first, second)
    assert second_entered.is_set()


@pytest.mark.asyncio
async def test_same_uri_in_different_accounts_does_not_overlap():
    coordinator = UriMutationCoordinator()
    mutation_entered = asyncio.Event()
    release_mutation = asyncio.Event()
    mutation_task = asyncio.create_task(
        _hold_mutation(
            coordinator,
            "account-a",
            ["viking://resources/source"],
            mutation_entered,
            release_mutation,
        )
    )
    await mutation_entered.wait()

    access_entered = asyncio.Event()
    access_task = asyncio.create_task(
        _enter_access(
            coordinator,
            "account-b",
            ["viking://resources/source"],
            access_entered,
        )
    )
    await asyncio.wait_for(access_entered.wait(), timeout=1.0)

    release_mutation.set()
    await asyncio.gather(mutation_task, access_task)


@pytest.mark.asyncio
async def test_cancelled_lease_holder_releases_waiters():
    coordinator = UriMutationCoordinator()
    holder_entered = asyncio.Event()

    async def hold_forever() -> None:
        async with coordinator.access("account-a", ["viking://resources/source"]):
            holder_entered.set()
            await asyncio.Future()

    holder = asyncio.create_task(hold_forever())
    await holder_entered.wait()

    mutation_entered = asyncio.Event()
    waiter = asyncio.create_task(
        _enter_mutation(
            coordinator,
            "account-a",
            ["viking://resources/source"],
            mutation_entered,
        )
    )
    holder.cancel()
    await asyncio.gather(holder, return_exceptions=True)
    await asyncio.wait_for(mutation_entered.wait(), timeout=1.0)
    await waiter


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leave_active_lease():
    coordinator = UriMutationCoordinator()
    mutation_entered = asyncio.Event()
    release_mutation = asyncio.Event()
    mutation_task = asyncio.create_task(
        _hold_mutation(
            coordinator,
            "account-a",
            ["viking://resources/source"],
            mutation_entered,
            release_mutation,
        )
    )
    await mutation_entered.wait()

    cancelled_entered = asyncio.Event()
    cancelled_waiter = asyncio.create_task(
        _enter_access(
            coordinator,
            "account-a",
            ["viking://resources/source"],
            cancelled_entered,
        )
    )
    await asyncio.sleep(0)
    cancelled_waiter.cancel()
    await asyncio.gather(cancelled_waiter, return_exceptions=True)

    release_mutation.set()
    await mutation_task

    next_entered = asyncio.Event()
    await asyncio.wait_for(
        _enter_mutation(
            coordinator,
            "account-a",
            ["viking://resources/source"],
            next_entered,
        ),
        timeout=1.0,
    )
    assert next_entered.is_set()
