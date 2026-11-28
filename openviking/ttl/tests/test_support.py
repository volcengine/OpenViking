# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Test-only CacheRuntime adapter backed by fakeredis."""

from __future__ import annotations


class FakeredisCacheRuntime:
    """Expose the TTL CacheRuntime contract over an isolated fakeredis instance."""

    default_ttl_seconds = 0

    def __init__(self) -> None:
        """Create an isolated binary-safe fakeredis client."""
        import fakeredis.aioredis

        self.redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
        self._scripts: dict[str, str] = {}

    def register_script(self, script_id: str, redis_lua: str) -> None:
        """Register one named Lua script without executing it."""
        self._scripts[script_id] = redis_lua

    async def execute_script(self, script_id: str, keys: list[str], args: list[bytes]):
        """Execute one registered Lua script through fakeredis."""
        return await self.redis.eval(self._scripts[script_id], len(keys), *keys, *args)

    async def get(self, key: str) -> bytes | None:
        """Read one string key."""
        return await self.redis.get(key)

    async def hgetall(self, key: str) -> dict[str, bytes]:
        """Read all hash fields using CacheRuntime's text field contract."""
        return {
            field.decode() if isinstance(field, bytes) else field: value
            for field, value in (await self.redis.hgetall(key)).items()
        }

    async def hset(self, key: str, values: dict[str, bytes]) -> int:
        """Store hash fields and return the number of new fields."""
        return await self.redis.hset(key, mapping=values)

    async def hsetnx(self, key: str, values: dict[str, bytes]) -> int:
        """Store only missing hash fields and return the number written."""
        added = 0
        for field, value in values.items():
            added += await self.redis.hsetnx(key, field, value)
        return added

    async def sismember(self, key: str, member: bytes) -> bool:
        """Return whether a set contains the given member."""
        return await self.redis.sismember(key, member)

    async def unlink(self, keys: list[str]) -> int:
        """Unlink all supplied keys."""
        return await self.redis.unlink(*keys)

    async def zadd(
        self,
        key: str,
        entries: list[tuple[float, str]],
        *,
        only_if_not_exists: bool = False,
        only_if_greater: bool = False,
    ) -> int:
        """Store sorted-set entries using the requested Redis condition."""
        return await self.redis.zadd(
            key,
            {member: score for score, member in entries},
            nx=only_if_not_exists,
            gt=only_if_greater,
        )

    async def zmscore(self, key: str, members: list[str]) -> list[float | None]:
        """Return sorted-set scores in the supplied member order."""
        return await self.redis.zmscore(key, members)

    async def zcard(self, key: str) -> int:
        """Return the sorted-set cardinality."""
        return await self.redis.zcard(key)

    async def zrangebyscore(
        self,
        key: str,
        min_score: float,
        max_score: float,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[tuple[str, float]]:
        """Return scored members in the requested score range."""
        return [
            (member.decode() if isinstance(member, bytes) else member, score)
            for member, score in await self.redis.zrangebyscore(
                key,
                min_score,
                max_score,
                start=offset,
                num=limit,
                withscores=True,
            )
        ]
