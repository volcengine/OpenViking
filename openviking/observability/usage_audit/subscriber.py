# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Event bus subscriber for Usage/Audit persistence."""

from __future__ import annotations

from openviking.observability.events import ObservabilityEvent

from .worker import UsageAuditWorker


class UsageAuditSubscriber:
    """Small sync adapter from event bus fan-out to the async worker queue."""

    def __init__(self, worker: UsageAuditWorker) -> None:
        self._worker = worker

    def __call__(self, event: ObservabilityEvent) -> None:
        self._worker.enqueue(event)
