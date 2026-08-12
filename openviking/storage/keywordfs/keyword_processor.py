# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Async worker that applies KeywordMsg mutations to the FTS5 sidecar."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional

from openviking_cli.utils.logger import get_logger

from ..queuefs.named_queue import DequeueHandlerBase
from .keyword_fs import KeywordFS
from .keyword_msg import Delete, DeletePrefix, Move, Upsert, KeywordMsg

logger = get_logger(__name__)


class KeywordProcessor(DequeueHandlerBase):
    """Consumes KeywordMsg from the Keyword queue and applies them to KeywordFS.

    Messages are dropped when the keyword sidecar is disabled (they should not
    have been enqueued in that case), re-enqueued on transient SQLite errors,
    and reported as errors on permanent failures.
    """

    def __init__(self, keyword_fs: Optional[KeywordFS], config: Optional[Any] = None):
        self._keyword_fs = keyword_fs
        self._config = config

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not data:
            return None
        try:
            raw = data.get("data")
            if isinstance(raw, str):
                msg = KeywordMsg.from_json(raw)
            elif isinstance(raw, dict):
                msg = KeywordMsg.from_dict(raw)
            else:
                msg = KeywordMsg.from_dict(data)
        except Exception as e:
            logger.warning(f"[KeywordProcessor] Failed to parse message: {e}")
            self.report_error(f"Failed to parse KeywordMsg: {e}", data)
            return None

        if self._config is None or not getattr(self._config, "enabled", False):
            self.report_success()
            return None
        kfs = self._keyword_fs
        if kfs is None:
            logger.warning("[KeywordProcessor] KeywordFS not wired; dropping message")
            self.report_success()
            return None

        try:
            if msg.kind == Upsert:
                kfs.upsert(
                    msg.account_id,
                    msg.uri,
                    msg.text,
                    level=msg.level,
                    context_type=msg.context_type,
                    owner_user_id=msg.owner_user_id,
                )
            elif msg.kind == Delete:
                kfs.delete(msg.account_id, msg.uri)
            elif msg.kind == DeletePrefix:
                kfs.delete_prefix(msg.account_id, msg.uri)
            elif msg.kind == Move:
                kfs.move(msg.account_id, msg.old_uri, msg.new_uri)
            else:
                logger.warning(f"[KeywordProcessor] Unknown kind: {msg.kind}")
                self.report_error(f"Unknown KeywordMsg kind: {msg.kind}", data)
                return None
        except sqlite3.OperationalError as e:
            logger.warning(f"[KeywordProcessor] Transient DB error, requeueing: {e}")
            self.report_requeue()
            return None
        except Exception as e:
            logger.error(f"[KeywordProcessor] Failed to apply message: {e}", exc_info=True)
            self.report_error(f"Failed to apply KeywordMsg: {e}", data)
            return None

        self.report_success()
        return None
