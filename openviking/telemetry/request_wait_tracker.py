# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-scoped wait tracker for write APIs."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from openviking.service.task_processing_time import pause_task_processing


@dataclass
class _RequestWaitState:
    pending_semantic_roots: Set[str] = field(default_factory=set)
    pending_embedding_roots: Set[str] = field(default_factory=set)
    semantic_processed: int = 0
    semantic_requeue_count: int = 0
    semantic_error_count: int = 0
    semantic_errors: List[str] = field(default_factory=list)
    embedding_processed: int = 0
    embedding_context_count: int = 0
    embedding_requeue_count: int = 0
    embedding_error_count: int = 0
    embedding_errors: List[str] = field(default_factory=list)
    semantic_queue_wait_ms: float = 0.0
    semantic_execute_ms: float = 0.0
    embedding_queue_wait_ms: float = 0.0
    embedding_execute_ms: float = 0.0
    created_at: float = field(default_factory=time.time)
    retained_roots: Set[str] = field(default_factory=set)
    cleanup_requested: bool = False


class RequestWaitTracker:
    """Track request-scoped queue completion using telemetry_id."""

    _instance: Optional["RequestWaitTracker"] = None

    def __new__(cls) -> "RequestWaitTracker":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_lock"):
            return
        self._lock = threading.Lock()
        self._states: Dict[str, _RequestWaitState] = {}

    @classmethod
    def get_instance(cls) -> "RequestWaitTracker":
        return cls()

    def _create_state(self, telemetry_id: str) -> Optional[_RequestWaitState]:
        if not telemetry_id:
            return None
        with self._lock:
            return self._states.setdefault(telemetry_id, _RequestWaitState())

    def register_request(self, telemetry_id: str) -> None:
        self._create_state(telemetry_id)

    def has_request(self, telemetry_id: str) -> bool:
        with self._lock:
            return telemetry_id in self._states

    def retain_request(self, telemetry_id: str, root_id: str) -> None:
        """Keep a Skill worker's accounting alive if its HTTP waiter times out."""
        if not telemetry_id or not root_id:
            return
        with self._lock:
            state = self._states.setdefault(telemetry_id, _RequestWaitState())
            state.retained_roots.add(root_id)

    def _release_retained_root(
        self, telemetry_id: str, root_id: str, state: _RequestWaitState
    ) -> None:
        state.retained_roots.discard(root_id)
        if state.cleanup_requested and not state.retained_roots:
            self._states.pop(telemetry_id, None)

    def register_semantic_root(self, telemetry_id: str, root_id: str) -> None:
        if not telemetry_id or not root_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.pending_semantic_roots.add(root_id)

    def register_embedding_root(self, telemetry_id: str, root_id: str) -> None:
        if not telemetry_id or not root_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.pending_embedding_roots.add(root_id)

    def record_embedding_requeue(self, telemetry_id: str, delta: int = 1) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.embedding_requeue_count += max(delta, 0)

    def record_semantic_timing(
        self, telemetry_id: str, *, queue_wait_ms: float = 0.0, execute_ms: float = 0.0
    ) -> None:
        self._record_timing(
            telemetry_id,
            queue_wait_ms=queue_wait_ms,
            execute_ms=execute_ms,
            queue_wait_attr="semantic_queue_wait_ms",
            execute_attr="semantic_execute_ms",
        )

    def record_embedding_timing(
        self, telemetry_id: str, *, queue_wait_ms: float = 0.0, execute_ms: float = 0.0
    ) -> None:
        self._record_timing(
            telemetry_id,
            queue_wait_ms=queue_wait_ms,
            execute_ms=execute_ms,
            queue_wait_attr="embedding_queue_wait_ms",
            execute_attr="embedding_execute_ms",
        )

    def _record_timing(
        self,
        telemetry_id: str,
        *,
        queue_wait_ms: float,
        execute_ms: float,
        queue_wait_attr: str,
        execute_attr: str,
    ) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            setattr(
                state, queue_wait_attr, getattr(state, queue_wait_attr) + max(queue_wait_ms, 0.0)
            )
            setattr(state, execute_attr, getattr(state, execute_attr) + max(execute_ms, 0.0))

    def get_embedding_context_count(self, telemetry_id: str) -> int:
        """Return contexts successfully indexed for one request."""
        if not telemetry_id:
            return 0
        with self._lock:
            state = self._states.get(telemetry_id)
            return state.embedding_context_count if state is not None else 0

    def get_queue_timing(self, telemetry_id: str) -> Dict[str, Dict[str, float]]:
        if not telemetry_id:
            return {
                "semantic": {"queue_wait_ms": 0.0, "execute_ms": 0.0},
                "embedding": {"queue_wait_ms": 0.0, "execute_ms": 0.0},
            }
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return {
                    "semantic": {"queue_wait_ms": 0.0, "execute_ms": 0.0},
                    "embedding": {"queue_wait_ms": 0.0, "execute_ms": 0.0},
                }
            return {
                "semantic": {
                    "queue_wait_ms": state.semantic_queue_wait_ms,
                    "execute_ms": state.semantic_execute_ms,
                },
                "embedding": {
                    "queue_wait_ms": state.embedding_queue_wait_ms,
                    "execute_ms": state.embedding_execute_ms,
                },
            }

    def mark_semantic_done(
        self,
        telemetry_id: str,
        root_id: str,
        processed_delta: int = 1,
    ) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.pending_semantic_roots.discard(root_id)
            state.semantic_processed += max(processed_delta, 0)
            self._release_retained_root(telemetry_id, root_id, state)

    def record_semantic_requeue(self, telemetry_id: str, delta: int = 1) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.semantic_requeue_count += max(delta, 0)

    def mark_semantic_failed(self, telemetry_id: str, root_id: str, message: str) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.pending_semantic_roots.discard(root_id)
            state.semantic_error_count += 1
            if message:
                state.semantic_errors.append(message)
            self._release_retained_root(telemetry_id, root_id, state)

    def mark_embedding_done(
        self,
        telemetry_id: str,
        root_id: str,
        processed_delta: int = 1,
        *,
        vector_written: bool = False,
    ) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            was_pending = root_id in state.pending_embedding_roots
            state.pending_embedding_roots.discard(root_id)
            state.embedding_processed += max(processed_delta, 0)
            if vector_written and was_pending:
                state.embedding_context_count += 1

    def mark_embedding_failed(self, telemetry_id: str, root_id: str, message: str) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.pending_embedding_roots.discard(root_id)
            state.embedding_error_count += 1
            if message:
                state.embedding_errors.append(message)

    def is_complete(self, telemetry_id: str) -> bool:
        if not telemetry_id:
            return True
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return True
            return not state.pending_semantic_roots and not state.pending_embedding_roots

    async def wait_for_request(
        self,
        telemetry_id: str,
        timeout: Optional[float] = None,
        poll_interval: float = 0.05,
    ) -> None:
        if not telemetry_id:
            return
        with pause_task_processing():
            start = time.time()
            while True:
                if self.is_complete(telemetry_id):
                    return
                if timeout is not None and (time.time() - start) > timeout:
                    raise TimeoutError(f"Request processing not complete after {timeout}s")
                await asyncio.sleep(poll_interval)

    async def wait_for_embeddings(
        self,
        telemetry_id: str,
        poll_interval: float = 0.05,
        *,
        stop_waiting: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Drain embeddings while the producing semantic root remains pending."""
        if not telemetry_id:
            return
        with pause_task_processing():
            while True:
                with self._lock:
                    state = self._states.get(telemetry_id)
                    if state is None or not state.pending_embedding_roots:
                        return
                if stop_waiting is not None and stop_waiting():
                    # Shutdown has drained the embedding consumer's active writes.
                    # Leave this semantic delivery unacked for recovery rather than
                    # waiting forever for embeddings still in the persistent queue.
                    raise asyncio.CancelledError("Embedding worker stopped with queued work")
                await asyncio.sleep(poll_interval)

    def build_queue_status(self, telemetry_id: str) -> Dict[str, Dict[str, object]]:
        with self._lock:
            state = self._states.get(telemetry_id) or _RequestWaitState()
            return {
                "Semantic": {
                    "processed": state.semantic_processed,
                    "requeue_count": state.semantic_requeue_count,
                    "error_count": state.semantic_error_count,
                    "errors": [{"message": msg} for msg in state.semantic_errors],
                },
                "Embedding": {
                    "processed": state.embedding_processed,
                    "requeue_count": state.embedding_requeue_count,
                    "error_count": state.embedding_error_count,
                    "errors": [{"message": msg} for msg in state.embedding_errors],
                },
            }

    def cleanup(self, telemetry_id: str) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is not None and state.retained_roots:
                state.cleanup_requested = True
            else:
                self._states.pop(telemetry_id, None)


def get_request_wait_tracker() -> RequestWaitTracker:
    return RequestWaitTracker.get_instance()


__all__ = ["RequestWaitTracker", "get_request_wait_tracker"]
