# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Dependency-light async helpers for the VectorDB benchmark."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import TypeVar


_T = TypeVar("_T")
_R = TypeVar("_R")


async def map_bounded_as_completed(
    items: Iterable[_T],
    fn: Callable[[_T], Awaitable[_R]],
    concurrency: int,
) -> AsyncIterator[_R]:
    """Run ``fn`` with bounded in-flight calls and yield results as they finish."""

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def bounded(item: _T) -> _R:
        async with semaphore:
            return await fn(item)

    tasks = [asyncio.create_task(bounded(item)) for item in items]
    try:
        for future in asyncio.as_completed(tasks):
            yield await future
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
