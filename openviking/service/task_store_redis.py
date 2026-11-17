# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Optional shared Redis Task/Work authority, with no separate task index.

The initial backend favors the existing transition owner over a second state
machine. It writes a whole Task/Work aggregate per mutation; large fan-out and
large retained histories should use the relational backends instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
from collections.abc import Awaitable
from functools import wraps
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import uuid4

from openviking.service.task_domain import (
    ACTIVE_TASK_STATUSES,
    DeletedTask,
    DeleteExpiredResult,
    WorkRecoveryMode,
)
from openviking.service.task_store import TaskAlreadyActive, TaskWorkStore
from openviking.service.task_store_snapshot import _TaskWorkSnapshot
from openviking.service.task_tracker_concurrency import (
    KeyedAsyncLockPool,
    OwnerLoopDispatcher,
    run_to_completion,
)

_INIT = """
local version=redis.call('HGET',KEYS[1],'schema')
if not version then
 if redis.call('EXISTS',KEYS[1])==1 then return redis.error_reply('Unversioned Task/Work namespace') end
 redis.call('HSET',KEYS[1],'schema','1')
elseif version~='1' then return redis.error_reply('Unsupported Task/Work schema') end
return 1
"""
_READ = """
if redis.call('HGET',KEYS[1],'schema')~='1' then return redis.error_reply('Task/Work schema is not initialized') end
return redis.call('HGET',KEYS[1],ARGV[1])
"""
_COMMIT = """
if redis.call('HGET',KEYS[1],'schema')~='1' then return redis.error_reply('Task/Work schema is not initialized') end
local rows=cjson.decode(ARGV[1])
local deltas={}
for _,row in ipairs(rows) do
 if (redis.call('HGET',KEYS[1],row.field) or '')~=row.expected then return 0 end
 if row.old_active then deltas[row.old_business]=(deltas[row.old_business] or 0)-1 end
 if row.new_active then deltas[row.new_business]=(deltas[row.new_business] or 0)+1 end
end
local counts={}
for field,delta in pairs(deltas) do
 local current=redis.call('HGET',KEYS[1],field) or '0'
 if not string.match(current,'^%d+$') then return redis.error_reply('Invalid active Task count') end
 local count=tonumber(current)+delta
 if count<0 then return redis.error_reply('Invalid active Task count') end
 counts[field]=count
end
for _,row in ipairs(rows) do
 if row.guard and counts[row.new_business]>1 then return -1 end
end
-- All validation precedes writes. Records, active admission and discoverability
-- share this atomic command; there is no post-commit index publication step.
for _,row in ipairs(rows) do
 if row.replacement=='' then redis.call('HDEL',KEYS[1],row.field)
 else redis.call('HSET',KEYS[1],row.field,row.replacement) end
end
for field,count in pairs(counts) do
 if count==0 then redis.call('HDEL',KEYS[1],field)
 else redis.call('HSET',KEYS[1],field,count) end
end
return 1
"""
_READS = {
    "get_task",
    "get_durable_task",
    "get_task_with_events",
    "get_work",
    "get_works",
    "summarize_works",
    "has_open_work",
    "get_task_status",
    "filter_cancellation_requested",
}
_OPERATIONS = {name for name in TaskWorkStore.__dict__ if not name.startswith("_")} - {
    "work_recovery_mode",
    "create",
    "list",
    "list_durable",
    "delete_expired",
    "snapshot_task_stats",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _owner(payload):
    return payload["account_id"], payload["user_id"], payload["task_id"]


def _business(payload):
    return "a:" + _digest(
        [payload[k] for k in ("account_id", "user_id", "task_type", "resource_id")]
    )


def _active(payload):
    return bool(payload and payload["status"] in ACTIVE_TASK_STATUSES)


def _on_owner(method):
    @wraps(method)
    async def operation(self, *args, **kwargs):
        async def invoke():
            if method.__name__ in {"load", "close"}:
                return await method(self, *args, **kwargs)
            return await self._execute(lambda: method(self, *args, **kwargs))

        return await self._dispatcher.run(invoke)

    return operation


class RedisTaskWorkStore:
    work_recovery_mode = WorkRecoveryMode.DURABLE_STORE

    def __init__(self, *, params, service_name=None):
        from redis.asyncio import BlockingConnectionPool, Redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        values = dict(params)
        unknown = values.keys() - {
            "url",
            "namespace",
            "service_name",
            "socket_timeout",
            "max_connections",
        }
        if unknown:
            raise ValueError(f"Unknown Redis Task/Work options: {sorted(unknown)}")
        url = values.get("url")
        if not isinstance(url, str) or not url.startswith(("redis://", "rediss://")):
            raise ValueError("Redis Task/Work requires a redis:// or rediss:// url")
        if urlsplit(url).query or urlsplit(url).fragment:
            raise ValueError(
                "Redis Task/Work URL query options are not supported; use explicit params"
            )
        namespace = values.get("namespace", "openviking:task-work:v1")
        service = (
            service_name if service_name is not None else values.get("service_name", "openviking")
        )
        if not isinstance(namespace, str) or not re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", namespace):
            raise ValueError(
                "Redis Task/Work namespace must be 1..128 ASCII letters, digits, colon, dot, underscore or hyphen"
            )
        if not isinstance(service, str) or not service:
            raise ValueError("Redis Task/Work service_name must be nonempty")
        timeout = values.get("socket_timeout", 10.0)
        connections = values.get("max_connections", 16)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (float, int))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("Redis Task/Work socket_timeout must be finite and positive")
        if isinstance(connections, bool) or not isinstance(connections, int) or connections < 1:
            raise ValueError("Redis Task/Work max_connections must be positive")
        self._key = f"{namespace}:{{{_digest(service)}}}"
        # An uncertain write is surfaced, never automatically retried by Redis.
        pool = BlockingConnectionPool.from_url(
            url,
            decode_responses=True,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
            max_connections=connections,
            timeout=timeout,
            retry=Retry(NoBackoff(), 0),
            retry_on_error=[],
        )
        self._client = Redis(connection_pool=pool)
        self._dispatcher = OwnerLoopDispatcher()
        self._loaded = False
        self._locks: KeyedAsyncLockPool[object] = KeyedAsyncLockPool()
        self._lifecycle_lock = asyncio.Lock()
        self._inflight = set()
        self._closing = False
        self._scan_cursor = 0
        self._scan_pending = []

    @_on_owner
    async def load(self):
        async with self._lifecycle_lock:
            if self._loaded:
                # Verify again: load must never accept a changed schema.
                await self._eval(_INIT, 1, self._key)
                return
            try:
                await self._eval(_INIT, 1, self._key)
            except BaseException:
                await run_to_completion(lambda: self._client.aclose(close_connection_pool=True))
                raise
            self._loaded = True

    @_on_owner
    async def close(self):
        async def drain():
            async with self._lifecycle_lock:
                self._closing = True
                try:
                    if self._inflight:
                        await asyncio.gather(*tuple(self._inflight), return_exceptions=True)
                    self._loaded = False
                    await self._client.aclose(close_connection_pool=True)
                finally:
                    self._closing = False

        await run_to_completion(drain)

    async def _execute(self, factory):
        if self._closing:
            raise RuntimeError("Redis Task/Work store is closing")
        # Track the actual dispatched operation, including cancellation cleanup.
        task = asyncio.create_task(factory())
        self._inflight.add(task)
        try:
            return await task
        finally:
            self._inflight.discard(task)

    async def _eval(self, script, *args):
        # redis-py shares command annotations with its synchronous mixin.
        return await cast(Awaitable[Any], self._client.eval(script, *args))

    def _require_loaded(self):
        if not self._loaded:
            raise RuntimeError("Redis Task/Work store is not loaded")

    @staticmethod
    def _field(owner):
        return "r:" + _digest(owner)

    @staticmethod
    def _decode(raw):
        value = json.loads(raw)
        if value["version"] != 1 or not value["token"] or not isinstance(value["payload"], dict):
            raise ValueError("Invalid Redis Task/Work record")
        payload = value["payload"]
        if not all(isinstance(part, str) and part for part in _owner(payload)):
            raise ValueError("Invalid Redis Task/Work owner")
        if type(payload.get("_redis_exclusive")) is not bool:
            raise ValueError("Invalid Redis Task/Work admission marker")
        return payload

    async def _read(self, owner):
        self._require_loaded()
        raw = await self._eval(_READ, 1, self._key, self._field(owner))
        payload = self._decode(raw) if raw else None
        if payload is not None and _owner(payload) != tuple(owner):
            raise ValueError("Redis Task/Work owner mismatch")
        return raw, payload

    async def _records(self, account_id=None, user_id=None):
        self._require_loaded()
        await self._eval(_READ, 1, self._key, "schema")
        result = {}
        async for field, raw in self._client.hscan_iter(self._key, match="r:*", count=128):
            payload = self._decode(raw)
            owner = _owner(payload)
            if field != self._field(owner):
                raise ValueError("Redis Task/Work owner mismatch")
            if (account_id is None or owner[0] == account_id) and (
                user_id is None or owner[1] == user_id
            ):
                result[owner] = (raw, payload)
        return result

    async def _commit(self, before, after):
        changes = []
        for owner in before.keys() | after.keys():
            raw, old = before.get(owner, (None, None))
            new = after.get(owner)
            if old == new:
                continue
            changes.append(
                {
                    "field": self._field(owner),
                    "expected": raw or "",
                    "replacement": _json({"version": 1, "token": uuid4().hex, "payload": new})
                    if new
                    else "",
                    "old_business": _business(old) if old else "",
                    "new_business": _business(new) if new else "",
                    "old_active": _active(old),
                    "new_active": _active(new),
                    "guard": bool(
                        _active(new)
                        and new.get("_redis_exclusive")
                        and (not old or _business(old) != _business(new))
                    ),
                }
            )
        if not changes:
            return True
        result = await run_to_completion(lambda: self._eval(_COMMIT, 1, self._key, _json(changes)))
        if result == -1:
            payload = next(iter(after.values()))
            raise TaskAlreadyActive(payload["task_type"], payload["resource_id"])
        return bool(result)

    @_on_owner
    async def create(self, task, *, reject_if_active=False):
        owner = (task.account_id, task.user_id, task.task_id)
        if not all(owner):
            raise ValueError("Redis Task/Work requires account_id and user_id")
        async with self._locks.acquire(owner):
            for _ in range(128):
                raw, payload = await self._read(owner)
                snapshot = _TaskWorkSnapshot([payload] if payload else [])
                result = await snapshot.create(task)
                if not result.created:
                    return result
                after = snapshot.payloads()
                after[owner]["_redis_exclusive"] = bool(reject_if_active)
                if await self._commit({owner: (raw, payload)}, after):
                    return result
                await asyncio.sleep(random.uniform(0.001, 0.02))
        raise TimeoutError("Redis Task/Work creation contention")

    def __getattr__(self, name):
        if name not in _OPERATIONS:
            raise AttributeError(name)

        async def operation(*args, **kwargs):
            return await self._dispatcher.run(
                lambda: self._execute(lambda: self._operate(name, args, kwargs))
            )

        return operation

    async def _operate(self, name, args, kwargs):
        first = args[0] if args else None
        task_id = args[0].task_id if name in {"add_work", "reconcile_delivery"} else first
        account, user = kwargs.get("account_id"), kwargs.get("user_id")
        if name == "filter_cancellation_requested":
            rows = await self._records(account, user)
        elif task_id is not None and account and user:
            owner = (account, user, task_id)
            raw, payload = await self._read(owner)
            rows = {owner: (raw, payload)} if payload else {}
        else:
            rows = await self._records(account, user)
        if name in _READS:
            return await getattr(_TaskWorkSnapshot([p for _, p in rows.values()]), name)(
                *args, **kwargs
            )
        # Lock before reading again. CAS arbitrates other processes; no distributed
        # lease may expire and permit an unfenced snapshot to overwrite new state.
        lock_key = (account, user, task_id)
        async with self._locks.acquire(lock_key):
            for _ in range(128):
                if task_id is not None and account and user:
                    owner = (account, user, task_id)
                    raw, payload = await self._read(owner)
                    rows = {owner: (raw, payload)} if payload else {}
                else:
                    rows = await self._records(account, user)
                snapshot = _TaskWorkSnapshot([p for _, p in rows.values()])
                result = await getattr(snapshot, name)(*args, **kwargs)
                if await self._commit(rows, snapshot.payloads()):
                    return result
                await asyncio.sleep(random.uniform(0.001, 0.02))
        raise TimeoutError("Redis Task/Work mutation contention")

    @_on_owner
    async def list(self, account_id=None, **kwargs):
        rows = await self._records(account_id, kwargs.get("user_id"))
        return await _TaskWorkSnapshot([p for _, p in rows.values()]).list(account_id, **kwargs)

    @_on_owner
    async def list_durable(self, account_id, *, task_type=None):
        rows = await self._records(account_id)
        return await _TaskWorkSnapshot([p for _, p in rows.values()]).list_durable(
            account_id, task_type=task_type
        )

    @_on_owner
    async def delete_expired(self, policy, *, now, limit):
        self._require_loaded()
        if limit <= 0:
            return DeleteExpiredResult()
        await self._eval(_READ, 1, self._key, "schema")
        deleted = []
        scanned = 0
        async with self._locks.acquire("retention"):
            while scanned < limit:
                if not self._scan_pending:
                    self._scan_cursor, values = await self._client.hscan(
                        self._key, self._scan_cursor, match="r:*", count=min(limit, 128)
                    )
                    self._scan_pending.extend(values)
                    if not self._scan_pending and self._scan_cursor == 0:
                        break
                if not self._scan_pending:
                    continue
                field = self._scan_pending.pop()
                scanned += 1
                # Read again after scanning; expiry and open Work are checked on
                # the same revision removed by CAS, never on stale scan values.
                raw = await self._eval(_READ, 1, self._key, field)
                if not raw:
                    continue
                payload = self._decode(raw)
                owner = _owner(payload)
                snapshot = _TaskWorkSnapshot([payload])
                task = next(iter(snapshot._task_cache.values()))
                works = snapshot._work_sets[owner]
                if not works.open_count and policy.is_expired(
                    task.status.value, task.updated_at, now
                ):
                    if await self._commit({owner: (raw, payload)}, {}):
                        deleted.append(DeletedTask(owner[2], owner[0], owner[1]))
                if not self._scan_pending and self._scan_cursor == 0:
                    break
        return DeleteExpiredResult(
            tuple(deleted), scanned, not self._scan_pending and self._scan_cursor == 0
        )

    def snapshot_task_stats(self):
        # Like SQL backends, synchronous metric collection does not perform I/O.
        return {}
