# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Redis directory indexes and expiry-driven maintenance for the OV writer."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from bisect import bisect_right
from collections import OrderedDict
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from itertools import islice
from random import uniform
from typing import TYPE_CHECKING, Any, Sequence
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

from openviking.config.ttl import (
    TTL_ROOT_DEPTH,
    TTLIndexType,
    classify_ttl_uri,
    resolve_effective_ttl,
    validate_ttl_root,
)
from openviking.core.namespace import uri_parts
from openviking.scheduling.expiry.api import (
    TTL_ITEM_TYPE,
    CallbackRequest,
    CallbackResult,
    CallbackStatus,
    ExpiryRegistration,
    ExpiryScheduler,
    HandlerOptions,
    ItemIdentity,
)
from openviking.scheduling.expiry.config import ExpiryRedisConfig
from openviking.scheduling.expiry.redis_store import create_redis_client
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher
from openviking.storage.abstract_overview import ABSTRACT_OVERVIEW_FILENAMES
from openviking.ttl.adapter import TTLBackfillRequest, TTLRecycleRequest, TTLRecycleResult
from openviking_cli.exceptions import (
    ConflictError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    UnavailableError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.ttl_config import TTLConfig

if TYPE_CHECKING:
    from openviking.server.api_keys import APIKeyManager
    from openviking.service.core import OpenVikingService

TTLDirectory = tuple[TTLIndexType, str, str, list[str]]

logger = logging.getLogger(__name__)
DAY_MS = 86_400_000
# The schema uses Go url.PathEscape, not QueryEscape or Python's default quote.
PATH_SAFE = "-._~$&+:=@"
RESET = """
local intents = cjson.decode(ARGV[3])
local matched = {}
for scope, token in pairs(intents) do
  if redis.call('HGET', KEYS[4], scope) == token then
    table.insert(matched, scope)
  end
end
-- An acknowledged or superseded automatic reset must not erase new progress.
if next(intents) and #matched == 0 then return 2 end
redis.call('DEL', KEYS[2])
redis.call('HSET', KEYS[1], 'roots', ARGV[1], 'checkpoint', ARGV[2], 'discover', ARGV[4])
redis.call('SADD', KEYS[3], 'initial')
if ARGV[4] == '0' then
  for _, scope in ipairs(matched) do redis.call('HDEL', KEYS[4], scope) end
end
return 1
"""
INITIAL = """
local roots = redis.call('HGET', KEYS[1], 'roots')
local discover = redis.call('HGET', KEYS[1], 'discover') == '1'
if roots and roots ~= ARGV[1] and not discover then return 0 end
if not roots or discover then redis.call('HSET', KEYS[1], 'roots', ARGV[1]) end
redis.call('HSETNX', KEYS[1], 'checkpoint', ARGV[2])
redis.call('HSET', KEYS[1], 'discover', ARGV[4])
if not redis.call('GET', KEYS[2]) then redis.call('SADD', KEYS[3], 'initial') end
return 1
"""
REMOVE = """
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if score and tonumber(score) == tonumber(ARGV[2]) then
  return redis.call('ZREM', KEYS[1], ARGV[1])
end
return 0
"""
READ_INITIAL = """
local intents = {}
local fields = redis.call('HGETALL', KEYS[4])
for i = 1, #fields, 2 do intents[fields[i]] = fields[i + 1] end
local encoded = '{}'
if #fields > 0 then encoded = cjson.encode(intents) end
return {redis.call('HGET', KEYS[1], 'roots') or '',
        redis.call('HGET', KEYS[1], 'checkpoint') or '',
        redis.call('GET', KEYS[2]) or '',
        tostring(redis.call('SISMEMBER', KEYS[3], 'initial')), encoded,
        redis.call('HGET', KEYS[1], 'discover') or '0'}
"""


def directory(uri: str) -> TTLDirectory | None:
    """Return (index type, policy root, recycle unit, date components)."""
    try:
        parts = uri_parts(uri)
    except (TypeError, ValueError):
        return None
    kind = classify_ttl_uri(uri)
    if kind is None:
        return None
    root_depth = TTL_ROOT_DEPTH[kind]
    root = "viking://" + "/".join(parts[:root_depth])
    unit_depth = 1 if kind == "sessions" else 3
    if len(parts) < root_depth + unit_depth:
        return None
    date_parts = parts[root_depth : root_depth + unit_depth]
    if kind == "sessions" and date_parts[0] in ABSTRACT_OVERVIEW_FILENAMES:
        return None
    if kind != "sessions":
        if not re.fullmatch(r"\d{4}/\d{2}/\d{2}", "/".join(date_parts)):
            return None
        try:
            datetime(*map(int, date_parts))
        except ValueError:
            return None
    return kind, root, root + "/" + "/".join(date_parts), date_parts


def next_check_ms(resource_id: str, now_ms: int) -> int:
    """Preserve the Server's FNV-1a32 hourly offset; skip missed windows."""
    value = 2166136261
    for byte in resource_id.encode():
        value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    hour = 3_600_000
    due = now_ms // hour * hour + value % 3600 * 1000
    return due if due > now_ms else due + hour


class DirectoryTTL:
    def __init__(
        self,
        *,
        resource_id: str,
        redis: dict[str, Any] | None = None,
        writer: bool = True,
        timezone_name: str | None = None,
        batch_size: int = 256,
        batch_interval: float = 0.05,
        stat_rate: int = 200,
        delete_interval: float = 0.05,
        read_timeout: float = 0.2,
        retry_limit: int = 10000,
    ) -> None:
        if not resource_id or not 1 <= batch_size <= 512 or stat_rate <= 0 or batch_interval < 0:
            raise ValueError("invalid TTL resource or rate limits")
        self.resource_id = resource_id
        self.redis_config = ExpiryRedisConfig.model_validate(redis or {})
        self.writer = writer
        # With no override, naive date timestamps use the writer's existing
        # local timezone, just like its date-directory generation fallback.
        self.zone = ZoneInfo(timezone_name) if timezone_name else None
        self.batch_size, self.stat_rate = batch_size, stat_rate
        self.batch_interval = batch_interval
        self.delete_interval, self.read_timeout = delete_interval, read_timeout
        self.retry_limit = retry_limit
        self.redis = None
        self._task = None
        self._registration_task = None
        self._started = False
        self._closed = False
        self._wake = asyncio.Event()
        self._registration_wake = asyncio.Event()
        self._pending = set()
        self._locks = {}
        self._stop_scan = set()
        self._retry = {}
        self.registration_dropped_total = 0
        self._recycle_lock = asyncio.Lock()
        self._registration_lock = asyncio.Lock()
        self._scheduler: ExpiryScheduler | None = None
        self._scheduled: set[str] = set()
        self._recycle: set[str] = set()
        self._recycle_after_ms = 0
        self._dispatcher = OwnerLoopDispatcher()
        self._user_policies = OrderedDict()
        self._user_loads = {}

    def bind_runtime(self, service: OpenVikingService, account_manager: APIKeyManager) -> None:
        # Bind before queue workers can invoke the write hooks.
        self._dispatcher.bind_current_loop()
        self.fs = service.viking_fs
        self.manager = service.runtime_config_manager
        self.account_manager = account_manager

    def supports_config(self) -> bool:
        return self.writer

    def install_expiry(self, scheduler: ExpiryScheduler) -> None:
        self._scheduler = scheduler
        scheduler.register_handler(
            TTL_ITEM_TYPE,
            self,
            options=HandlerOptions(
                concurrency=1, max_attempts=None, metadata_enabled=False, lazy_start=True
            ),
        )

    @staticmethod
    def _identity(account: str) -> ItemIdentity:
        return ItemIdentity(TTL_ITEM_TYPE, account, "_system", "maintenance")

    async def _schedule(self, account: str, *, force: bool = False) -> bool:
        if self._scheduler is None or (account in self._scheduled and not force):
            return True
        async with self._registration_lock:
            manager = getattr(self, "account_manager", None)
            info = await manager.get_account(account) if manager is not None else {}
            if info is not None and info.get("status") != "deleting":
                await self._scheduler.report(
                    ExpiryRegistration(
                        self._identity(account),
                        due_at_ms=next_check_ms(self.resource_id, time.time_ns() // 1_000_000),
                        timeout_seconds=3600,
                    )
                )
                self._scheduled.add(account)
                return True
        await self.delete_account(account)
        return False

    async def delete_account(self, account: str) -> None:
        """Retire TTL state through the existing durable Account deletion task."""
        self._stop_scan.add(account)
        try:
            async with (
                self._locks.setdefault(account, asyncio.Lock()),
                self._recycle_lock,
                self._registration_lock,
            ):
                self._scheduled.discard(account)
                self._pending.discard(account)
                self._recycle.discard(account)
                for key in list(self._retry):
                    if key[0] == account:
                        self._retry.pop(key, None)
                for key in list(self._user_policies):
                    if key[0] == account:
                        self._user_policies.pop(key, None)
                if self._scheduler is not None:
                    await self._scheduler.unregister(self._identity(account))
                redis = await self.client()
                prefix = self.prefix(account)
                await redis.unlink(
                    *(prefix + kind for kind in TTLIndexType),
                    *(
                        prefix + "backfill:" + name
                        for name in ("initial", "initial:done", "pending", "reset_required")
                    ),
                )
        finally:
            self._stop_scan.discard(account)
        logger.info("TTL account state removed resource=%s account=%s", self.resource_id, account)

    async def handle(self, request: CallbackRequest) -> CallbackResult:
        if self._closed:
            return CallbackResult(CallbackStatus.RETRY, "stopping")
        account = request.identity.account_id
        # Confirm the next durable wake-up before acknowledging this one.
        # Long-running filesystem work stays outside the short expiry callback.
        if not await self._schedule(account, force=True):
            return CallbackResult(CallbackStatus.COMPLETED, "account_deleted")
        if time.time_ns() // 1_000_000 >= self._recycle_after_ms:
            self._recycle.add(account)
            self._wake_worker()
        return CallbackResult(CallbackStatus.COMPLETED, "maintenance_scheduled")

    def _wake_worker(self) -> None:
        self._wake.set()
        if self.writer and self._started and not self._closed and self._recycle:
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run(), name="ttl-maintenance")

    def _wake_registrations(self) -> None:
        self._registration_wake.set()
        if self.writer and self._started and not self._closed:
            if self._registration_task is None or self._registration_task.done():
                self._registration_task = asyncio.create_task(
                    self._flush_registrations(), name="ttl-registration"
                )

    async def _flush_registrations(self) -> None:
        # Coalesce a commit's writes without delaying the business request.
        await asyncio.sleep(min(self.batch_interval, 0.05))
        delay = 1
        while self._retry and not self._closed:
            self._registration_wake.clear()
            try:
                await self._retry_writes()
            except Exception:
                logger.exception("TTL registration retry delay=%s", delay)
                try:
                    await asyncio.wait_for(
                        self._registration_wake.wait(), uniform(delay / 2, delay)
                    )
                except TimeoutError:
                    pass
                delay = min(delay * 2, 60)
            else:
                delay = 1
                if self._retry:
                    await asyncio.sleep(self.batch_interval)

    async def patch_configuration(self, settings: dict, account_id: str | None = None) -> None:
        patch = settings["ttl"]

        def may_enable(value):
            if value is None:
                return True
            if isinstance(value, dict):
                return any(may_enable(node) for node in value.values())
            return value != "disabled"

        accounts = []
        if may_enable(patch):
            accounts = (
                [{"account_id": account_id}]
                if account_id is not None
                else await self.account_manager.list_accounts()
            )
            for account in accounts:
                account = account["account_id"]
                pair = await self.policies(account)
                roots = set(self.policy_roots())
                scopes = [root for root in roots if not resolve_effective_ttl(*pair, root).enabled]
                await self._register_reset_intent(account, scopes)
        if account_id is None:
            await self.manager.patch_cluster(settings)
        else:
            await self.manager.patch_account(account_id, settings)

    async def _register_reset_intent(self, account: str, scopes: Sequence[str]) -> None:
        try:
            if scopes:
                redis = await self.client()
                pipe = redis.pipeline(transaction=False)
                token = uuid4().hex
                for scope in scopes:
                    pipe.hsetnx(self.prefix(account) + "backfill:reset_required", scope, token)
                await pipe.execute()
            if not await self._schedule(account):
                raise NotFoundError(account, "account")
        except Exception as exc:
            raise UnavailableError("TTL reset intent registration unavailable") from exc

    async def read_initial(self, account_id: str) -> dict:
        prefix = self.prefix(account_id) + "backfill:"
        try:
            redis = await self.client()
            values = await redis.eval(
                READ_INITIAL,
                4,
                prefix + "initial",
                prefix + "initial:done",
                prefix + "pending",
                prefix + "reset_required",
            )
            return {
                "roots": values[0],
                "checkpoint": values[1],
                "done": values[2] == "1",
                "pending": values[3] == "1",
                "reset_required": json.loads(values[4]),
                "discover": values[5] == "1",
            }
        except Exception as exc:
            raise UnavailableError("TTL initial state query unavailable") from exc

    async def _initial_roots(self, account: str, pair) -> list[str]:
        ctx = self.context(account)
        roots = set()
        for i, user in enumerate(await self._children("viking://user", ctx)):
            if i % self.batch_size == 0:
                await asyncio.sleep(self.batch_interval)
            config = await self.user_policy(account, uri_parts(user)[1])
            for root in (user + "/memories/events", user + "/sessions"):
                if resolve_effective_ttl(*pair, root, config).enabled:
                    roots.add(root)
            probe = user + "/peers"
            if resolve_effective_ttl(*pair, probe, config).enabled:
                for peer in await self._children(user + "/peers", ctx):
                    roots.add(peer + "/memories/events")
        return sorted(roots)

    def prefix(self, account_id: str) -> str:
        scope = quote(self.resource_id, safe=PATH_SAFE) + "|" + quote(account_id, safe=PATH_SAFE)
        return "ttl:{" + scope + "}:"

    def context(self, account_id: str) -> RequestContext:
        return RequestContext(user=UserIdentifier(account_id, "ttl-internal"), role=Role.ROOT)

    @staticmethod
    def user_roots(user: str) -> list[str]:
        base = f"viking://user/{user}"
        return [
            base + "/sessions",
            base + "/memories/events",
            base + "/peers",
        ]

    def _cache_user_policy(self, key, config) -> None:
        self._user_policies[key] = (time.monotonic(), config)
        self._user_policies.move_to_end(key)
        while len(self._user_policies) > 1024:
            self._user_policies.popitem(last=False)

    async def user_policy(self, account: str, user: str) -> TTLConfig | None:
        """Share one snapshot per user; refresh on demand, not once per directory."""
        key = (account, user)
        cached = self._user_policies.get(key)
        if cached is not None and time.monotonic() - cached[0] < 30:
            return cached[1]

        async def load():
            cached = self._user_policies.get(key)
            if cached is not None and time.monotonic() - cached[0] < 30:
                return cached[1]
            task = self._user_loads.get(key)
            if task is None:
                from openviking.server.user_config import read_user_config

                async def read():
                    try:
                        ctx = RequestContext(user=UserIdentifier(account, user), role=Role.ROOT)
                        value = (await read_user_config(self.fs, ctx)).ttl
                        if self._user_loads.get(key) is not asyncio.current_task():
                            return await self.user_policy(account, user)
                        # A PATCH published while this read was in flight wins.
                        if self._user_policies.get(key) is cached:
                            self._cache_user_policy(key, value)
                        return self._user_policies.get(key, (0, value))[1]
                    finally:
                        if self._user_loads.get(key) is asyncio.current_task():
                            self._user_loads.pop(key, None)

                task = asyncio.create_task(read())
                self._user_loads[key] = task
            return await asyncio.shield(task)

        return await self._dispatcher.run(load)

    async def policy(self, account: str, uri: str, pair=None):
        pair = pair if pair is not None else await self.policies(account)
        parts = uri_parts(uri)
        user = (
            await self.user_policy(account, parts[1]) if classify_ttl_uri(uri) is not None else None
        )
        return resolve_effective_ttl(*pair, uri, user)

    async def _initial_enabled(self, account: str, pair, state) -> bool:
        if self.enabled(pair):
            return True
        roots = set(json.loads(state["roots"]) if state["roots"] else [])
        roots.update(state["reset_required"])
        for (scope, user), (_, config) in list(self._user_policies.items()):
            if scope == account and config is not None:
                roots.update(self.user_roots(user))
        for root in sorted(roots):
            if (await self.policy(account, root, pair)).enabled:
                return True
        return False

    async def prepare_user_configuration(self, ctx, before, after) -> None:
        pair = await self.policies(ctx.account_id)
        opening = [
            root
            for root in self.user_roots(ctx.user.user_id)
            if resolve_effective_ttl(*pair, root, after).enabled
            and not resolve_effective_ttl(*pair, root, before).enabled
        ]
        if not opening:
            return
        await self._register_reset_intent(ctx.account_id, opening)

    async def publish_user_configuration(self, ctx, config) -> None:
        async def publish():
            key = (ctx.account_id, ctx.user.user_id)
            self._user_loads.pop(key, None)
            self._cache_user_policy(key, config)

        await self._dispatcher.run(publish)

    async def policies(self, account_id: str) -> tuple[TTLConfig | None, TTLConfig | None]:
        return await self.manager.resolve_account(
            account_id, lambda v: (v.cluster.ttl, v.account.ttl)
        )

    @classmethod
    def policy_roots(cls) -> list[str]:
        return cls.user_roots("_ttl_")

    @classmethod
    def enabled(cls, pair: tuple[TTLConfig | None, TTLConfig | None]) -> bool:
        return any(resolve_effective_ttl(*pair, uri).enabled for uri in cls.policy_roots())

    def expiry(self, item: TTLDirectory, base: float, days: int) -> float:
        if item[0] == "sessions":
            return base + days * DAY_MS
        dt = datetime(*map(int, item[3]), tzinfo=self.zone) + timedelta(days=days)
        return int(dt.timestamp() * 1000)

    def base(self, item: TTLDirectory) -> int:
        return int(datetime(*map(int, item[3]), tzinfo=self.zone).timestamp() * 1000)

    async def client(self):
        # No connection or ping at startup or on a disabled-account path.
        if self.redis is None:
            from redis.backoff import NoBackoff
            from redis.retry import Retry

            if not self.redis_config.endpoints:
                raise UnavailableError("TTL Redis is not configured")
            self.redis = create_redis_client(self.redis_config, retry=Retry(NoBackoff(), 0))
        return self.redis

    async def start(self) -> None:
        self._dispatcher.bind_current_loop()
        self._closed = False
        self._started = True
        self._recycle_after_ms = next_check_ms(self.resource_id, time.time_ns() // 1_000_000)
        if self._retry:
            self._wake_registrations()

    async def aclose(self) -> None:
        self._closed = True
        self._started = False
        self._wake.set()
        self._registration_wake.set()
        if self._registration_task:
            await self._registration_task
            self._registration_task = None
        if self._task:
            await self._task  # Finish in-flight I/O; never cancel a Redis checkpoint write.
            self._task = None
        if self._retry:
            try:
                async with asyncio.timeout(2):
                    while self._retry:
                        await self._retry_writes(closing=True)
            except Exception:
                logger.warning(
                    "TTL shutdown left volatile registrations pending=%s",
                    len(self._retry),
                    exc_info=True,
                )
        async with self._recycle_lock:
            if self.redis:
                await self.redis.aclose()
                self.redis = None

    async def on_post_write(self, uri: str, ctx: RequestContext | None, written_at_ms: int) -> None:
        if not self.writer or ctx is None:
            return
        item = directory(uri)
        if item is None or (item[0] == "sessions" and uri != item[2] + "/messages.jsonl"):
            return
        if item[0] != "sessions" and uri.rsplit("/", 1)[-1] in ABSTRACT_OVERVIEW_FILENAMES:
            return
        base = written_at_ms if item[0] == "sessions" else self.base(item)
        await self._dispatcher.run(
            lambda: self._enqueue_registration((ctx.account_id, item[0], item[2]), base)
        )

    async def _enqueue_registration(self, key: tuple[str, TTLIndexType, str], base: int) -> None:
        # Only an in-memory merge on the write path. The worker owns Redis I/O.
        old = self._retry.get(key)
        if old is None and len(self._retry) >= self.retry_limit:
            self.registration_dropped_total += 1
            logger.error(
                "TTL event=registration_dropped scope=%s uri=%s pending=%s dropped_total=%s",
                key[0],
                key[2],
                len(self._retry),
                self.registration_dropped_total,
            )
            return
        was_empty = not self._retry
        self._retry[key] = max(base, old) if old is not None else base
        # Updates to a failed batch must not defeat the worker's retry backoff.
        if was_empty:
            self._wake_registrations()

    async def _register(self, key, base):
        account, kind, uri = key
        redis = await self.client()
        await redis.zadd(
            self.prefix(account) + kind,
            {uri: base},
            **({"gt": True} if kind == "sessions" else {"nx": True}),
        )

    async def _retry_writes(self, *, closing=False):
        groups = {}
        for (account, kind, uri), base in islice(self._retry.items(), min(self.batch_size, 256)):
            groups.setdefault((account, kind), {})[uri] = base
        error = None
        for (account, kind), entries in groups.items():
            if self._closed and not closing:
                return
            try:
                async with self._registration_lock:
                    pair = await self.policies(account)
                    active = {
                        uri: base
                        for uri, base in entries.items()
                        if self._retry.get((account, kind, uri)) == base
                        and (await self.policy(account, uri, pair)).enabled
                    }
                    if active:
                        redis = await self.client()
                        await redis.zadd(
                            self.prefix(account) + kind,
                            active,
                            **({"gt": True} if kind == "sessions" else {"nx": True}),
                        )
                if active:
                    await self._schedule(account)
                # Also discard disabled entries; do not remove a concurrent update.
                for uri, base in entries.items():
                    key = (account, kind, uri)
                    if self._retry.get(key) == base:
                        self._retry.pop(key, None)
            except Exception as exc:
                error = exc
                # Do not let one failed account monopolize the bounded head batch.
                for uri in entries:
                    key = (account, kind, uri)
                    if key in self._retry:
                        current = self._retry.pop(key)
                        self._retry[key] = current
                logger.exception("TTL batch registration failed scope=%s kind=%s", account, kind)
        if error is not None:
            raise error

    async def register_initial_backfill(self, request: TTLBackfillRequest) -> None:
        if not self.writer or request.resource_id != self.resource_id:
            raise FailedPreconditionError("TTL request must target this writer")
        account = request.account_id
        pair = await self.policies(account)
        state = await self.read_initial(account)
        if not await self._initial_enabled(account, pair, state):
            raise FailedPreconditionError("TTL is disabled")
        await self._register_initial(request, pair, state, discover=False)

    async def _register_initial(
        self, request: TTLBackfillRequest, pair, state, *, discover=True
    ) -> None:
        account = request.account_id
        if not await self._schedule(account):
            raise FailedPreconditionError("TTL Account is unavailable")
        intents = request.reset_intents or {
            root: token
            for root, token in state["reset_required"].items()
            if (await self.policy(account, root, pair)).enabled
        }
        reset = request.reset or bool(intents)
        roots = request.roots
        if not reset and not state["roots"] and (state["pending"] or state["done"]):
            raise FailedPreconditionError("initial state is missing roots")
        needs_discovery = roots is None and (reset or state["discover"] or not state["roots"])
        if roots is None:
            if needs_discovery and discover:
                roots = await self._initial_roots(account, pair)
                needs_discovery = False
            elif state["roots"]:
                try:
                    roots = json.loads(state["roots"])
                except ValueError as exc:
                    raise FailedPreconditionError("invalid saved initial roots") from exc
                if not isinstance(roots, list):
                    raise FailedPreconditionError("invalid saved initial roots")
            else:
                roots = []
        roots = sorted(set(roots))
        try:
            for root in roots:
                validate_ttl_root(root)
        except ValueError as exc:
            raise InvalidArgumentError(
                "initial roots must be canonical Events or Sessions roots"
            ) from exc
        if request.reset_intents and not request.reset:
            raise InvalidArgumentError("reset intents require reset")
        # Configuration can change while discovery yields. Leave
        # these intents pending rather than acknowledge a scan of closed roots.
        active_intents = {
            root: token
            for root, token in intents.items()
            if (await self.policy(account, root, pair)).enabled
        }
        if intents and not active_intents:
            return
        if not reset and state["done"]:
            return
        if reset:
            self._stop_scan.add(account)
        try:
            async with self._locks.setdefault(account, asyncio.Lock()):
                async with self._recycle_lock if reset else nullcontext():
                    prefix = self.prefix(account) + "backfill:"
                    redis = await self.client()
                    checkpoint = json.dumps({"root": 0, "stack": []}, separators=(",", ":"))
                    try:
                        result = await redis.eval(
                            RESET if reset else INITIAL,
                            4,
                            prefix + "initial",
                            prefix + "initial:done",
                            prefix + "pending",
                            prefix + "reset_required",
                            json.dumps(roots, separators=(",", ":")),
                            checkpoint,
                            json.dumps(active_intents, separators=(",", ":")),
                            "1" if needs_discovery else "0",
                        )
                    except Exception as exc:
                        raise UnavailableError(
                            "TTL initial registration failed; query state before retry"
                        ) from exc
                    if not result:
                        raise ConflictError("initial roots changed; reset is required")
                    if result == 2:
                        return
                    self._pending.add(account)
        finally:
            self._stop_scan.discard(account)
        logger.info(
            "TTL initial registered scope=%s/%s event=%s",
            self.resource_id,
            account,
            "reset" if reset else "resume",
        )

    async def _resume_initial(self, account: str, *, discover=True) -> None:
        state = await self.read_initial(account)
        if state["done"] and not state["reset_required"]:
            return
        pair = await self.policies(account)
        if not await self._initial_enabled(account, pair, state):
            self._pending.discard(account)
            return
        await self._register_initial(
            TTLBackfillRequest(self.resource_id, account), pair, state, discover=discover
        )

    async def _run(self):
        delay = 1
        while not self._closed:
            self._wake.clear()
            failed = False
            # Only expiry callbacks admit Accounts to a maintenance round.
            for account in self._recycle.copy():
                if self._closed:
                    break
                try:
                    await self._resume_initial(account)
                    if account in self._pending and not self._closed:
                        async with self._locks.setdefault(account, asyncio.Lock()):
                            await self._scan(account)
                    elif not self._closed:
                        result = await self._recycle_registered(
                            TTLRecycleRequest(self.resource_id, account)
                        )
                        logger.info(
                            "TTL recycle resource=%s account=%s result=%s",
                            self.resource_id,
                            account,
                            result,
                        )
                    self._recycle.discard(account)
                except Exception:
                    failed = True
                    logger.exception("TTL worker retry scope=%s delay=%s", account, delay)
            delay = min(delay * 2, 60) if failed else 1
            if not self._recycle:
                return
            try:
                await asyncio.wait_for(self._wake.wait(), uniform(delay / 2, delay))
            except TimeoutError:
                pass

    async def _children(self, uri, ctx):
        await asyncio.sleep(self.batch_interval)
        try:
            entries = await self.fs.ls(
                uri,
                ctx=ctx,
                node_limit=None,
                include_abstract=False,
                show_all_hidden=True,
            )
        except Exception as exc:
            if is_not_found_error(exc):
                return []
            raise
        return sorted(e["uri"].rstrip("/") for e in entries if e.get("isDir"))

    async def _scan(self, account):
        redis = await self.client()
        prefix = self.prefix(account) + "backfill:"
        if not await redis.sismember(prefix + "pending", "initial"):
            self._pending.discard(account)
            return
        if await redis.get(prefix + "initial:done") == "1":
            await redis.srem(prefix + "pending", "initial")
            self._pending.discard(account)
            return
        state = await redis.hgetall(prefix + "initial")
        if state.get("discover") == "1":
            return  # Scope discovery belongs to the scheduled maintenance round.
        if not state.get("roots") or not state.get("checkpoint"):
            raise FailedPreconditionError("pending initial has incomplete state")
        roots = json.loads(state["roots"])
        checkpoint = json.loads(state["checkpoint"])
        ctx = self.context(account)
        listings = {}
        while checkpoint["root"] < len(roots):
            if self._closed or account in self._stop_scan:
                return
            root = roots[checkpoint["root"]]
            pair = await self.policies(account)
            if not (await self.policy(account, root, pair)).enabled:
                # A disabled root does not block other enabled roots in the account.
                # Reopening this root requires the persisted initial reset.
                checkpoint["root"] += 1
                checkpoint["stack"] = []
                listings.clear()
                await redis.hset(
                    prefix + "initial",
                    "checkpoint",
                    json.dumps(checkpoint, separators=(",", ":")),
                )
                continue
            stack = checkpoint["stack"]
            if not stack:
                stack.append({"uri": root, "last": ""})
            frame = stack[-1]
            if frame["uri"] not in listings:
                listings[frame["uri"]] = await self._children(frame["uri"], ctx)
            cached = listings[frame["uri"]]
            start = bisect_right(cached, frame["last"])
            if start == len(cached):
                listings.pop(frame["uri"])
                stack.pop()
                if not stack:
                    checkpoint["root"] += 1
                await redis.hset(
                    prefix + "initial",
                    "checkpoint",
                    json.dumps(checkpoint, separators=(",", ":")),
                )
                continue
            # Stop at Session or valid date directories. Never list their contents.
            leaf = root.endswith("/sessions") or len(stack) == 3
            if not leaf:
                child = cached[start]
                frame["last"] = child
                stack.append({"uri": child, "last": ""})
                continue
            batch = cached[start : start + self.batch_size]
            items = [(u, directory(u)) for u in batch]
            items = [(u, item) for u, item in items if item is not None]
            if items:
                await asyncio.sleep(self.batch_interval)
                index = self.prefix(account) + items[0][1][0]
                scores = await redis.zmscore(index, [u for u, _ in items])
                additions = {}
                for (uri, item), score in zip(items, scores, strict=True):
                    if score is not None:
                        continue
                    if item[0] == "sessions":
                        await asyncio.sleep(1 / self.stat_rate)
                        try:
                            stat = await self.fs.stat(uri + "/messages.jsonl", ctx=ctx)
                        except Exception as exc:
                            if is_not_found_error(exc):
                                continue
                            raise
                        additions[uri] = self.modtime(stat)
                    else:
                        additions[uri] = self.base(item)
                if additions:
                    await redis.zadd(index, additions, nx=True)
            # A failed/unknown ZADD must never advance the persisted checkpoint.
            frame["last"] = batch[-1]
            await redis.hset(
                prefix + "initial",
                "checkpoint",
                json.dumps(checkpoint, separators=(",", ":")),
            )
        if self._closed or account in self._stop_scan:
            return
        await redis.set(prefix + "initial:done", "1")
        await redis.srem(prefix + "pending", "initial")
        self._pending.discard(account)
        logger.info("TTL initial scope=%s event=done", account)

    @staticmethod
    def modtime(stat):
        value = stat.get("modTime") or stat.get("ModTime")
        if not isinstance(value, str):
            raise ValueError("messages.jsonl has no valid ModTime")
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)

    async def resolve_expires_at(
        self, uris: Sequence[str], ctx: RequestContext | None
    ) -> dict[str, str | None]:
        result = dict.fromkeys(uris)
        if ctx is None:
            return result
        pair = await self.policies(ctx.account_id)
        sessions = {}
        now = {}
        for uri in uris:
            item = directory(uri)
            if item is None:
                continue
            policy = await self.policy(ctx.account_id, item[1], pair)
            if not policy.enabled:
                continue
            if item[0] == "sessions":
                sessions.setdefault(item[2], []).append((uri, item, policy.days))
            else:
                now[uri] = self.expiry(item, self.base(item), policy.days)
        if sessions:
            try:
                async with asyncio.timeout(self.read_timeout):
                    redis = await self.client()
                    scores = await redis.zmscore(
                        self.prefix(ctx.account_id) + "sessions", list(sessions)
                    )
                for entries, score in zip(sessions.values(), scores, strict=True):
                    if score is not None:
                        for uri, item, days in entries:
                            now[uri] = self.expiry(item, score, days)
            except Exception:
                logger.warning(
                    "TTL expiry read unavailable scope=%s",
                    ctx.account_id,
                    exc_info=True,
                )
        for uri, ms in now.items():
            result[uri] = (
                datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
            )
        return result

    async def recycle_due(self, request: TTLRecycleRequest) -> TTLRecycleResult:
        if not self.writer or request.resource_id != self.resource_id:
            raise FailedPreconditionError("TTL request must target this writer")
        if request.max_base_time_ms is None:
            await self._resume_initial(request.account_id, discover=False)
        return await self._recycle_registered(request)

    async def _recycle_registered(self, request: TTLRecycleRequest) -> TTLRecycleResult:
        if self._recycle_lock.locked():
            raise ConflictError("TTL recycle round is already running")
        counts = {"scanned": 0, "deleted": 0, "skipped": 0, "failed": 0}
        account = request.account_id
        async with self._recycle_lock:
            redis = await self.client()
            prefix = self.prefix(account)
            if await redis.get(prefix + "backfill:initial:done") != "1":
                return TTLRecycleResult()
            cutoff = request.max_base_time_ms
            if cutoff is None:
                if not await redis.exists(*(prefix + kind for kind in TTLIndexType)):
                    return TTLRecycleResult()
                cutoff = await self._recycle_cutoff(account)
                if cutoff is None:
                    return TTLRecycleResult()
            ctx = self.context(account)
            for kind in ("user_events", "peer_events", "sessions"):
                offset = 0
                while not self._closed:
                    await asyncio.sleep(self.batch_interval)
                    candidates = await redis.zrangebyscore(
                        prefix + kind,
                        "-inf",
                        cutoff,
                        start=offset,
                        num=min(request.limit, self.batch_size),
                        withscores=True,
                    )
                    if not candidates:
                        break
                    if await redis.get(prefix + "backfill:initial:done") != "1":
                        return TTLRecycleResult(**counts)
                    removed = 0
                    for uri, score in candidates:
                        if self._closed or account in self._stop_scan:
                            return TTLRecycleResult(**counts)
                        counts["scanned"] += 1
                        try:
                            did_delete, did_remove = await self._delete(account, uri, score, ctx)
                            counts["deleted" if did_delete else "skipped"] += 1
                            removed += did_remove
                        except Exception:
                            counts["failed"] += 1
                            logger.exception("TTL recycle failed scope=%s uri=%s", account, uri)
                    offset += len(candidates) - removed
        return TTLRecycleResult(**counts)

    async def _recycle_cutoff(self, account: str) -> int | None:
        # Use the current user registry, not initial roots or an incomplete cache.
        # Changes after this snapshot enter the next hourly round.
        pair = await self.policies(account)
        roots = self.policy_roots()

        def minimum(config, days=None):
            for root in roots:
                value = resolve_effective_ttl(*pair, root, config).days
                if value is not None and (days is None or value < days):
                    days = value
            return days

        days = minimum(None)
        if days != 1:
            manager = getattr(self, "account_manager", None)
            if manager is None:
                # Local dev mode has no user registry; use the safe lower bound.
                return time.time_ns() // 1_000_000
            users = await manager.list_users_page(account, limit=None, expose_key=False)
            for i, user in enumerate(users["users"]):
                if i % self.batch_size == 0:
                    await asyncio.sleep(self.batch_interval)
                days = minimum(await self.user_policy(account, user["user_id"]), days)
                if days == 1:
                    break
        if days is None:
            return None
        return time.time_ns() // 1_000_000 - (days - 1) * DAY_MS

    async def _delete(self, account, uri, score, ctx):
        item = directory(uri)
        if item is None or item[2] != uri:
            return False, 0
        # Every valid TTL is at least one day. Skip young entries without loading
        # user configuration; Events retain their existing calendar-day semantics.
        if self.expiry(item, score, 1) > time.time_ns() // 1_000_000:
            return False, 0
        policy = await self.policy(account, item[1])
        if (
            not policy.enabled
            or self.expiry(item, score, policy.days) > time.time_ns() // 1_000_000
        ):
            return False, 0
        await asyncio.sleep(self.delete_interval)
        path = self.fs._uri_to_path(uri, ctx=ctx)
        agfs = self.fs._async_agfs
        lease = await agfs.pathlock_acquire_tree(path)
        try:
            redis = await self.client()
            if await redis.get(self.prefix(account) + "backfill:initial:done") != "1":
                return False, 0
            policy = await self.policy(account, item[1])
            if not policy.enabled:
                return False, 0
            base = score
            if item[0] == "sessions":
                try:
                    base = self.modtime(await self.fs.stat(uri + "/messages.jsonl", ctx=ctx))
                except Exception as exc:
                    if not is_not_found_error(exc):
                        raise
            if self.expiry(item, base, policy.days) > time.time_ns() // 1_000_000:
                await self._register((account, item[0], uri), base)
                return False, int(base > score)
            try:
                await self.fs.rm(uri, recursive=True, ctx=ctx, lease_ref=lease)
            except Exception as exc:
                if not is_not_found_error(exc):
                    raise
            removed = await redis.eval(REMOVE, 1, self.prefix(account) + item[0], uri, str(score))
            return True, int(removed)
        finally:
            await agfs.pathlock_release(lease)
