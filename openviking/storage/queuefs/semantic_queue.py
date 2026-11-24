# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SemanticQueue: Semantic extraction queue."""

import time
from hashlib import sha256
from typing import Optional

from openviking.pyagfs.async_client import AsyncAGFSClient
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking_cli.utils.logger import get_logger

from .named_queue import NamedQueue
from .queue_middleware import EnqueueContext
from .semantic_msg import SemanticMsg

logger = get_logger(__name__)


def semantic_revision_key(coalesce_key: str) -> str:
    return f"queue:semantic:{coalesce_key}"


async def is_semantic_coalesce_stale(
    coalesce_key: str, coalesce_version: int, *, agfs: AsyncAGFSClient
) -> bool:
    if not coalesce_key or coalesce_version <= 0:
        return False
    return coalesce_version < await latest_semantic_revision(coalesce_key, agfs=agfs)


async def latest_semantic_revision(coalesce_key: str, *, agfs: AsyncAGFSClient) -> int:
    if not coalesce_key:
        return 0
    try:
        return await agfs.revision_latest(semantic_revision_key(coalesce_key))
    except Exception as _log_error:
        # Coalescing is an optimization: uncertainty must not discard valid work.
        logger.info(
            "Cannot read semantic revision; retaining work error_type=%s", type(_log_error).__name__
        )
        return 0


async def is_semantic_msg_stale(msg: SemanticMsg, *, agfs: AsyncAGFSClient) -> bool:
    return await is_semantic_coalesce_stale(msg.coalesce_key, msg.coalesce_version, agfs=agfs)


class SemanticQueue(NamedQueue):
    """Semantic extraction queue for async generation of .abstract.md and .overview.md."""

    async def enqueue(self, msg: SemanticMsg) -> str:
        """Serialize SemanticMsg object and store in queue."""
        if msg.coalesce_key and msg.coalesce_version <= 0:
            # Retries retain their original revision, including after restart.
            msg.coalesce_version = await self._async_agfs.revision_allocate(
                semantic_revision_key(msg.coalesce_key)
            )

        msg.queue_enqueued_at = time.time()

        serial_key = None
        if msg.generation_trigger == "directory_refresh":
            # Let QueueFS wait for the active refresh to release its sidecar lease.
            serial_key = sha256(f"{msg.account_id}|{msg.uri}".encode()).hexdigest()
        return await super().enqueue(msg.to_dict(), serial_key=serial_key)

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
            except Exception as _log_error:
                # The message already exists. Reporting enqueue failure would
                # trigger unnecessary duplicates; an unpublished revision only
                # permits older work to run.
                logger.info(
                    "Semantic work persisted but revision publish failed error_type=%s",
                    type(_log_error).__name__,
                )
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
                logger.debug("Failed to parse message data: %s", type(e).__name__)
                return None

        try:
            return SemanticMsg.from_dict(data_dict)
        except Exception as e:
            logger.debug("Failed to create SemanticMsg from dict: %s", type(e).__name__)
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
