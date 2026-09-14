# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Named queue for keyword-sidecar mutations."""

from __future__ import annotations

from typing import Optional

from openviking_cli.utils.logger import get_logger

from ..queuefs.named_queue import NamedQueue
from .keyword_msg import KeywordMsg

logger = get_logger(__name__)


class KeywordQueue(NamedQueue):
    """KeywordQueue: Named queue specifically for KeywordMsg objects."""

    async def enqueue(self, msg: Optional[KeywordMsg]) -> str:
        """Serialize a KeywordMsg object and store it in the queue."""
        if msg is None:
            logger.warning("Keyword message is None, skipping enqueuing")
            return ""
        return await super().enqueue(msg.to_dict())

    async def dequeue(self) -> Optional[KeywordMsg]:
        """Get a message from the queue and deserialize to KeywordMsg."""
        data_dict = await super().dequeue()
        if not data_dict:
            return None
        raw = data_dict.get("data")
        try:
            if isinstance(raw, str):
                return KeywordMsg.from_json(raw)
            if isinstance(raw, dict):
                return KeywordMsg.from_dict(raw)
        except Exception as e:
            logger.debug(f"[KeywordQueue] Failed to parse message data: {e}")
            return None
        try:
            return KeywordMsg.from_dict(data_dict)
        except Exception:
            return None
