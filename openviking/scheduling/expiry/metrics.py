# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Low-cardinality metrics and structured timer logging."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from openviking.observability.log_safety import short_hash

from .keys import decode_item_id
from .models import Claim

logger = logging.getLogger(__name__)


class ExpiryMetrics:
    def __init__(self, sink: Callable[[str, float, Mapping[str, str]], None] | None = None) -> None:
        self._sink = sink
        self._sink_failures = 0

    def _emit(self, name: str, value: float, labels: Mapping[str, str]) -> None:
        if self._sink is None:
            return
        try:
            self._sink(name, value, labels)
        except Exception as exc:
            self._sink_failures += 1
            (logger.error if self._sink_failures == 1 else logger.info)(
                "expiry metric sink failed total=%d error_type=%s",
                self._sink_failures,
                type(exc).__name__,
                extra={
                    "degraded": True,
                    "failed": self._sink_failures,
                    "error_type": type(exc).__name__,
                },
            )

    def count(self, name: str, value: int = 1, *, item_type: str = "", outcome: str = "") -> None:
        self._emit(name, float(value), {"item_type": item_type, "outcome": outcome})

    def observe(self, name: str, value: float, *, item_type: str = "", outcome: str = "") -> None:
        self._emit(name, value, {"item_type": item_type, "outcome": outcome})

    def gauge(self, name: str, value: float, *, item_type: str = "") -> None:
        self._emit(name, value, {"item_type": item_type})

    def log_invalid_source_items(
        self,
        *,
        shard_id: int,
        minute_ms: int,
        reason: str,
        count: int,
    ) -> None:
        logger.info(
            "expiry discarded invalid source items shard_id=%d minute_ms=%d reason=%s count=%d",
            shard_id,
            minute_ms,
            reason,
            count,
            extra={"degraded": True, "skipped": count, "error_code": reason},
        )

    def log_exhausted(
        self,
        claim: Claim,
        *,
        max_attempts: int,
        reason: str,
    ) -> None:
        identity = decode_item_id(claim.item_id)
        logger.error(
            "expiry callback exhausted item_type=%s account_id=%s user_id=%s "
            "local_id_hash=%s shard_id=%d minute_ms=%d claim_score=%d max_attempts=%d "
            "reason=%s",
            identity.item_type,
            identity.account_id,
            identity.user_id,
            short_hash(identity.local_id),
            claim.bucket.shard_id,
            claim.bucket.minute_ms,
            claim.claim_score,
            max_attempts,
            reason,
            extra={
                "error_code": reason,
                "retry_limit": max_attempts,
                "target_id": short_hash(identity.local_id),
            },
        )
