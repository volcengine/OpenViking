# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-scoped wait tracker for write APIs."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class _RequestWaitState:
    pending_semantic_roots: Set[str] = field(default_factory=set)
    pending_embedding_roots: Set[str] = field(default_factory=set)
    semantic_processed: int = 0
    semantic_requeue_count: int = 0
    semantic_error_count: int = 0
    semantic_errors: List[str] = field(default_factory=list)
    embedding_processed: int = 0
    embedding_requeue_count: int = 0
    embedding_error_count: int = 0
    embedding_errors: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


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

    def register_semantic_root(self, telemetry_id: str, semantic_msg_id: str) -> None:
        if not telemetry_id or not semantic_msg_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.pending_semantic_roots.add(semantic_msg_id)

    def register_embedding_root(self, telemetry_id: str, root_id: str) -> None:
        if not telemetry_id or not root_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.pending_embedding_roots.add(root_id)

    def record_embedding_processed(self, telemetry_id: str, delta: int = 1) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.embedding_processed += max(delta, 0)

    def record_embedding_requeue(self, telemetry_id: str, delta: int = 1) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.embedding_requeue_count += max(delta, 0)

    def record_embedding_error(self, telemetry_id: str, message: str) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.embedding_error_count += 1
            if message:
                state.embedding_errors.append(message)

    def mark_semantic_done(
        self,
        telemetry_id: str,
        semantic_msg_id: str,
        processed_delta: int = 1,
    ) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.pending_semantic_roots.discard(semantic_msg_id)
            state.semantic_processed += max(processed_delta, 0)

    def record_semantic_requeue(self, telemetry_id: str, delta: int = 1) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.semantic_requeue_count += max(delta, 0)

    def mark_semantic_failed(self, telemetry_id: str, semantic_msg_id: str, message: str) -> None:
        if not telemetry_id:
            return
        with self._lock:
            state = self._states.get(telemetry_id)
            if state is None:
                return
            state.pending_semantic_roots.discard(semantic_msg_id)
            state.semantic_error_count += 1
            if message:
                state.semantic_errors.append(message)

    def mark_embedding_done(
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
            state.pending_embedding_roots.discard(root_id)
            state.embedding_processed += max(processed_delta, 0)

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
        start = time.time()
        while True:
            if self.is_complete(telemetry_id):
                return
            if timeout is not None and (time.time() - start) > timeout:
                raise TimeoutError(f"Request processing not complete after {timeout}s")
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
            self._states.pop(telemetry_id, None)


def get_request_wait_tracker() -> RequestWaitTracker:
    return RequestWaitTracker.get_instance()


__all__ = ["RequestWaitTracker", "get_request_wait_tracker"]
