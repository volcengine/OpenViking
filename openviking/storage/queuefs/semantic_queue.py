# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SemanticQueue: Semantic extraction queue."""

import threading
import time
from typing import Optional

from openviking_cli.utils.logger import get_logger

from .named_queue import NamedQueue
from .semantic_msg import SemanticMsg

logger = get_logger(__name__)

# Coalesce rapid re-enqueues for the same memory parent directory (github #769).
_MEMORY_PARENT_SEMANTIC_DEDUPE_SEC = 45.0


class SemanticQueue(NamedQueue):
    """Semantic extraction queue for async generation of .abstract.md and .overview.md."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._memory_parent_semantic_last: dict[str, float] = {}
        self._memory_parent_semantic_lock = threading.Lock()

    @staticmethod
    def _memory_parent_semantic_key(msg: SemanticMsg) -> str:
        return f"{msg.account_id}|{msg.user_id}|{msg.agent_id}|{msg.uri}"

    async def enqueue(self, msg: SemanticMsg) -> str:
        """Serialize SemanticMsg object and store in queue."""
        if msg.context_type == "memory":
            key = self._memory_parent_semantic_key(msg)
            now = time.monotonic()
            with self._memory_parent_semantic_lock:
                last = self._memory_parent_semantic_last.get(key, 0.0)
                if now - last < _MEMORY_PARENT_SEMANTIC_DEDUPE_SEC:
                    logger.debug(
                        "[SemanticQueue] Skipping duplicate memory semantic enqueue for %s "
                        "(within %.0fs dedupe window; see #769)",
                        msg.uri,
                        _MEMORY_PARENT_SEMANTIC_DEDUPE_SEC,
                    )
                    return "deduplicated"
                self._memory_parent_semantic_last[key] = now
                if len(self._memory_parent_semantic_last) > 2000:
                    cutoff = now - (_MEMORY_PARENT_SEMANTIC_DEDUPE_SEC * 4)
                    stale = [k for k, t in self._memory_parent_semantic_last.items() if t < cutoff]
                    for k in stale[:800]:
                        self._memory_parent_semantic_last.pop(k, None)

        return await super().enqueue(msg.to_dict())

    async def dequeue(self) -> Optional[SemanticMsg]:
        """Get message from queue and deserialize to SemanticMsg object."""
        data_dict = await super().dequeue()
        if not data_dict:
            return None

        if "data" in data_dict and isinstance(data_dict["data"], str):
            try:
                return SemanticMsg.from_json(data_dict["data"])
            except Exception as e:
                logger.debug(f"[SemanticQueue] Failed to parse message data: {e}")
                return None

        try:
            return SemanticMsg.from_dict(data_dict)
        except Exception as e:
            logger.debug(f"[SemanticQueue] Failed to create SemanticMsg from dict: {e}")
            return None

    async def peek(self) -> Optional[SemanticMsg]:
        """Peek at message from queue."""
        data_dict = await super().peek()
        if not data_dict:
            return None

        if "data" in data_dict and isinstance(data_dict["data"], str):
            try:
                return SemanticMsg.from_json(data_dict["data"])
            except Exception:
                return None

        try:
            return SemanticMsg.from_dict(data_dict)
        except Exception:
            return None
