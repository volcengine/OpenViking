# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Incremental ingest ("新增"): a WatchScheduler-style asyncio poll loop.

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

from openviking.ingest.replay import SessionReplayer
from openviking.ingest.sources.base import LogSource, NotSupportedError
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
        self.poll_interval = min(
            (cfg.poll_interval_seconds for _, cfg, _ in sources), default=5.0
        )

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
        cursor = self.store.get_cursor(name, ref.native_session_id, source.cursor_kind)
        try:
            messages, new_cursor = source.read_messages(ref, cursor)
        except Exception:  # noqa: BLE001
            logger.exception("[ingest:%s] read failed for %s", name, ref.native_session_id)
            return
        if not messages:
            return
        added = await self.replayer.append_session(name, ref, messages, new_cursor)
        if added <= 0:
            return
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
                    await self.replayer.commit_session(
                        d.name, d.ref, d.cfg.commit.keep_recent_count
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("[ingest:%s] idle commit failed", d.name)
                self._dirty.pop(key, None)

    async def _flush_all(self) -> None:
        """Final commit for every dirty session on shutdown."""
        for key, d in list(self._dirty.items()):
            try:
                await self.replayer.commit_session(d.name, d.ref, d.cfg.commit.keep_recent_count)
            except Exception:  # noqa: BLE001
                logger.exception("[ingest:%s] shutdown commit failed", d.name)
            self._dirty.pop(key, None)
