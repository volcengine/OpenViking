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
import threading
import time
from typing import Dict, Optional

from openviking.service.coordinator import get_coordinator
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

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
_TOUCH_REFRESH_DIVISOR = 4.0
_COORDINATOR_RETRY_BASE_SEC = 0.25
_COORDINATOR_RETRY_MAX_SEC = 2.0
_COMPLETION_STATUS_GRACE_SEC = 2.0



class RequestWaitTracker:
    """Track request-scoped queue completion using telemetry_id."""

    _instance: Optional["RequestWaitTracker"] = None
    _initialized: bool = False

    def __new__(cls) -> "RequestWaitTracker":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # Singleton: state is held by the Coordinator, not on the instance.
        if self._initialized:
            return
        self._touch_lock = threading.Lock()
        self._next_touch_deadline: Dict[str, float] = {}
        self._initialized = True

    @classmethod
    def get_instance(cls) -> "RequestWaitTracker":
        return cls()

    def _is_registered(self, telemetry_id: str) -> bool:
        return get_coordinator().scard(_key(telemetry_id, _REG)) > 0

    @staticmethod
    def _deadline_at(timeout: Optional[float]) -> Optional[float]:
        if timeout is None:
            return None
        return time.monotonic() + max(float(timeout), 0.0)

    @staticmethod
    def _is_deadline_exceeded(deadline_at: Optional[float]) -> bool:
        return deadline_at is not None and time.monotonic() >= deadline_at

    @staticmethod
    async def _sleep_with_deadline(delay_sec: float, deadline_at: Optional[float]) -> bool:
        if deadline_at is not None:
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                return False
            delay_sec = min(delay_sec, remaining)
        await asyncio.sleep(max(delay_sec, 0.0))
        return True

    @staticmethod
    def _retry_delay(retry_count: int) -> float:
        exponent = max(retry_count - 1, 0)
        return min(_COORDINATOR_RETRY_BASE_SEC * (2**exponent), _COORDINATOR_RETRY_MAX_SEC)

    @staticmethod
    def _timeout_message(timeout: Optional[float], *, awaiting_status: bool) -> str:
        if awaiting_status:
            return f"Request processing completed but queue status was unavailable after {timeout}s"
        return f"Request processing not complete after {timeout}s"

    def _clear_touch_state(self, telemetry_id: str) -> None:
        with self._touch_lock:
            self._next_touch_deadline.pop(telemetry_id, None)

    def _touch_reg(self, telemetry_id: str) -> None:
        """Refresh TTL on all keys for this request so long-running tasks don't expire."""
        coord = get_coordinator()
        if not coord.is_distributed or coord.default_ttl_sec <= 0:
            return

        now = time.monotonic()
        refresh_interval = max(coord.default_ttl_sec / _TOUCH_REFRESH_DIVISOR, 0.1)
        with self._touch_lock:
            next_due = self._next_touch_deadline.get(telemetry_id, 0.0)
            if next_due > now:
                return
            self._next_touch_deadline[telemetry_id] = now + refresh_interval

        try:
            for suffix in _ALL_SUFFIXES:
                coord.expire(_key(telemetry_id, suffix), coord.default_ttl_sec)
        except Exception:
            # Allow the next caller to retry immediately instead of being gated
            # by the local throttle window after a failed refresh attempt.
            self._clear_touch_state(telemetry_id)
            raise

    def _build_queue_status_once(self, telemetry_id: str) -> Dict[str, Dict[str, object]]:
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
            self._clear_touch_state(telemetry_id)
            return True
        complete = (
            coord.scard(_key(telemetry_id, _PENDING_SEM)) == 0
            and coord.scard(_key(telemetry_id, _PENDING_EMB)) == 0
        )
        if complete:
            self._clear_touch_state(telemetry_id)
        return complete

    async def wait_for_request(
        self,
        telemetry_id: str,
        timeout: Optional[float] = None,
        poll_interval: float = 0.05,
    ) -> Optional[Dict[str, Dict[str, object]]]:
        if not telemetry_id:
            return None
        processing_deadline_at = self._deadline_at(timeout)
        status_deadline_at: Optional[float] = None
        awaiting_status = False
        coordinator_retry_count = 0
        while True:
            try:
                if awaiting_status:
                    return self._build_queue_status_once(telemetry_id)

                complete = self.is_complete(telemetry_id)
                if complete:
                    coordinator_retry_count = 0
                    awaiting_status = True
                    if processing_deadline_at is not None:
                        completion_status_grace_deadline_at = self._deadline_at(
                            _COMPLETION_STATUS_GRACE_SEC
                        )
                        status_deadline_at = max(
                            processing_deadline_at,
                            completion_status_grace_deadline_at,
                        )
                    continue
                self._touch_reg(telemetry_id)
                coordinator_retry_count = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                coordinator_retry_count += 1
                active_deadline_at = status_deadline_at if awaiting_status else processing_deadline_at
                if self._is_deadline_exceeded(active_deadline_at):
                    raise TimeoutError(
                        self._timeout_message(timeout, awaiting_status=awaiting_status)
                    ) from exc
                delay_sec = self._retry_delay(coordinator_retry_count)
                logger.warning(
                    "Coordinator error while waiting for request %s: %s; retrying in %.2fs",
                    telemetry_id,
                    exc,
                    delay_sec,
                )
                if not await self._sleep_with_deadline(delay_sec, active_deadline_at):
                    raise TimeoutError(
                        self._timeout_message(timeout, awaiting_status=awaiting_status)
                    ) from exc
                continue

            if self._is_deadline_exceeded(processing_deadline_at):
                raise TimeoutError(
                    self._timeout_message(timeout, awaiting_status=awaiting_status)
                )
            if not await self._sleep_with_deadline(poll_interval, processing_deadline_at):
                raise TimeoutError(
                    self._timeout_message(timeout, awaiting_status=awaiting_status)
                )

    def build_queue_status(self, telemetry_id: str) -> Dict[str, Dict[str, object]]:
        return self._build_queue_status_once(telemetry_id)

    def cleanup(self, telemetry_id: str) -> None:
        if not telemetry_id:
            return
        self._clear_touch_state(telemetry_id)
        coord = get_coordinator()
        try:
            coord.delete(*(_key(telemetry_id, suffix) for suffix in _ALL_SUFFIXES))
        except Exception as exc:
            if coord.is_distributed:
                logger.warning(
                    "Failed to cleanup request wait tracker state for %s: %s; "
                    "leaving keys to TTL-based cleanup",
                    telemetry_id,
                    exc,
                )
                return
            raise


def get_request_wait_tracker() -> RequestWaitTracker:
    return RequestWaitTracker.get_instance()


__all__ = ["RequestWaitTracker", "get_request_wait_tracker"]
