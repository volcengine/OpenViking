# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from .base import EventMetricDataSource, StateMetricDataSource


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

    @staticmethod
    def record_fencing(
        *, operation: str, outcome: str, latency_seconds: float
    ) -> None:
        """Emit one low-cardinality fenced-session write outcome."""
        EventMetricDataSource._emit(
            "session.fencing",
            {
                "operation": str(operation),
                "outcome": str(outcome),
                "latency_seconds": max(0.0, float(latency_seconds)),
            },
        )

    @staticmethod
    def record_fenced_effect(*, operation: str, outcome: str) -> None:
        """Emit one bounded v2 durable-writer attempt outcome."""
        EventMetricDataSource._emit(
            "session.fenced_effect",
            {"operation": str(operation), "outcome": str(outcome)},
        )


class FencedBacklogDataSource(StateMetricDataSource):
    """Read the bounded PostgreSQL outbox snapshot used by scrape metrics."""

    def read_backlog(self) -> dict:
        from openviking.server.fenced_postgres import (
            _connect,
            fencing_database_url,
            fencing_service_token,
        )
        from openviking.server.routers.fenced_sessions import (
            fenced_writer_runtime_status,
        )

        runtime = fenced_writer_runtime_status()
        result = {
            "writer_healthy": bool(runtime["healthy"]),
            "effect_concurrency": int(runtime["effect_concurrency"]),
            "commit_concurrency": int(runtime["commit_concurrency"]),
            "effect": {
                state: {"items": 0, "oldest_age_seconds": 0.0}
                for state in ("queued", "running")
            },
            "commit": {
                state: {"items": 0, "oldest_age_seconds": 0.0}
                for state in ("pending", "running", "ambiguous")
            },
        }
        database_url = fencing_database_url()
        service_token = fencing_service_token()
        if not database_url and not service_token:
            return result
        if not database_url or not service_token:
            raise RuntimeError("partial Alice fencing configuration")

        conn = _connect(application_name="openviking-fenced-metrics")
        try:
            conn.autocommit = True
            with conn.cursor() as cursor:
                # A scrape must never hold a database connection behind slow
                # application work.  CollectorManager also adds a 1s outer
                # timeout and a 1s TTL/SWR cache.
                cursor.execute("SET statement_timeout = 500")
                cursor.execute(
                    """
                    SELECT o.state, count(*),
                           COALESCE(
                             EXTRACT(EPOCH FROM (now() - min(r.submitted_at))),
                             0
                           )
                    FROM openviking_fencing.effect_outbox o
                    JOIN openviking_fencing.operation_receipt r
                      USING (account_id,user_id,writer,session_scope_id,operation_id)
                    WHERE o.state IN ('queued','running')
                    GROUP BY o.state
                    """
                )
                for state, count, oldest in cursor.fetchall():
                    normalized = str(state)
                    if normalized in result["effect"]:
                        result["effect"][normalized] = {
                            "items": int(count),
                            "oldest_age_seconds": max(0.0, float(oldest)),
                        }
                cursor.execute(
                    """
                    SELECT state, count(*),
                           COALESCE(
                             EXTRACT(EPOCH FROM (now() - min(created_at))),
                             0
                           )
                    FROM openviking_fencing.commit_work_outbox
                    WHERE state IN ('pending','running','ambiguous')
                    GROUP BY state
                    """
                )
                for state, count, oldest in cursor.fetchall():
                    normalized = str(state)
                    if normalized in result["commit"]:
                        result["commit"][normalized] = {
                            "items": int(count),
                            "oldest_age_seconds": max(0.0, float(oldest)),
                        }
        finally:
            conn.close()
        return result
