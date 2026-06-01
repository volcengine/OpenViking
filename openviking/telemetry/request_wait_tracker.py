# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-scoped wait tracker for write APIs.

State lives in the cross-instance Coordinator so that load-balanced server
instances observe the same per-request completion status. With the default
in-process Coordinator the behaviour is identical to the prior singleton dict;
with the Redis backend the same telemetry_id is consistent across instances.

Per telemetry_id the tracker keeps:
  * two sets  -> pending semantic / embedding roots (completion gate)
  * six ints  -> processed / requeue / error counters per stage
  * two lists -> error messages per stage
All map onto Coordinator primitives, so updates are atomic per key.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional

from openviking.service.coordinator import get_coordinator

_PREFIX = "rwt:"


def _key(telemetry_id: str, suffix: str) -> str:
    return f"{_PREFIX}{telemetry_id}:{suffix}"


# Per-request key suffixes.
_REG = "reg"  # registration marker (set; idempotent via sadd)
_PENDING_SEM = "pending_sem"
_PENDING_EMB = "pending_emb"
_SEM_PROCESSED = "sem_processed"
_SEM_REQUEUE = "sem_requeue"
_SEM_ERROR_COUNT = "sem_error_count"
_SEM_ERRORS = "sem_errors"
_EMB_PROCESSED = "emb_processed"
_EMB_REQUEUE = "emb_requeue"
_EMB_ERROR_COUNT = "emb_error_count"
_EMB_ERRORS = "emb_errors"

_ALL_SUFFIXES = (
    _REG,
    _PENDING_SEM,
    _PENDING_EMB,
    _SEM_PROCESSED,
    _SEM_REQUEUE,
    _SEM_ERROR_COUNT,
    _SEM_ERRORS,
    _EMB_PROCESSED,
    _EMB_REQUEUE,
    _EMB_ERROR_COUNT,
    _EMB_ERRORS,
)



class RequestWaitTracker:
    """Track request-scoped queue completion using telemetry_id."""

    _instance: Optional["RequestWaitTracker"] = None

    def __new__(cls) -> "RequestWaitTracker":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # Singleton: state is held by the Coordinator, not on the instance.
        pass

    @classmethod
    def get_instance(cls) -> "RequestWaitTracker":
        return cls()

    def _is_registered(self, telemetry_id: str) -> bool:
        return get_coordinator().scard(_key(telemetry_id, _REG)) > 0

    def _touch_reg(self, telemetry_id: str) -> None:
        """Refresh TTL on all keys for this request so long-running tasks don't expire."""
        coord = get_coordinator()
        if coord.is_distributed and coord.default_ttl_sec > 0:
            for suffix in _ALL_SUFFIXES:
                coord.expire(_key(telemetry_id, suffix), coord.default_ttl_sec)

    def register_request(self, telemetry_id: str) -> None:
        if not telemetry_id:
            return
        get_coordinator().sadd(_key(telemetry_id, _REG), "1")

    def register_semantic_root(self, telemetry_id: str, semantic_msg_id: str) -> None:
        if not telemetry_id or not semantic_msg_id:
            return
        if not self._is_registered(telemetry_id):
            return
        get_coordinator().sadd(_key(telemetry_id, _PENDING_SEM), semantic_msg_id)
        self._touch_reg(telemetry_id)

    def register_embedding_root(self, telemetry_id: str, root_id: str) -> None:
        if not telemetry_id or not root_id:
            return
        if not self._is_registered(telemetry_id):
            return
        get_coordinator().sadd(_key(telemetry_id, _PENDING_EMB), root_id)
        self._touch_reg(telemetry_id)

    def record_embedding_processed(self, telemetry_id: str, delta: int = 1) -> None:
        if not telemetry_id or not self._is_registered(telemetry_id):
            return
        if delta > 0:
            get_coordinator().incr(_key(telemetry_id, _EMB_PROCESSED), delta)
            self._touch_reg(telemetry_id)

    def record_embedding_requeue(self, telemetry_id: str, delta: int = 1) -> None:
        if not telemetry_id or not self._is_registered(telemetry_id):
            return
        if delta > 0:
            get_coordinator().incr(_key(telemetry_id, _EMB_REQUEUE), delta)
            self._touch_reg(telemetry_id)

    def record_embedding_error(self, telemetry_id: str, message: str) -> None:
        if not telemetry_id or not self._is_registered(telemetry_id):
            return
        coord = get_coordinator()
        coord.incr(_key(telemetry_id, _EMB_ERROR_COUNT))
        if message:
            coord.rpush(_key(telemetry_id, _EMB_ERRORS), message)
        self._touch_reg(telemetry_id)

    def mark_semantic_done(
        self,
        telemetry_id: str,
        semantic_msg_id: str,
        processed_delta: int = 1,
    ) -> None:
        if not telemetry_id or not self._is_registered(telemetry_id):
            return
        coord = get_coordinator()
        coord.srem(_key(telemetry_id, _PENDING_SEM), semantic_msg_id)
        if processed_delta > 0:
            coord.incr(_key(telemetry_id, _SEM_PROCESSED), processed_delta)
        self._touch_reg(telemetry_id)

    def record_semantic_requeue(self, telemetry_id: str, delta: int = 1) -> None:
        if not telemetry_id or not self._is_registered(telemetry_id):
            return
        if delta > 0:
            get_coordinator().incr(_key(telemetry_id, _SEM_REQUEUE), delta)
            self._touch_reg(telemetry_id)

    def mark_semantic_failed(self, telemetry_id: str, semantic_msg_id: str, message: str) -> None:
        if not telemetry_id or not self._is_registered(telemetry_id):
            return
        coord = get_coordinator()
        coord.srem(_key(telemetry_id, _PENDING_SEM), semantic_msg_id)
        coord.incr(_key(telemetry_id, _SEM_ERROR_COUNT))
        if message:
            coord.rpush(_key(telemetry_id, _SEM_ERRORS), message)
        self._touch_reg(telemetry_id)

    def mark_embedding_done(
        self,
        telemetry_id: str,
        root_id: str,
        processed_delta: int = 1,
    ) -> None:
        if not telemetry_id or not self._is_registered(telemetry_id):
            return
        coord = get_coordinator()
        coord.srem(_key(telemetry_id, _PENDING_EMB), root_id)
        if processed_delta > 0:
            coord.incr(_key(telemetry_id, _EMB_PROCESSED), processed_delta)
        self._touch_reg(telemetry_id)

    def mark_embedding_failed(self, telemetry_id: str, root_id: str, message: str) -> None:
        if not telemetry_id or not self._is_registered(telemetry_id):
            return
        coord = get_coordinator()
        coord.srem(_key(telemetry_id, _PENDING_EMB), root_id)
        coord.incr(_key(telemetry_id, _EMB_ERROR_COUNT))
        if message:
            coord.rpush(_key(telemetry_id, _EMB_ERRORS), message)
        self._touch_reg(telemetry_id)

    def is_complete(self, telemetry_id: str) -> bool:
        if not telemetry_id:
            return True
        coord = get_coordinator()
        if not self._is_registered(telemetry_id):
            return True
        return (
            coord.scard(_key(telemetry_id, _PENDING_SEM)) == 0
            and coord.scard(_key(telemetry_id, _PENDING_EMB)) == 0
        )

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
            self._touch_reg(telemetry_id)
            await asyncio.sleep(poll_interval)

    def build_queue_status(self, telemetry_id: str) -> Dict[str, Dict[str, object]]:
        coord = get_coordinator()
        return {
            "Semantic": {
                "processed": coord.get_int(_key(telemetry_id, _SEM_PROCESSED)),
                "requeue_count": coord.get_int(_key(telemetry_id, _SEM_REQUEUE)),
                "error_count": coord.get_int(_key(telemetry_id, _SEM_ERROR_COUNT)),
                "errors": [
                    {"message": msg} for msg in coord.lrange(_key(telemetry_id, _SEM_ERRORS))
                ],
            },
            "Embedding": {
                "processed": coord.get_int(_key(telemetry_id, _EMB_PROCESSED)),
                "requeue_count": coord.get_int(_key(telemetry_id, _EMB_REQUEUE)),
                "error_count": coord.get_int(_key(telemetry_id, _EMB_ERROR_COUNT)),
                "errors": [
                    {"message": msg} for msg in coord.lrange(_key(telemetry_id, _EMB_ERRORS))
                ],
            },
        }

    def cleanup(self, telemetry_id: str) -> None:
        if not telemetry_id:
            return
        get_coordinator().delete(*(_key(telemetry_id, suffix) for suffix in _ALL_SUFFIXES))


def get_request_wait_tracker() -> RequestWaitTracker:
    return RequestWaitTracker.get_instance()


__all__ = ["RequestWaitTracker", "get_request_wait_tracker"]
