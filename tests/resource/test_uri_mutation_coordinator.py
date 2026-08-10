# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for process-local URI mutation coordination."""

import asyncio

import pytest

from openviking.resource.uri_mutation_coordinator import (
    UriMutationCoordinator,
    uri_matches_prefix,
)


async def _hold_lease(
    coordinator: UriMutationCoordinator,
    mode: str,
    account_id: str,
    uri: str,
    entered: asyncio.Event,
    release: asyncio.Event,
) -> None:
    async with getattr(coordinator, mode)(account_id, [uri]):
        entered.set()
        await release.wait()


async def _enter_lease(
    coordinator: UriMutationCoordinator,
    mode: str,
    account_id: str,
    uri: str,
    entered: asyncio.Event,
) -> None:
    async with getattr(coordinator, mode)(account_id, [uri]):
        entered.set()


def test_uri_matches_prefix_respects_segment_boundary():
    assert uri_matches_prefix("viking://resources/wiki/page", "viking://resources/wiki")
    assert uri_matches_prefix("viking://resources/wiki", "viking://resources/wiki/")
    assert not uri_matches_prefix("viking://resources/wiki-old", "viking://resources/wiki")
    assert not uri_matches_prefix(None, "viking://resources/wiki")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_mode", "second_mode", "second_account", "second_uri", "blocked"),
    [
        ("access", "access", "account-a", "viking://resources/wiki/page", False),
        ("access", "mutation", "account-a", "viking://resources/wiki/page", True),
        ("mutation", "access", "account-a", "viking://resources", True),
        ("mutation", "mutation", "account-a", "viking://resources/wiki", True),
        ("mutation", "access", "account-a", "viking://resources/wiki-old", False),
        ("mutation", "access", "account-b", "viking://resources/wiki", False),
    ],
)
async def test_leases_conflict_only_for_mutations_in_overlapping_account_scopes(
    first_mode: str,
    second_mode: str,
    second_account: str,
    second_uri: str,
    blocked: bool,
):
    coordinator = UriMutationCoordinator()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    first = asyncio.create_task(
        _hold_lease(
            coordinator,
            first_mode,
            "account-a",
            "viking://resources/wiki",
            first_entered,
            release_first,
        )
    )
    await first_entered.wait()

    second_entered = asyncio.Event()
    second = asyncio.create_task(
        _enter_lease(
            coordinator,
            second_mode,
            second_account,
            second_uri,
            second_entered,
        )
    )
    if blocked:
        await asyncio.sleep(0)
        assert not second_entered.is_set()
    else:
        await asyncio.wait_for(second_entered.wait(), timeout=1)

    release_first.set()
    await asyncio.gather(first, second)
    assert second_entered.is_set()


@pytest.mark.asyncio
async def test_cancelling_a_lease_holder_releases_waiters():
    coordinator = UriMutationCoordinator()
    holder_entered = asyncio.Event()

    async def hold_forever() -> None:
        async with coordinator.access("account-a", ["viking://resources/wiki"]):
            holder_entered.set()
            await asyncio.Future()

    holder = asyncio.create_task(hold_forever())
    await holder_entered.wait()

    waiter_entered = asyncio.Event()
    waiter = asyncio.create_task(
        _enter_lease(
            coordinator,
            "mutation",
            "account-a",
            "viking://resources/wiki",
            waiter_entered,
        )
    )
    holder.cancel()
    await asyncio.gather(holder, return_exceptions=True)

    await asyncio.wait_for(waiter_entered.wait(), timeout=1)
    await waiter
