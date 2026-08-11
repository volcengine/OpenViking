# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Process-local coordination for operations that depend on stable URI scopes."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Sequence


def _normalize_uri(uri: str) -> str:
    return uri.rstrip("/")


def _normalize_uris(uris: Sequence[Optional[str]]) -> tuple[str, ...]:
    return tuple(sorted({_normalize_uri(uri) for uri in uris if uri}))


def uri_matches_prefix(uri: Optional[str], prefix: str) -> bool:
    """Return whether *uri* is equal to or below *prefix* on a path boundary."""
    if not uri:
        return False
    normalized_uri = _normalize_uri(uri)
    normalized_prefix = _normalize_uri(prefix)
    return normalized_uri == normalized_prefix or normalized_uri.startswith(f"{normalized_prefix}/")


def _uris_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return any(
        uri_matches_prefix(left_uri, right_uri) or uri_matches_prefix(right_uri, left_uri)
        for left_uri in left
        for right_uri in right
    )


@dataclass(eq=False, frozen=True)
class _UriLease:
    account_id: str
    uris: tuple[str, ...]

    def overlaps(self, other: "_UriLease") -> bool:
        return self.account_id == other.account_id and _uris_overlap(self.uris, other.uris)


class UriMutationCoordinator:
    """Coordinate stable URI access and URI mutations within one process."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active_accesses: list[_UriLease] = []
        self._active_mutations: list[_UriLease] = []

    @asynccontextmanager
    async def access(
        self,
        account_id: str,
        uris: Sequence[Optional[str]],
    ) -> AsyncIterator[None]:
        """Hold a lease requiring the supplied URI scopes to remain stable."""
        lease = _UriLease(account_id=account_id, uris=_normalize_uris(uris))
        await self._acquire(lease, mutation=False)
        try:
            yield
        finally:
            await self._release(lease, mutation=False)

    @asynccontextmanager
    async def mutation(
        self,
        account_id: str,
        uris: Sequence[Optional[str]],
    ) -> AsyncIterator[None]:
        """Hold an exclusive lease for mutating the supplied URI scopes."""
        lease = _UriLease(account_id=account_id, uris=_normalize_uris(uris))
        await self._acquire(lease, mutation=True)
        try:
            yield
        finally:
            await self._release(lease, mutation=True)

    async def _acquire(self, lease: _UriLease, *, mutation: bool) -> None:
        async with self._condition:
            while self._has_blocker(lease, mutation=mutation):
                await self._condition.wait()
            self._active_leases(mutation=mutation).append(lease)

    async def _release(self, lease: _UriLease, *, mutation: bool) -> None:
        async with self._condition:
            active = self._active_leases(mutation=mutation)
            for index, current in enumerate(active):
                if current is lease:
                    del active[index]
                    break
            self._condition.notify_all()

    def _has_blocker(self, lease: _UriLease, *, mutation: bool) -> bool:
        if any(lease.overlaps(active) for active in self._active_mutations):
            return True
        return mutation and any(lease.overlaps(active) for active in self._active_accesses)

    def _active_leases(self, *, mutation: bool) -> list[_UriLease]:
        return self._active_mutations if mutation else self._active_accesses
