# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""B: access-triggered bounded discovery and QueueFS deletion, no Redis.

The queue consumer polls queue state, never source directories. Scheduling an
access is best effort until its discovery message is persisted; the next access
can retrigger it. Durable deliveries are acknowledged only after successful work.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from openviking.config.ttl import resolve_loaded_ttl_config, resolve_ttl_config
from openviking.server.identity import RequestContext, Role
from openviking.service.ttl_deletion import delete_expired
from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.queue_middleware import AckContext
from openviking.storage.ttl import query_filter, scope_and_root, deletion_uri
from openviking_cli.session.user_id import UserIdentifier

logger = logging.getLogger(__name__)


class TTLCleanup:
    def __init__(self, fs, settings, *, queue=None):
        self.fs, self.settings = fs, settings
        if settings.enabled and (
            settings.queue_backend != "queuefs" or settings.strategy != "passive"
        ):
            raise ValueError("This branch implements passive QueueFS cleanup")
        self.queue = queue or NamedQueue(fs.agfs, "/queue", "TTLModtimeCleanup")
        self._loop = None
        self._worker = None
        self._tasks = set()
        self._access = {}
        self._closed = False
        self._pending = None
        self.stats = {"discovery_queries": 0, "delete_candidates": 0, "deleted": 0, "errors": 0}

    async def start(self):
        if self.settings.enabled:
            self._loop = asyncio.get_running_loop()
            self._worker = asyncio.create_task(self._run())

    async def close(self):
        self._closed = True
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)

    def on_access(self, ctx):
        if self._closed or not self.settings.enabled:
            return
        config = resolve_loaded_ttl_config(self.fs, ctx.account_id)
        if config is None or not config.enabled:
            return

        def schedule():
            key = (ctx.account_id, ctx.user.user_id)
            now = time.monotonic()
            if now - self._access.get(key, float("-inf")) < self.settings.check_interval_seconds:
                return
            if len(self._access) >= 1024:
                self._access.clear()
            self._access[key] = now
            task = asyncio.create_task(self._enqueue_access(ctx))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        if self._loop is None or self._loop is asyncio.get_running_loop():
            schedule()
        else:
            self._loop.call_soon_threadsafe(schedule)

    async def _enqueue_access(self, ctx):
        try:
            await self.queue.enqueue(
                {"kind": "discover", "account": ctx.account_id, "user": ctx.user.user_id}
            )
        except Exception:
            self.stats["errors"] += 1
            self._access.pop((ctx.account_id, ctx.user.user_id), None)
            logger.exception("TTL discovery enqueue failed")

    async def on_write(self, uri, ctx, **_):
        if scope_and_root(uri):
            self.on_access(ctx)

    async def _discover(self, ctx, config):
        filters = And(
            [
                query_filter(config, datetime.now(timezone.utc), expired=True),
                Eq("account_id", ctx.account_id),
                PathScope("uri", f"viking://user/{ctx.user.user_id}"),
            ]
        )
        self.stats["discovery_queries"] += 1
        rows = await self.fs.vector_store.filter(
            filter=filters,
            limit=self.settings.batch_size,
            ctx=ctx,
            output_fields=["uri", "updated_at", "level"],
        )
        seen = set()
        for row in rows:
            uri = row.get("uri", "")
            target = deletion_uri(uri)
            if target is not None:
                seen.add(target)
        return sorted(seen)

    async def on_access_sync(self, ctx):
        """Explicit comparison mode: pay candidate discovery and deletes inline."""
        if not self.settings.enabled:
            return
        config = resolve_loaded_ttl_config(self.fs, ctx.account_id)
        if config is None or not config.enabled:
            return
        for uri in await self._discover(ctx, config):
            self.stats["delete_candidates"] += 1
            if await delete_expired(self.fs, uri, ctx):
                self.stats["deleted"] += 1

    async def run_once(self):
        delivery = self._pending or await self.queue.dequeue_raw()
        if delivery is None:
            return False
        self._pending = delivery
        payload = delivery.get("data", {})
        payload = json.loads(payload) if isinstance(payload, str) else payload
        ctx = RequestContext(
            user=UserIdentifier(payload["account"], payload["user"]), role=Role.ROOT
        )
        if payload["kind"] == "discover":
            config = await resolve_ttl_config(self.fs, ctx.account_id, fresh=True)
            if config is not None and config.enabled:
                for uri in await self._discover(ctx, config):
                    await self.queue.enqueue(
                        {
                            "kind": "delete",
                            "account": ctx.account_id,
                            "user": ctx.user.user_id,
                            "uri": uri,
                        }
                    )
        elif payload["kind"] == "delete":
            self.stats["delete_candidates"] += 1
            if await delete_expired(self.fs, payload["uri"], ctx):
                self.stats["deleted"] += 1
        else:
            raise ValueError("Unknown TTL queue message kind")
        # Strict acknowledgement; failure leaves the durable delivery recoverable.
        await self.queue._ack(AckContext(self.queue.name, delivery["id"], delivery))
        self._pending = None
        return True

    async def _run(self):
        while True:
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.stats["errors"] += 1
                logger.exception("TTL QueueFS delivery failed; retrying unacknowledged delivery")
                await asyncio.sleep(1)
                worked = False
            if not worked:
                await asyncio.sleep(0.2)
