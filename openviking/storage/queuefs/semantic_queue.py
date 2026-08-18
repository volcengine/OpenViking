# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SemanticQueue: Semantic extraction queue."""

import asyncio
import json
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Optional

from openviking.service.task_work_index import extract_task_metadata, get_task_context
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.utils.logger import get_logger

from .named_queue import NamedQueue
from .semantic_msg import SemanticMsg, semantic_directory_key

logger = get_logger(__name__)

# Coalesce rapid re-enqueues for the same memory parent directory (github #769).
_MEMORY_PARENT_SEMANTIC_DEDUPE_SEC = 45.0


@dataclass
class _PendingDirectoryRefresh:
    trigger_id: str
    queue_id: str
    revision: int
    events: dict[str, str]
    enqueue_result: Future[str]


class SemanticQueue(NamedQueue):
    """Semantic extraction queue for async generation of .abstract.md and .overview.md."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._memory_parent_semantic_last: dict[str, float] = {}
        self._memory_parent_semantic_lock = threading.Lock()
        self._directory_refreshes: dict[
            tuple[str, str, str], _PendingDirectoryRefresh
        ] = {}
        self._directory_refresh_lock = threading.Lock()

    @staticmethod
    def _memory_parent_semantic_key(msg: SemanticMsg) -> str:
        return f"{msg.account_id}|{msg.user_id}|{msg.peer_id}|{msg.uri}"

    @staticmethod
    def is_directory_refresh(msg: SemanticMsg) -> bool:
        return bool(
            msg.directory_refresh_only
            or (
                msg.context_type in {"resource", "skill"}
                and not msg.recursive
                and msg.changes
                and set(msg.changes) == {"deleted"}
            )
        )

    @staticmethod
    def _directory_key(msg: SemanticMsg) -> tuple[str, str, str]:
        return semantic_directory_key(
            context_type=msg.context_type,
            uri=msg.target_uri or msg.uri,
            account_id=msg.account_id,
        )

    @staticmethod
    def _observe_future(future: Future[str]) -> None:
        if not future.cancelled():
            future.exception()

    def _join_directory_refresh(
        self, msg: SemanticMsg
    ) -> tuple[bool, Future[str]]:
        key = self._directory_key(msg)
        with self._directory_refresh_lock:
            state = self._directory_refreshes.get(key)
            if state is None:
                enqueue_result: Future[str] = Future()
                enqueue_result.add_done_callback(self._observe_future)
                self._directory_refreshes[key] = _PendingDirectoryRefresh(
                    trigger_id=msg.id,
                    queue_id="",
                    revision=1,
                    events={msg.id: msg.telemetry_id},
                    enqueue_result=enqueue_result,
                )
                return True, enqueue_result
            if msg.id == state.trigger_id:
                state.queue_id = ""
                return True, state.enqueue_result
            state.revision += 1
            state.events.setdefault(msg.id, msg.telemetry_id)
            return False, state.enqueue_result

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
                    for stale_key in stale[:800]:
                        self._memory_parent_semantic_last.pop(stale_key, None)

        if not msg.directory_refresh_only:
            return await super().enqueue(msg.to_dict())

        # A directory trigger reads the current AGFS state. Child paths and tags
        # belong to file work and must not be carried into this message.
        msg.changes = None
        msg.ingest_options = IngestOptions()
        msg.tag_parent_directory = False

        # Task-owned messages keep their own durable lifecycle.
        if get_task_context() is not None or msg.lock_handoff is not None:
            return await super().enqueue(msg.to_dict())

        leader, enqueue_result = self._join_directory_refresh(msg)
        if not leader:
            return await asyncio.shield(asyncio.wrap_future(enqueue_result))

        try:
            result = await super().enqueue(msg.to_dict())
        except BaseException as error:
            with self._directory_refresh_lock:
                state = self._directory_refreshes.get(self._directory_key(msg))
                if state is not None and state.trigger_id == msg.id:
                    self._directory_refreshes.pop(self._directory_key(msg), None)
            if not enqueue_result.done():
                enqueue_result.set_exception(error)
            raise
        with self._directory_refresh_lock:
            state = self._directory_refreshes.get(self._directory_key(msg))
            if state is not None and state.trigger_id == msg.id:
                state.queue_id = result
        if not enqueue_result.done():
            enqueue_result.set_result(result)
        return result

    def restore_directory_refreshes(self, messages: list[dict]) -> None:
        """Rebuild pending directory triggers before workers start."""
        with self._directory_refresh_lock:
            self._directory_refreshes.clear()
            for envelope in messages:
                if extract_task_metadata(envelope) is not None:
                    continue
                try:
                    payload = envelope.get("data", envelope)
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    msg = SemanticMsg.from_dict(payload)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not self.is_directory_refresh(msg):
                    continue
                key = self._directory_key(msg)
                state = self._directory_refreshes.get(key)
                if state is not None:
                    state.events.setdefault(msg.id, msg.telemetry_id)
                    continue
                enqueue_result: Future[str] = Future()
                queue_id = str(envelope.get("id", msg.id))
                enqueue_result.set_result(queue_id)
                self._directory_refreshes[key] = _PendingDirectoryRefresh(
                    trigger_id=msg.id,
                    queue_id=queue_id,
                    revision=1,
                    events={msg.id: msg.telemetry_id},
                    enqueue_result=enqueue_result,
                )

    def claim_directory_refresh(
        self, msg: SemanticMsg, queue_id: str
    ) -> Optional[int]:
        with self._directory_refresh_lock:
            state = self._directory_refreshes.get(self._directory_key(msg))
            if state is None:
                return 0
            if state.queue_id and state.queue_id != queue_id:
                return None
            return state.revision

    def directory_refresh_events(self, msg: SemanticMsg) -> dict[str, str]:
        with self._directory_refresh_lock:
            state = self._directory_refreshes.get(self._directory_key(msg))
            if state is None or state.trigger_id != msg.id:
                return {msg.id: msg.telemetry_id}
            return dict(state.events)

    def finish_directory_refresh(
        self, msg: SemanticMsg, revision: int
    ) -> Optional[dict[str, str]]:
        with self._directory_refresh_lock:
            key = self._directory_key(msg)
            state = self._directory_refreshes.get(key)
            if state is None or state.trigger_id != msg.id:
                return {msg.id: msg.telemetry_id}
            if state.revision != revision:
                return None
            self._directory_refreshes.pop(key, None)
            return dict(state.events)

    def abort_directory_refresh(self, msg: SemanticMsg) -> dict[str, str]:
        with self._directory_refresh_lock:
            key = self._directory_key(msg)
            state = self._directory_refreshes.get(key)
            if state is None or state.trigger_id != msg.id:
                return {msg.id: msg.telemetry_id}
            self._directory_refreshes.pop(key, None)
            return dict(state.events)

    async def clear(self) -> bool:
        cleared = await super().clear()
        if cleared:
            with self._directory_refresh_lock:
                self._directory_refreshes.clear()
        return cleared

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
