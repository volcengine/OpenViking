# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from .base import EventMetricDataSource


class SessionLifecycleDataSource(EventMetricDataSource):
    """
    Emit session lifecycle events consumed by `SessionCollector`.

    The datasource keeps session instrumentation at the business boundary by expressing lifecycle
    changes as normalized event payloads instead of direct registry writes.
    """

    @staticmethod
    def record_lifecycle(*, action: str, status: str) -> None:
        """
        Emit the outcome of a session lifecycle action such as create, get, delete, or commit.

        The payload is expected to describe the final outcome of the action rather than
        intermediate progress within the session workflow.
        """
        EventMetricDataSource._emit(
            "session.lifecycle",
            {"action": str(action), "status": str(status)},
        )

    @staticmethod
    def record_contexts_used(*, action: str, delta: int = 1) -> None:
        """
        Emit the number of additional contexts consumed by a session-level action.

        Non-positive deltas are ignored so callers can pass computed increments without needing a
        separate guard.
        """
        if delta <= 0:
            return
        EventMetricDataSource._emit(
            "session.contexts_used",
            {"action": str(action), "delta": int(delta)},
        )

    @staticmethod
    def record_archive(*, status: str) -> None:
        """Emit the normalized outcome of one session archive attempt."""
        EventMetricDataSource._emit(
            "session.archive",
            {"status": str(status)},
        )
