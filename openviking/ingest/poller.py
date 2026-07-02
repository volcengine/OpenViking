# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Incremental ingest (watch mode): a WatchScheduler-style asyncio poll loop.

Mirrors ``openviking/resource/watch_scheduler.py`` (interval polling, graceful start/stop)
rather than depending on filesystem events: a durable read-cursor makes polling correct
and self-healing (a missed tick / sleep / restart just reads cursor->EOF next time).

Each tick, per enabled harness: rescan sessions -> incremental ``read_messages`` from the
stored cursor -> append + advance cursor -> commit on idle or token threshold.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Tuple

from openviking.ingest.models import Cursor
from openviking.ingest.replay import SessionReplayer
from openviking.ingest.sources.base import DEFAULT_READ_LIMIT, LogSource, NotSupportedError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.ingest_config import IngestHarnessConfig

logger = get_logger(__name__)


class _Dirty:
    __slots__ = ("name", "cfg", "source", "ref", "last_activity")

    def __init__(self, name, cfg, source, ref, last_activity):
        self.name = name
        self.cfg = cfg
        self.source = source
        self.ref = ref
        self.last_activity = last_activity


class IngestPoller:
    def __init__(
        self,
        sources: List[Tuple[str, IngestHarnessConfig, LogSource]],
        replayer: SessionReplayer,
    ):
        self._sources = sources
        self.replayer = replayer
        self.store = replayer.store
        self._running = False
        self._stop = asyncio.Event()
        self._dirty: Dict[Tuple[str, str], _Dirty] = {}
        self.poll_interval = min((cfg.poll_interval_seconds for _, cfg, _ in sources), default=5.0)

    def stop(self) -> None:
        self._running = False
        self._stop.set()

    async def run(self) -> None:
        if not self._sources:
            logger.info("[ingest] no harnesses enabled for watch; nothing to do")
            return
        self._running = True
        logger.info(
            "[ingest] watch loop started (poll=%.1fs, harnesses=%s)",
            self.poll_interval,
            [n for n, _, _ in self._sources],
        )
        try:
            while self._running:
                await self._tick()
                await self._commit_idle()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._flush_all()
            logger.info("[ingest] watch loop stopped")

    async def _tick(self) -> None:
        for name, cfg, source in self._sources:
            try:
                refs = list(source.discover_sessions())
            except NotSupportedError as exc:
                logger.warning("[ingest:%s] %s", name, exc)
                continue
            except Exception:  # noqa: BLE001
                logger.exception("[ingest:%s] discovery failed", name)
                continue
            for ref in refs:
                await self._poll_session(name, cfg, source, ref)

    async def _poll_session(self, name, cfg, source, ref) -> None:
        await self.replayer.reconcile(name, ref)
        appended = False
        while True:
            cursor = self.store.get_cursor(name, ref.native_session_id, source.cursor_kind)
            try:
                messages, new_cursor = source.read_messages(ref, cursor, DEFAULT_READ_LIMIT)
            except Exception:  # noqa: BLE001
                logger.exception("[ingest:%s] read failed for %s", name, ref.native_session_id)
                return
            if not messages:
                from_value = (cursor or Cursor.zero(source.cursor_kind)).value
                if new_cursor.value != from_value:
                    sid = self.replayer.ov_session_id(name, ref.native_session_id)
                    self.store.advance_cursor(
                        name, ref.native_session_id, sid, new_cursor, locator=ref.locator
                    )
                break
            from_cursor = cursor or Cursor.zero(source.cursor_kind)
            added = await self.replayer.append_batch(name, ref, messages, from_cursor, new_cursor)
            if added > 0:
                appended = True

        rec = self.store.get(name, ref.native_session_id)
        if appended or (rec and rec.needs_commit):
            self._dirty[(name, ref.native_session_id)] = _Dirty(
                name, cfg, source, ref, time.monotonic()
            )
            committed = await self.replayer.maybe_commit_on_threshold(
                name, ref, cfg.commit.commit_token_threshold, cfg.commit.keep_recent_count
            )
            if committed:
                self._dirty.pop((name, ref.native_session_id), None)

    async def _commit_idle(self) -> None:
        now = time.monotonic()
        for key, d in list(self._dirty.items()):
            if now - d.last_activity >= d.cfg.commit.commit_idle_seconds:
                try:
                    await self.replayer.commit_if_needed(
                        d.name, d.ref, d.cfg.commit.keep_recent_count
                    )
                    self._dirty.pop(key, None)  # only drop on success
                except Exception:  # noqa: BLE001 - keep dirty and retry after a backoff
                    logger.exception("[ingest:%s] idle commit failed; will retry", d.name)
                    d.last_activity = time.monotonic()

    async def _flush_all(self) -> None:
        """Final commit for every dirty session on shutdown (needs_commit persists if it fails)."""
        for key, d in list(self._dirty.items()):
            try:
                await self.replayer.commit_if_needed(d.name, d.ref, d.cfg.commit.keep_recent_count)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[ingest:%s] shutdown commit failed (needs_commit persisted)", d.name
                )
            self._dirty.pop(key, None)
