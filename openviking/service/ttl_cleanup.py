# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""B: enqueue known expired objects; no discovery queries or idle queue polling."""

import asyncio
import json
import logging
import time

from openviking.server.identity import RequestContext, Role
from openviking.service.ttl_deletion import delete_expired
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.queue_middleware import AckContext
from openviking.storage.ttl import deletion_uri
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
        self._wake = asyncio.Event()
        self._tasks = set()
        self._access = {}
        self._closed = False
        self._pending = None
        self.stats = {"enqueued": 0, "delete_candidates": 0, "deleted": 0, "errors": 0}

    async def start(self):
        if self.settings.enabled:
            self._loop = asyncio.get_running_loop()
            if self.settings.execution == "async":
                # Recover durable deliveries once. There is no source scan and
                # no recurring read of an empty queue.
                self._wake.set()
                self._worker = asyncio.create_task(self._run())

    async def close(self):
        self._closed = True
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)

    def on_expired(self, uri, ctx):
        """Caller already checked expiry using metadata its normal access held."""
        target = deletion_uri(uri)
        if self._closed or not self.settings.enabled or target is None:
            return

        def schedule():
            key = (ctx.account_id, target)
            now = time.monotonic()
            if now - self._access.get(key, float("-inf")) < self.settings.check_interval_seconds:
                return
            if len(self._access) >= 1024:
                self._access.clear()
            self._access[key] = now
            task = asyncio.create_task(self._enqueue(target, ctx, key))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        if self._loop is None or self._loop is asyncio.get_running_loop():
            schedule()
        else:
            self._loop.call_soon_threadsafe(schedule)

    async def _enqueue(self, uri, ctx, key):
        try:
            await self.queue.enqueue(
                {"kind": "delete", "account": ctx.account_id, "user": ctx.user.user_id, "uri": uri}
            )
            self.stats["enqueued"] += 1
            self._wake.set()
        except Exception:
            self.stats["errors"] += 1
            self._access.pop(key, None)
            logger.exception("TTL deletion enqueue failed; next access can retry")

    async def delete_known_sync(self, uri, ctx):
        """Explicit experiment: wait for deletion of this already known URI."""
        if self.settings.enabled and not self._closed and deletion_uri(uri) is not None:
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
        if payload["kind"] == "delete":
            ctx = RequestContext(
                user=UserIdentifier(payload["account"], payload["user"]), role=Role.ROOT
            )
            self.stats["delete_candidates"] += 1
            if await delete_expired(self.fs, payload["uri"], ctx):
                self.stats["deleted"] += 1
        elif payload["kind"] != "discover":
            raise ValueError("Unknown TTL queue message kind")
        # Discard obsolete B0 discovery notifications without issuing a query.
        await self.queue._ack(AckContext(self.queue.name, delivery["id"], delivery))
        self._pending = None
        return True

    async def _run(self):
        while True:
            await self._wake.wait()
            self._wake.clear()
            while True:
                try:
                    worked = await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.stats["errors"] += 1
                    logger.exception("TTL deletion failed; retrying durable delivery")
                    await asyncio.sleep(1)
                    continue
                if not worked:
                    break
