"""A: Redis modTime index; current policy supplies the cutoff, no file sweep."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from openviking.config.ttl import resolve_loaded_ttl_config, resolve_ttl_config
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher
from openviking.service.ttl_deletion import delete_expired
from openviking.storage.ttl import scope_and_root, timestamp, deletion_uri
from openviking_cli.session.user_id import UserIdentifier

logger = logging.getLogger(__name__)

# A newer score must survive completion of an older in-flight deletion.
ACK = """
local actual = redis.call('ZSCORE', KEYS[1], ARGV[1])
if actual and tonumber(actual) == tonumber(ARGV[2]) then
  return redis.call('ZREM', KEYS[1], ARGV[1])
end
return 0
"""


class TTLCleanup:
    def __init__(self, fs, settings, *, client=None):
        self.fs, self.settings = fs, settings
        if settings.enabled and (
            settings.queue_backend != "redis"
            or settings.strategy != "scheduled"
            or settings.execution != "async"
        ):
            raise ValueError("This branch implements scheduled Redis cleanup")
        self._client = client
        self._dispatcher = OwnerLoopDispatcher()
        self._worker = None
        self._closed = False
        self.stats = {
            "index_writes": 0,
            "index_queries": 0,
            "delete_candidates": 0,
            "deleted": 0,
            "errors": 0,
        }

    async def start(self):
        if not self.settings.enabled:
            return
        self._dispatcher.bind_current_loop()
        if self._client is None:
            if not self.settings.index_url:
                raise ValueError("Scheduled TTL requires ttl_cleanup.index_url")
            from redis.asyncio import Redis

            self._client = Redis.from_url(self.settings.index_url, decode_responses=True)
        await self._client.ping()
        self._worker = asyncio.create_task(self._run())

    async def close(self):
        self._closed = True
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()

    def on_access(self, ctx):
        # Search never consults Redis and does not create a cleanup request.
        pass

    def _key(self, account, user, root):
        return (
            self.settings.index_namespace
            + ":objects:"
            + json.dumps([account, user, root], separators=(",", ":"))
        )

    @property
    def catalog(self):
        return self.settings.index_namespace + ":roots"

    async def on_write(self, uri, ctx, *, content=None):
        target = scope_and_root(uri)
        if not self.settings.enabled or self._closed or not target:
            return
        config = resolve_loaded_ttl_config(self.fs, ctx.account_id)
        if not config.enabled or config.resolve_uri_policy(uri, target[0]).mode != "days":
            return
        if target[0] == "sessions":
            session = deletion_uri(uri)
            if not session or uri != session + "/.meta.json" or content is None:
                return
            stamp = timestamp(json.loads(content).get("created_at"))
            if stamp is None:
                raise ValueError("Session TTL requires its existing created_at")
            await self.register(session, stamp.timestamp(), ctx)
            return
        stat = await self.fs._async_agfs.stat(self.fs._uri_to_path(uri, ctx=ctx))
        stamp = timestamp(stat.get("modTime"))
        if stat.get("isDir") or stamp is None or uri.rsplit("/", 1)[-1].startswith("."):
            return
        await self.register(uri, stamp.timestamp(), ctx)

    async def register(self, uri, stamp, ctx):
        target = scope_and_root(uri)
        if target is None:
            raise ValueError("TTL index URI must be in a managed scope")
        key = self._key(ctx.account_id, ctx.user.user_id, target[1])

        async def write():
            # Register key and object atomically; GT prevents a delayed older
            # write from replacing a newer file version's modTime.
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.sadd(self.catalog, key)
                pipe.zadd(key, {uri: stamp}, gt=True)
                await pipe.execute()
            self.stats["index_writes"] += 1

        await self._dispatcher.run(write)

    async def run_once(self):
        count = 0
        # Redis catalog iteration resumes after restart. No source ls/stat walk.
        async for key in self._client.sscan_iter(self.catalog, count=100):
            raw = key.decode() if isinstance(key, bytes) else key
            account, user, root = json.loads(raw.split(":objects:", 1)[1])
            ctx = RequestContext(user=UserIdentifier(account, user), role=Role.ROOT)
            config = await resolve_ttl_config(self.fs, account, fresh=True)
            scope = scope_and_root(root)[0]
            policy = config.resolve_uri_policy(root, scope) if config else None
            if policy is None or policy.mode != "days":
                continue
            cutoff = datetime.now(timezone.utc).timestamp() - policy.ttl_days * 86400
            self.stats["index_queries"] += 1
            rows = await self._client.zrangebyscore(
                key, "-inf", cutoff, start=0, num=self.settings.batch_size, withscores=True
            )
            for uri, score in rows:
                uri = uri.decode() if isinstance(uri, bytes) else uri
                self.stats["delete_candidates"] += 1
                if await delete_expired(self.fs, uri, ctx):
                    self.stats["deleted"] += 1
                    await self._client.eval(ACK, 1, key, uri, score)
                    count += 1
                else:
                    # If a write succeeded but its index update failed, repair
                    # this stale candidate using the current file version.
                    await self.on_write(uri, ctx)
        return count

    async def _run(self):
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.stats["errors"] += 1
                logger.exception("TTL Redis iteration failed; retained candidates will retry")
            await asyncio.sleep(self.settings.check_interval_seconds)
