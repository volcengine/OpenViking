# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Cancellation-safe ownership transfer for pathlock leases."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, TypeVar

from openviking.service.task_tracker_concurrency import run_to_completion

_ResultT = TypeVar("_ResultT")


class PathLockGuard:
    """Track one owned lease across cancellation-safe handoff and release."""

    def __init__(self, agfs: Any, lease: Any = None) -> None:
        self._agfs = agfs
        self._lease = lease
        self._transferred = False

    @property
    def lease(self) -> Any:
        return self._lease

    @property
    def transferred(self) -> bool:
        return self._transferred

    def detach(self) -> Any:
        """Give the owned lease to another owner without awaiting."""
        lease = self._lease
        self._lease = None
        return lease

    async def acquire(self, factory: Callable[[], Awaitable[Any]]) -> Any:
        """Acquire and record a lease before cancellation can escape."""

        async def settle() -> Any:
            lease = await factory()
            self._lease = lease
            self._transferred = False
            return lease

        return await run_to_completion(settle)

    async def to_handoff(self) -> Any:
        if self._lease is None:
            raise RuntimeError("pathlock guard does not own a lease")
        return await run_to_completion(lambda: self._agfs.pathlock_to_handoff(self._lease))

    async def adopt(
        self,
        handoff: Any,
        *,
        fallback: Callable[[], Awaitable[Any]] | None = None,
    ) -> Any:
        """Adopt a handoff and record ownership before cancellation can escape."""

        async def settle() -> Any:
            try:
                lease = await self._agfs.pathlock_adopt(handoff)
            except Exception:
                if fallback is None:
                    raise
                lease = await fallback()
            self._lease = lease
            self._transferred = False
            return lease

        return await run_to_completion(settle)

    async def handoff(self) -> None:
        """Transfer ownership when dispatch was performed by a compatibility caller."""
        if self._lease is None:
            raise RuntimeError("pathlock guard does not own a lease")

        async def settle() -> None:
            await self._agfs.pathlock_handoff(self._lease)
            self._lease = None
            self._transferred = True

        await run_to_completion(settle)

    async def handoff_and_dispatch(
        self,
        handoff: Any,
        dispatch: Callable[[], Awaitable[_ResultT]],
        *,
        accepted: Callable[[_ResultT], bool] | None = None,
    ) -> _ResultT:
        """Settle handoff, durable dispatch, and ownership state as one operation."""
        if self._lease is None:
            raise RuntimeError("pathlock guard does not own a lease")

        async def settle() -> _ResultT:
            lease = self._lease
            await self._agfs.pathlock_handoff(lease)
            self._lease = None

            async def reclaim() -> None:
                adopt = getattr(self._agfs, "pathlock_adopt", None)
                self._lease = await adopt(handoff) if callable(adopt) else lease

            try:
                result = await dispatch()
                is_accepted = accepted(result) if accepted is not None else True
            except BaseException:
                await reclaim()
                raise
            if is_accepted:
                self._transferred = True
            else:
                await reclaim()
            return result

        return await run_to_completion(settle)

    async def dispatch_and_close(
        self,
        dispatch: Callable[[], Awaitable[_ResultT]],
    ) -> _ResultT:
        """Durably dispatch before releasing a lease as one settlement."""
        if self._lease is None:
            raise RuntimeError("pathlock guard does not own a lease")

        async def settle() -> _ResultT:
            result = await dispatch()
            lease = self._lease
            await self._agfs.pathlock_release(lease)
            self._lease = None
            return result

        return await run_to_completion(settle)

    async def close(self) -> None:
        """Release the owned lease, if any, before propagating cancellation."""

        async def settle() -> None:
            if self._lease is None:
                return
            lease = self._lease
            await self._agfs.pathlock_release(lease)
            self._lease = None

        await run_to_completion(settle)
