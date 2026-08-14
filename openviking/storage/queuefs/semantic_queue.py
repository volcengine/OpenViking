# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SemanticQueue: Semantic extraction queue."""

import asyncio
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Optional

from openviking.service.task_work_index import get_task_context
from openviking_cli.utils.logger import get_logger

from .named_queue import NamedQueue
from .semantic_msg import SemanticMsg

logger = get_logger(__name__)

# Coalesce rapid re-enqueues for the same memory parent directory (github #769).
_MEMORY_PARENT_SEMANTIC_DEDUPE_SEC = 45.0


@dataclass
class _SemanticBatch:
    msg: SemanticMsg
    events: list[dict[str, str]]


@dataclass
class _SemanticCoalesceState:
    trigger_id: str
    status: str
    pending: Optional[_SemanticBatch]
    enqueue_result: Future[str]
    active: Optional[_SemanticBatch] = None
    dirty: Optional[_SemanticBatch] = None


class _InMemorySemanticCoalescer:
    """Keep one process-local queue trigger per semantic key and merge work around it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, _SemanticCoalesceState] = {}

    @staticmethod
    def _clone_msg(msg: SemanticMsg) -> SemanticMsg:
        return SemanticMsg.from_dict(msg.to_dict())

    @classmethod
    def _batch_from_msg(cls, msg: SemanticMsg) -> _SemanticBatch:
        events = getattr(msg, "_coalesced_events", None)
        if events:
            completions = [dict(event) for event in events]
        else:
            completions = [{"id": msg.id, "telemetry_id": msg.telemetry_id}]
        return _SemanticBatch(cls._clone_msg(msg), completions)

    @classmethod
    def _merge_batches(
        cls,
        current: Optional[_SemanticBatch],
        incoming: _SemanticBatch,
    ) -> _SemanticBatch:
        if current is None:
            return incoming

        merged = cls._clone_msg(incoming.msg)
        merged.recursive = current.msg.recursive or incoming.msg.recursive
        merged.skip_vectorization = (
            current.msg.skip_vectorization and incoming.msg.skip_vectorization
        )
        merged.is_code_repo = current.msg.is_code_repo or incoming.msg.is_code_repo

        if current.msg.changes is None or incoming.msg.changes is None:
            merged.changes = None
        else:
            path_states: dict[str, str] = {}
            for changes in (current.msg.changes, incoming.msg.changes):
                for change_type in ("added", "modified", "deleted"):
                    for path in changes.get(change_type, []):
                        path_states[path] = change_type
            merged.changes = {
                change_type: sorted(
                    path for path, state in path_states.items() if state == change_type
                )
                for change_type in ("added", "modified", "deleted")
                if change_type in path_states.values()
            }

        events = list(current.events)
        known_ids = {event["id"] for event in events}
        events.extend(event for event in incoming.events if event["id"] not in known_ids)
        return _SemanticBatch(merged, events)

    @classmethod
    def _claimed_msg(cls, state: _SemanticCoalesceState) -> SemanticMsg:
        assert state.active is not None
        msg = cls._clone_msg(state.active.msg)
        msg._coalesced_events = [dict(event) for event in state.active.events]
        msg._coalesce_trigger_id = state.trigger_id
        return msg

    def submit(self, msg: SemanticMsg) -> tuple[bool, Future[str]]:
        incoming = self._batch_from_msg(msg)
        with self._lock:
            state = self._states.get(msg.coalesce_key)
            if state is None:
                enqueue_result: Future[str] = Future()
                self._states[msg.coalesce_key] = _SemanticCoalesceState(
                    trigger_id=msg.id,
                    status="enqueuing",
                    pending=incoming,
                    enqueue_result=enqueue_result,
                )
                return True, enqueue_result

            if state.status in {"enqueuing", "pending"}:
                state.pending = self._merge_batches(state.pending, incoming)
            else:
                state.dirty = self._merge_batches(state.dirty, incoming)
            return False, state.enqueue_result

    def enqueue_succeeded(self, key: str, future: Future[str], result: str) -> None:
        with self._lock:
            state = self._states.get(key)
            if state is not None and state.enqueue_result is future:
                if state.status == "enqueuing":
                    state.status = "pending"
        if not future.done():
            future.set_result(result)

    def enqueue_failed(self, key: str, future: Future[str], error: BaseException) -> None:
        with self._lock:
            state = self._states.get(key)
            if state is not None and state.enqueue_result is future:
                self._states.pop(key, None)
        if not future.done():
            future.set_exception(error)

    def claim(self, msg: SemanticMsg) -> SemanticMsg:
        if not msg.coalesce_key:
            return msg
        with self._lock:
            state = self._states.get(msg.coalesce_key)
            if state is None or state.trigger_id != msg.id:
                return msg
            if state.status in {"enqueuing", "pending"} and state.pending is not None:
                state.status = "processing"
                state.active = state.pending
                state.pending = None
            elif state.status == "processing" and state.active is not None:
                state.active = self._merge_batches(state.active, state.dirty)
                state.dirty = None
            else:
                return msg
            return self._claimed_msg(state)

    def finish(self, msg: SemanticMsg) -> Optional[SemanticMsg]:
        trigger_id = getattr(msg, "_coalesce_trigger_id", "")
        if not trigger_id:
            return None
        with self._lock:
            state = self._states.get(msg.coalesce_key)
            if state is None or state.trigger_id != trigger_id:
                return None
            if state.dirty is None:
                self._states.pop(msg.coalesce_key, None)
                return None
            state.active = state.dirty
            state.dirty = None
            return self._claimed_msg(state)

    def abort(self, msg: SemanticMsg) -> SemanticMsg:
        trigger_id = getattr(msg, "_coalesce_trigger_id", "")
        if not trigger_id:
            return msg
        with self._lock:
            state = self._states.get(msg.coalesce_key)
            if state is None or state.trigger_id != trigger_id or state.active is None:
                return msg
            batch = self._merge_batches(state.active, state.dirty) if state.dirty else state.active
            self._states.pop(msg.coalesce_key, None)
        aborted = self._clone_msg(batch.msg)
        aborted._coalesced_events = [dict(event) for event in batch.events]
        return aborted

    def prepare_retry(self, msg: SemanticMsg) -> SemanticMsg:
        return self.abort(msg)

    def clear(self) -> None:
        with self._lock:
            self._states.clear()


_SEMANTIC_COALESCER = _InMemorySemanticCoalescer()


def claim_semantic_message(msg: SemanticMsg) -> SemanticMsg:
    return _SEMANTIC_COALESCER.claim(msg)


def finish_semantic_message(msg: SemanticMsg) -> Optional[SemanticMsg]:
    return _SEMANTIC_COALESCER.finish(msg)


def abort_semantic_message(msg: SemanticMsg) -> SemanticMsg:
    return _SEMANTIC_COALESCER.abort(msg)


def prepare_semantic_retry(msg: SemanticMsg) -> SemanticMsg:
    return _SEMANTIC_COALESCER.prepare_retry(msg)


class SemanticQueue(NamedQueue):
    """Semantic extraction queue for async generation of .abstract.md and .overview.md."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._memory_parent_semantic_last: dict[str, float] = {}
        self._memory_parent_semantic_lock = threading.Lock()

    @staticmethod
    def _memory_parent_semantic_key(msg: SemanticMsg) -> str:
        return f"{msg.account_id}|{msg.user_id}|{msg.peer_id}|{msg.uri}"

    async def enqueue(self, msg: SemanticMsg) -> str:
        """Serialize SemanticMsg object and store in queue."""
        if msg.context_type == "memory" and not msg.coalesce_key:
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

        if not msg.coalesce_key or get_task_context() is not None or msg.lock_handoff is not None:
            return await super().enqueue(msg.to_dict())

        should_enqueue, enqueue_result = _SEMANTIC_COALESCER.submit(msg)
        if not should_enqueue:
            return await asyncio.shield(asyncio.wrap_future(enqueue_result))

        try:
            # The durable trigger falls back to a full refresh if this process
            # restarts and loses the in-memory incremental batch.
            trigger = msg.to_dict()
            trigger["changes"] = None
            trigger["skip_vectorization"] = False
            result = await super().enqueue(trigger)
        except BaseException as error:
            _SEMANTIC_COALESCER.enqueue_failed(msg.coalesce_key, enqueue_result, error)
            raise
        _SEMANTIC_COALESCER.enqueue_succeeded(msg.coalesce_key, enqueue_result, result)
        return result

    async def clear(self) -> bool:
        cleared = await super().clear()
        if cleared:
            _SEMANTIC_COALESCER.clear()
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
