# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SemanticQueue: Semantic extraction queue."""

from typing import Optional

from openviking.service.coordinator import get_coordinator
from openviking_cli.utils.logger import get_logger

from .named_queue import NamedQueue
from .semantic_msg import SemanticMsg

logger = get_logger(__name__)

# Coalesce rapid re-enqueues for the same memory parent directory (github #769).
_MEMORY_PARENT_SEMANTIC_DEDUPE_SEC = 45.0


def _coalesce_coord_key(coalesce_key: str) -> str:
    return f"coalesce:{coalesce_key}"


def is_semantic_coalesce_stale(coalesce_key: str, coalesce_version: int) -> bool:
    if not coalesce_key or coalesce_version <= 0:
        return False
    # Strongly-consistent read: the Coordinator backend (in-process dict or
    # Redis) guarantees this reflects the latest enqueue's incr, including the
    # lock-inside TOCTOU re-check in write_semantic_sidecars.
    current = get_coordinator().get_int(_coalesce_coord_key(coalesce_key))
    return coalesce_version < current


def is_semantic_msg_stale(msg: SemanticMsg) -> bool:
    return is_semantic_coalesce_stale(msg.coalesce_key, msg.coalesce_version)


def _memory_dedupe_coord_key(key: str) -> str:
    return f"mem_dedupe:{key}"


class SemanticQueue(NamedQueue):
    """Semantic extraction queue for async generation of .abstract.md and .overview.md."""

    @staticmethod
    def _memory_parent_semantic_key(msg: SemanticMsg) -> str:
        return f"{msg.account_id}|{msg.user_id}|{msg.agent_id}|{msg.uri}"

    async def enqueue(self, msg: SemanticMsg) -> str:
        """Serialize SemanticMsg object and store in queue."""
        if msg.context_type == "memory" and not msg.coalesce_key:
            key = self._memory_parent_semantic_key(msg)
            coord = get_coordinator()
            coord_key = _memory_dedupe_coord_key(key)
            # Atomic claim: only the first caller within the dedupe window wins.
            # SET NX EX is a single round-trip, so concurrent enqueues across
            # instances cannot both pass the check (no get-then-set TOCTOU).
            # The window is the claim's TTL; it re-arms once it expires.
            claimed = coord.set_if_absent(coord_key, int(_MEMORY_PARENT_SEMANTIC_DEDUPE_SEC))
            if not claimed:
                logger.debug(
                    "[SemanticQueue] Skipping duplicate memory semantic enqueue for %s "
                    "(within %.0fs dedupe window; see #769)",
                    msg.uri,
                    _MEMORY_PARENT_SEMANTIC_DEDUPE_SEC,
                )
                return "deduplicated"

        if msg.coalesce_key:
            # Atomic, cross-instance-safe version bump via the Coordinator.
            msg.coalesce_version = get_coordinator().incr(_coalesce_coord_key(msg.coalesce_key))

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
