# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SemanticQueue: Semantic extraction queue."""

import threading
import time
from typing import Optional

from openviking.pyagfs.async_client import AsyncAGFSClient
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking_cli.utils.logger import get_logger

from .named_queue import NamedQueue
from .queue_middleware import EnqueueContext
from .semantic_msg import SemanticMsg

logger = get_logger(__name__)

# Coalesce rapid re-enqueues for the same memory parent directory (github #769).
_MEMORY_PARENT_SEMANTIC_DEDUPE_SEC = 45.0


def semantic_revision_key(coalesce_key: str) -> str:
    return f"queue:semantic:{coalesce_key}"


async def is_semantic_coalesce_stale(
    coalesce_key: str, coalesce_version: int, *, agfs: AsyncAGFSClient
) -> bool:
    if not coalesce_key or coalesce_version <= 0:
        return False
    try:
        latest = await agfs.revision_latest(semantic_revision_key(coalesce_key))
    except Exception:
        # Coalescing is an optimization: uncertainty must not discard valid work.
        logger.warning("Cannot read semantic revision; retaining work", exc_info=True)
        return False
    return coalesce_version < latest


async def is_semantic_msg_stale(msg: SemanticMsg, *, agfs: AsyncAGFSClient) -> bool:
    return await is_semantic_coalesce_stale(msg.coalesce_key, msg.coalesce_version, agfs=agfs)


class SemanticQueue(NamedQueue):
    """Semantic extraction queue for async generation of .abstract.md and .overview.md."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._memory_parent_semantic_last: dict[str, float] = {}
        self._memory_parent_semantic_lock = threading.Lock()

    @staticmethod
    def _memory_parent_semantic_key(msg: SemanticMsg) -> str:
        return f"{msg.account_id}|{msg.user_id}|{msg.peer_id}|{msg.uri}"

    async def enqueue(self, msg: SemanticMsg, *, deduplicate_memory: bool = True) -> str:
        """Serialize SemanticMsg object and store in queue."""
        if deduplicate_memory and msg.context_type == "memory" and not msg.coalesce_key:
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

        if msg.coalesce_key and msg.coalesce_version <= 0:
            # Retries retain their original revision, including after restart.
            msg.coalesce_version = await self._async_agfs.revision_allocate(
                semantic_revision_key(msg.coalesce_key)
            )

        msg.queue_enqueued_at = time.time()

        return await super().enqueue(msg.to_dict())

    async def _enqueue(self, ctx: EnqueueContext) -> str:
        # Settle persistence and publication together on cancellation; middleware
        # observes the committed receipt even if its post-enqueue work fails.
        return await run_to_completion(lambda: self._persist_and_publish(ctx))

    async def _persist_and_publish(self, ctx: EnqueueContext) -> str:
        message_id = await super()._enqueue(ctx)
        payload = ctx.payload
        if isinstance(payload, dict) and payload.get("coalesce_key"):
            try:
                await self._async_agfs.revision_publish(
                    semantic_revision_key(payload["coalesce_key"]),
                    payload["coalesce_version"],
                )
            except Exception:
                # The message already exists. Reporting enqueue failure would
                # trigger unnecessary duplicates; an unpublished revision only
                # permits older work to run.
                logger.warning("Semantic work persisted but revision publish failed", exc_info=True)
        return message_id

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
