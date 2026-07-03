# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Backfill orchestration: replay each discovered session cursor->end, then commit.

Incremental watch mode is handled by ``IngestPoller`` (``poller.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from openviking.ingest.models import Cursor
from openviking.ingest.registry import iter_enabled_sources
from openviking.ingest.replay import SessionReplayer
from openviking.ingest.sources.base import DEFAULT_READ_LIMIT, LogSource, NotSupportedError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.ingest_config import IngestConfig, IngestHarnessConfig

logger = get_logger(__name__)


@dataclass
class BackfillStats:
    sessions: int = 0
    messages: int = 0
    committed: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)

    def merge(self, other: "BackfillStats") -> None:
        self.sessions += other.sessions
        self.messages += other.messages
        self.committed += other.committed
        self.skipped += other.skipped
        self.errors.extend(other.errors)


def enabled_sources(
    config: IngestConfig, only: Optional[List[str]] = None
) -> List[Tuple[str, IngestHarnessConfig, LogSource]]:
    """Construct enabled sources, optionally filtered to ``only`` harness names."""
    out = []
    for name, cfg, source in iter_enabled_sources(config):
        if only and name not in only:
            continue
        out.append((name, cfg, source))
    return out


class IngestOrchestrator:
    def __init__(self, config: IngestConfig, replayer: SessionReplayer):
        self.config = config
        self.replayer = replayer
        self.store = replayer.store

    async def backfill_source(
        self,
        name: str,
        harness_cfg: IngestHarnessConfig,
        source: LogSource,
        *,
        since: Optional[str] = None,
        dry_run: bool = False,
        reset: bool = False,
    ) -> BackfillStats:
        stats = BackfillStats()
        try:
            refs = list(source.discover_sessions())
        except NotSupportedError as exc:
            logger.warning("[ingest:%s] %s", name, exc)
            stats.errors.append(f"{name}: {exc}")
            return stats
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ingest:%s] discovery failed", name)
            stats.errors.append(f"{name}: discovery failed: {exc}")
            return stats

        for ref in refs:
            if since and ref.started_at and ref.started_at < since:
                stats.skipped += 1
                continue
            try:
                if not dry_run:
                    if reset:
                        await self.replayer.reset_session(name, ref)
                    else:
                        await self.replayer.reconcile(name, ref)
                added = await self._backfill_one(name, source, ref, dry_run=dry_run)
                stats.sessions += 1
                stats.messages += added
                if not dry_run:
                    # Commit even when added == 0: a prior crash may have left appended
                    # messages un-committed (needs_commit / server pending tokens).
                    if await self.replayer.commit_if_needed(
                        name, ref, harness_cfg.commit.keep_recent_count
                    ):
                        stats.committed += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("[ingest:%s] session %s failed", name, ref.native_session_id)
                stats.errors.append(f"{name}/{ref.native_session_id}: {exc}")
        return stats

    async def _backfill_one(self, name, source, ref, *, dry_run: bool) -> int:
        cursor = self.store.get_cursor(name, ref.native_session_id, source.cursor_kind)
        total = 0
        while True:
            messages, new_cursor = source.read_messages(ref, cursor, DEFAULT_READ_LIMIT)
            if not messages:
                from_value = (cursor or Cursor.zero(source.cursor_kind)).value
                if not dry_run and new_cursor.value != from_value:
                    sid = self.replayer.ov_session_id(name, ref.native_session_id)
                    self.store.advance_cursor(
                        name, ref.native_session_id, sid, new_cursor, locator=ref.locator
                    )
                break
            if dry_run:
                total += sum(1 for m in messages if (m.text or "").strip() or m.parts)
            else:
                from_cursor = cursor or Cursor.zero(source.cursor_kind)
                total += await self.replayer.append_batch(
                    name, ref, messages, from_cursor, new_cursor
                )
            cursor = new_cursor
        return total

    async def backfill(
        self,
        only: Optional[List[str]] = None,
        *,
        since: Optional[str] = None,
        dry_run: bool = False,
        reset: bool = False,
    ) -> Dict[str, BackfillStats]:
        results: Dict[str, BackfillStats] = {}
        for name, cfg, source in enabled_sources(self.config, only):
            if cfg.mode not in ("backfill", "both") and only is None:
                continue
            logger.info("[ingest] backfilling %s (dry_run=%s)", name, dry_run)
            results[name] = await self.backfill_source(
                name, cfg, source, since=since, dry_run=dry_run, reset=reset
            )
        return results
