# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Delivery-local requeue state; the count is durable in replacement payloads."""

import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

REQUEUE_COUNT_FIELD = "_queue_requeue_count"


class RequeueLimitExceeded(RuntimeError):
    """No replacement was written because this delivery exhausted its budget."""

    def __init__(self, queue: str, limit: int):
        super().__init__(f"Queue {queue} requeue limit reached: {limit}")


class RequeueEnqueueError(RuntimeError):
    """A replacement was not written; the original must remain unacknowledged."""


@dataclass
class QueueDelivery:
    path: str
    count: int
    replacement_started: bool = False
    replacement_committed: bool = False


@dataclass
class Replacement:
    path: str
    count: int
    committed: bool = False


current_delivery: ContextVar[QueueDelivery | None] = ContextVar("queue_delivery", default=None)
current_replacement: ContextVar[Replacement | None] = ContextVar("queue_replacement", default=None)


def requeue_count(message: Any) -> int:
    """Read the total count, including pre-upgrade handoff retry history."""
    if hasattr(message, "to_dict"):
        message = message.to_dict()
    if isinstance(message, dict):
        message = message.get("data", message)
    if isinstance(message, str):
        try:
            message = json.loads(message)
        except ValueError:
            return 0
    if not isinstance(message, dict):
        return 0
    value = message.get(REQUEUE_COUNT_FIELD, message.get("lock_handoff_retry", 0))
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
