# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Pending-signal store for PR radar.

The collector watermark (``.radar-state.json``) tracks how far gh has been
read; this store tracks which collected PRs have not yet been digested into a
published report. A capped collection pass therefore cannot fragment the
report: undigested PRs accumulate here across watch triggers and the renderer
always consumes the full pending set in one digest.

Layout: one JSONL file next to the reports, each line a serialized
:class:`PrSignal`. The file holds ONLY undigested signals -- a successfully
published report clears it, so its size is bounded by one digest window
(plus the ``max_pending_prs`` safety valve).
"""

from __future__ import annotations

import json
from typing import Any, List

from openviking.resource.github_radar.models import (
    SIGNALS_FILENAME,
    PrSignal,
)
from openviking.server.error_mapping import is_not_found_error
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class RadarSignalStore:
    """JSONL-backed queue of PR signals awaiting digest, keyed by PR number."""

    def __init__(self, viking_fs: Any, target_uri: str) -> None:
        self._fs = viking_fs
        self._uri = f"{target_uri}/{SIGNALS_FILENAME}"

    async def load(self, ctx: Any) -> List[PrSignal]:
        try:
            raw = await self._fs.read_file(self._uri, ctx=ctx)
        except Exception as exc:  # noqa: BLE001
            if is_not_found_error(exc):
                return []
            raise
        signals: List[PrSignal] = []
        for line in (raw or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                signals.append(PrSignal.from_row(json.loads(line)))
            except (json.JSONDecodeError, TypeError, KeyError):
                # One corrupt line must not poison the whole pending queue.
                logger.warning("[PrRadar] skipping unreadable signal line in %s", self._uri)
        return signals

    async def save(self, signals: List[PrSignal], ctx: Any) -> None:
        payload = "\n".join(
            json.dumps(signal.to_row(), ensure_ascii=False) for signal in signals
        )
        await self._fs.write_file(self._uri, payload, ctx=ctx)

    async def clear(self, ctx: Any) -> None:
        await self.save([], ctx)

    @staticmethod
    def merge(existing: List[PrSignal], new: List[PrSignal]) -> List[PrSignal]:
        """Merge fresh signals into the pending queue.

        Identity is the ``(number, updated_at)`` pair, not the number alone:

          * an identical re-emit (inclusive ``since`` duplicate) keeps the
            first snapshot, so a digest never lists the same row twice;
          * a NEWER snapshot of an already queued PR is appended as a
            distinct version. The window splitter routes each version to the
            report window containing its own ``updated_at``, so a PR can
            legitimately appear as open in W1 and merged in W2 instead of one
            of those events being silently dropped.

        Per-window duplicate handling lives in ``partition_pending``.
        """
        identities = {(s.number, s.updated_at) for s in existing}
        merged = list(existing)
        for signal in new:
            key = (signal.number, signal.updated_at)
            if key not in identities:
                identities.add(key)
                merged.append(signal)
        return merged
