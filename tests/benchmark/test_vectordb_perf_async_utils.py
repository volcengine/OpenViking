# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest

from benchmark.vectordb_perf.async_utils import map_bounded_as_completed


@pytest.mark.asyncio
async def test_map_bounded_as_completed_limits_peak_in_flight_calls():
    active = 0
    peak_active = 0

    async def worker(value: int) -> int:
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0.001)
        active -= 1
        return value

    results = [
        result
        async for result in map_bounded_as_completed(range(20), worker, concurrency=4)
    ]

    assert sorted(results) == list(range(20))
    assert peak_active == 4


@pytest.mark.asyncio
async def test_map_bounded_as_completed_treats_non_positive_concurrency_as_one():
    active = 0
    peak_active = 0

    async def worker(value: int) -> int:
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0)
        active -= 1
        return value

    results = [
        result
        async for result in map_bounded_as_completed(range(3), worker, concurrency=0)
    ]

    assert sorted(results) == [0, 1, 2]
    assert peak_active == 1
